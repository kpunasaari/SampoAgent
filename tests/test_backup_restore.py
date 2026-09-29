from hashlib import sha256
from io import BytesIO
import sqlite3
from pathlib import Path
import sys
import zipfile

import pytest
from fastapi.testclient import TestClient

from sampoagent.app.main import create_app
from sampoagent.data.lifecycle import (
    ERASE_CONFIRMATION,
    RESTORE_CONFIRMATION,
    LocalRuntimeLock,
    create_backup_archive,
    decrypt_backup_archive,
    encrypt_backup_archive,
    erase_local_data,
    restore_backup_archive,
    runtime_lock_paths,
    verify_backup_archive,
)
from sampoagent.db.repository import Repository


def _seed_local_state(tmp_path: Path, name: str) -> tuple[Repository, Path, Path]:
    database = tmp_path / f"{name}.db"
    storage = tmp_path / f"{name}-storage"
    repository = Repository(database)
    repository.initialize()
    repository.save_profile("Synthetic Candidate", "en", "candidate@example.test")
    repository.save_mailbox_connection("gmail", "encrypted-token-ciphertext")
    for directory, filename, payload in (
        ("uploads", "source-cv.pdf", b"synthetic source CV"),
        ("archive", "tailored-cv.pdf", b"synthetic archived CV"),
        ("generated", "generated-cv.pdf", b"synthetic generated CV"),
        ("applications/1", "application-cv.pdf", b"synthetic application CV"),
        ("browser-profile/Default", "Cookies", b"browser session secret"),
    ):
        folder = storage / directory
        folder.mkdir(parents=True, exist_ok=True)
        (folder / filename).write_bytes(payload)
    uploaded = storage / "uploads" / "source-cv.pdf"
    repository.add_document(kind="uploaded_cv", path=str(uploaded), checksum=sha256(uploaded.read_bytes()).hexdigest())
    repository.archive_cv(
        path=str(uploaded), checksum=sha256(uploaded.read_bytes()).hexdigest(), language="en",
        role_family="warehouse_logistics", source_job_id=None, fit_score=0, strategy="uploaded",
    )
    return repository, database, storage


def test_backup_contains_consistent_local_database_and_allowlisted_cv_files_only(tmp_path):
    repository, _, storage = _seed_local_state(tmp_path, "source")

    archive = create_backup_archive(repository, storage)
    verified = verify_backup_archive(archive)

    assert verified.manifest["format"] == "sampoagent-local-backup"
    assert "database.sqlite3" in verified.files
    assert verified.files["storage/uploads/source-cv.pdf"] == b"synthetic source CV"
    assert verified.files["storage/archive/tailored-cv.pdf"] == b"synthetic archived CV"
    assert verified.files["storage/generated/generated-cv.pdf"] == b"synthetic generated CV"
    assert verified.files["storage/applications/1/application-cv.pdf"] == b"synthetic application CV"
    assert not any("browser-profile" in name for name in verified.files)
    assert b"browser session secret" not in archive
    assert "SAMPOAGENT_TOKEN_ENCRYPTION_KEY" not in verified.manifest
    assert b"encrypted-token-ciphertext" in verified.files["database.sqlite3"]
    repository.connection.close()


def test_export_envelope_is_authenticated_and_requires_a_separate_passphrase(tmp_path):
    repository, _, storage = _seed_local_state(tmp_path, "encrypted-export")
    plaintext = create_backup_archive(repository, storage)

    with pytest.raises(ValueError, match="12 to 1024 characters"):
        encrypt_backup_archive(plaintext, "short")
    encrypted = encrypt_backup_archive(plaintext, "correct horse battery staple")
    assert plaintext not in encrypted
    assert decrypt_backup_archive(encrypted, "correct horse battery staple") == plaintext
    with pytest.raises(ValueError, match="passphrase is incorrect"):
        decrypt_backup_archive(encrypted, "incorrect horse battery staple")
    repository.connection.close()


def test_restore_validates_then_restores_database_and_managed_files_without_replacing_browser_profile(tmp_path):
    source_repository, _, source_storage = _seed_local_state(tmp_path, "source-restore")
    archive = create_backup_archive(source_repository, source_storage)
    target_repository, target_database, target_storage = _seed_local_state(tmp_path, "target-restore")
    target_repository.save_profile("Old Candidate", "fi", "old@example.test")
    (target_storage / "uploads" / "obsolete.pdf").write_bytes(b"old file")
    (target_storage / "browser-profile" / "Default" / "Cookies").write_bytes(b"keep current login")
    target_repository.connection.close()

    restore_backup_archive(archive, target_database, target_storage, confirmation=RESTORE_CONFIRMATION)

    restored = Repository(target_database)
    restored.initialize()
    assert restored.profile()["name"] == "Synthetic Candidate"
    assert restored.mailbox_ciphertext() == "encrypted-token-ciphertext"
    assert (target_storage / "uploads" / "source-cv.pdf").read_bytes() == b"synthetic source CV"
    assert not (target_storage / "uploads" / "obsolete.pdf").exists()
    assert (target_storage / "browser-profile" / "Default" / "Cookies").read_bytes() == b"keep current login"
    source_repository.connection.close()
    restored.connection.close()


def test_tampered_or_path_traversal_archives_are_rejected_before_restore(tmp_path):
    source_repository, _, source_storage = _seed_local_state(tmp_path, "source-invalid")
    archive = create_backup_archive(source_repository, source_storage)
    target_repository, target_database, target_storage = _seed_local_state(tmp_path, "target-invalid")
    original_name = target_repository.profile()["name"]

    with zipfile.ZipFile(BytesIO(archive), "r") as incoming:
        members = {name: incoming.read(name) for name in incoming.namelist()}
    members["storage/uploads/source-cv.pdf"] = b"tampered CV"
    tampered = BytesIO()
    with zipfile.ZipFile(tampered, "w", compression=zipfile.ZIP_DEFLATED) as outgoing:
        for name, content in members.items():
            outgoing.writestr(name, content)
    with pytest.raises(ValueError, match="checksum"):
        restore_backup_archive(tampered.getvalue(), target_database, target_storage, confirmation=RESTORE_CONFIRMATION)
    assert target_repository.profile()["name"] == original_name

    traversal_manifest = b'{"format":"sampoagent-local-backup","version":1,"files":[{"path":"../outside.txt","size":1,"sha256":"' + sha256(b"x").hexdigest().encode() + b'"}]}'
    traversal = BytesIO()
    with zipfile.ZipFile(traversal, "w", compression=zipfile.ZIP_DEFLATED) as outgoing:
        outgoing.writestr("manifest.json", traversal_manifest)
        outgoing.writestr("../outside.txt", b"x")
    with pytest.raises(ValueError, match="path"):
        verify_backup_archive(traversal.getvalue())
    assert target_repository.profile()["name"] == original_name
    source_repository.connection.close()
    target_repository.connection.close()


def test_erase_requires_exact_confirmation_and_only_removes_sampoagent_owned_children(tmp_path):
    repository, database, storage = _seed_local_state(tmp_path, "erase")
    repository.connection.close()
    (storage / "notes.txt").write_text("not an application-managed child", encoding="utf-8")
    outside = tmp_path / "neighbor.txt"
    outside.write_text("preserve", encoding="utf-8")

    with pytest.raises(ValueError, match="confirmation"):
        erase_local_data(database, storage, confirmation="yes")
    assert database.exists()
    assert (storage / "uploads" / "source-cv.pdf").exists()

    erase_local_data(database, storage, confirmation=ERASE_CONFIRMATION)

    assert not database.exists()
    assert not Path(str(database) + "-wal").exists()
    assert not (storage / "uploads").exists()
    assert not (storage / "archive").exists()
    assert not (storage / "generated").exists()
    assert not (storage / "applications").exists()
    assert not (storage / "browser-profile").exists()
    assert (storage / "notes.txt").read_text(encoding="utf-8") == "not an application-managed child"
    assert outside.read_text(encoding="utf-8") == "preserve"


def test_runtime_lock_prevents_restore_or_erasure_while_app_process_is_active(tmp_path):
    repository, database, storage = _seed_local_state(tmp_path, "locked")
    repository.connection.close()
    lock_paths = runtime_lock_paths(database, storage)

    with LocalRuntimeLock(lock_paths):
        with pytest.raises(RuntimeError, match="already running"):
            with LocalRuntimeLock(lock_paths):
                pass

    assert database.exists()


def test_erase_cli_refuses_to_run_while_the_application_storage_is_locked(tmp_path, monkeypatch):
    repository, database, storage = _seed_local_state(tmp_path, "cli-locked")
    repository.connection.close()
    from sampoagent.cli import main

    monkeypatch.setattr(sys, "argv", [
        "sampoagent", "erase-local-data", "--database", str(database), "--storage-dir", str(storage),
        "--confirm", ERASE_CONFIRMATION,
    ])
    with LocalRuntimeLock(runtime_lock_paths(database, storage)):
        with pytest.raises(SystemExit, match="already running"):
            main()
    assert database.exists()
    assert (storage / "uploads" / "source-cv.pdf").exists()


def test_settings_offers_private_backup_and_explicit_offline_restore_and_delete_instructions(tmp_path):
    database = tmp_path / "ui-lifecycle.db"
    storage = tmp_path / "ui-lifecycle-storage"
    client = TestClient(create_app(database_path=database, storage_dir=storage), follow_redirects=False)

    settings = client.get("/settings")
    assert settings.status_code == 200
    assert "Download local backup / data export" in settings.text
    assert "sampoagent restore" in settings.text
    assert ERASE_CONFIRMATION in settings.text
    assert "Backups are not deleted automatically" in settings.text

    mismatch = client.post("/settings/data/backup", data={
        "passphrase": "correct horse battery staple",
        "passphrase_confirmation": "different password phrase",
    })
    assert mismatch.status_code == 400

    backup = client.post("/settings/data/backup", data={
        "passphrase": "correct horse battery staple",
        "passphrase_confirmation": "correct horse battery staple",
    })
    assert backup.status_code == 200
    assert backup.headers["content-type"].startswith("application/vnd.sampoagent.backup")
    assert backup.headers["cache-control"] == "no-store"
    decrypted = decrypt_backup_archive(backup.content, "correct horse battery staple")
    with zipfile.ZipFile(BytesIO(decrypted)) as archive:
        assert "database.sqlite3" in archive.namelist()
        restored = sqlite3.connect(":memory:")
        restored.deserialize(archive.read("database.sqlite3"))
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        restored.close()


def test_backup_restore_and_erase_cli_round_trip_only_selected_local_state(tmp_path, monkeypatch, capsys):
    repository, database, storage = _seed_local_state(tmp_path, "cli-lifecycle")
    repository.connection.close()
    archive_path = tmp_path / "private-backup.sampobak"

    monkeypatch.setattr(sys, "argv", [
        "sampoagent", "backup", "--database", str(database), "--storage-dir", str(storage), "--output", str(archive_path),
    ])
    from sampoagent.cli import main
    import sampoagent.cli as cli_module

    prompts = iter([
        "correct horse battery staple",
        "correct horse battery staple",
        "correct horse battery staple",
    ])
    monkeypatch.setattr(cli_module.getpass, "getpass", lambda _prompt: next(prompts))

    main()
    assert archive_path.is_file()
    assert "Verified local backup" in capsys.readouterr().out
    original_archive = archive_path.read_bytes()
    with pytest.raises(SystemExit, match="already exists"):
        main()
    assert archive_path.read_bytes() == original_archive

    repository = Repository(database)
    repository.save_profile("Changed Candidate", "fi", "changed@example.test")
    repository.connection.close()
    monkeypatch.setattr(sys, "argv", [
        "sampoagent", "restore", "--database", str(database), "--storage-dir", str(storage),
        "--archive", str(archive_path), "--confirm", RESTORE_CONFIRMATION,
    ])
    main()
    restored = Repository(database)
    restored.initialize()
    assert restored.profile()["name"] == "Synthetic Candidate"
    restored.connection.close()

    monkeypatch.setattr(sys, "argv", [
        "sampoagent", "erase-local-data", "--database", str(database), "--storage-dir", str(storage),
        "--confirm", ERASE_CONFIRMATION,
    ])
    main()
    assert not database.exists()
    assert not (storage / "uploads").exists()
    assert not (storage / "browser-profile").exists()
    assert archive_path.exists()
