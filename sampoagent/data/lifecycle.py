"""Explicit, local-only backup, restore, erasure, and process fencing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import tempfile
from typing import BinaryIO, Iterable
import unicodedata
import zipfile

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

BACKUP_FORMAT = "sampoagent-local-backup"
BACKUP_VERSION = 1
ENCRYPTED_BACKUP_MAGIC = b"SAMPOAGENT-ENCRYPTED-BACKUP\x01"
BACKUP_PASSPHRASE_MIN_LENGTH = 12
BACKUP_PASSPHRASE_MAX_LENGTH = 1024
RESTORE_CONFIRMATION = "RESTORE LOCAL SAMPOAGENT DATA"
ERASE_CONFIRMATION = "ERASE ALL LOCAL SAMPOAGENT DATA"
BACKUP_DIRECTORIES = ("uploads", "generated", "archive", "applications")
ERASABLE_DIRECTORIES = (*BACKUP_DIRECTORIES, "browser-profile")
MAX_ARCHIVE_BYTES = 1_073_741_824
MAX_UNCOMPRESSED_BYTES = 2_147_483_648
MAX_FILE_BYTES = 536_870_912
MAX_ARCHIVE_FILES = 20_000
_WINDOWS_RESERVED_NAMES = re.compile(
    r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.IGNORECASE
)
_REQUIRED_TABLES = {"candidate_profile", "settings", "jobs", "applications", "answer_bank"}


@dataclass(frozen=True)
class VerifiedBackup:
    manifest: dict[str, object]
    files: dict[str, bytes]


def encrypt_backup_archive(payload: bytes, passphrase: str) -> bytes:
    """Encrypt a verified ZIP envelope with a user-held passphrase."""
    if not BACKUP_PASSPHRASE_MIN_LENGTH <= len(passphrase) <= BACKUP_PASSPHRASE_MAX_LENGTH:
        raise ValueError(
            f"Backup passphrase must contain {BACKUP_PASSPHRASE_MIN_LENGTH} to {BACKUP_PASSPHRASE_MAX_LENGTH} characters"
        )
    verify_backup_archive(payload)
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode("utf-8"))
    encrypted = AESGCM(key).encrypt(nonce, payload, ENCRYPTED_BACKUP_MAGIC)
    return ENCRYPTED_BACKUP_MAGIC + salt + nonce + encrypted


def decrypt_backup_archive(envelope: bytes, passphrase: str) -> bytes:
    """Authenticate and decrypt an export before verifying its ZIP manifest."""
    header_size = len(ENCRYPTED_BACKUP_MAGIC) + 16 + 12 + 16
    if not BACKUP_PASSPHRASE_MIN_LENGTH <= len(passphrase) <= BACKUP_PASSPHRASE_MAX_LENGTH:
        raise ValueError("Backup passphrase length is outside the supported range")
    if len(envelope) > MAX_ARCHIVE_BYTES + 128 or len(envelope) < header_size or not envelope.startswith(ENCRYPTED_BACKUP_MAGIC):
        raise ValueError("Encrypted SampoAgent backup is malformed or unsupported")
    offset = len(ENCRYPTED_BACKUP_MAGIC)
    salt = envelope[offset : offset + 16]
    nonce = envelope[offset + 16 : offset + 28]
    ciphertext = envelope[offset + 28 :]
    try:
        key = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode("utf-8"))
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, ENCRYPTED_BACKUP_MAGIC)
    except (InvalidTag, UnicodeEncodeError, ValueError) as error:
        raise ValueError("Backup passphrase is incorrect or the encrypted file was changed") from error
    verify_backup_archive(plaintext)
    return plaintext


def _resolved_database_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_symlink():
        raise ValueError("Database path must not be a symbolic link")
    if path.exists() and path.is_dir():
        raise ValueError("Database path must be a file, not a directory")
    resolved = path.resolve(strict=False)
    if not resolved.name or resolved == Path(resolved.anchor):
        raise ValueError("Database path is not a safe file path")
    return resolved


def _resolved_storage_root(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_symlink():
        raise ValueError("Storage root must not be a symbolic link")
    if path.exists() and not path.is_dir():
        raise ValueError("Storage root must be a directory")
    resolved = path.resolve(strict=False)
    home = Path.home().resolve(strict=False)
    working = Path.cwd().resolve(strict=False)
    if resolved == Path(resolved.anchor) or resolved in {home, working} or working.is_relative_to(resolved):
        raise ValueError("Refusing to use a filesystem root, home directory, or workspace as application storage")
    return resolved


def _validate_database_location(database: Path, storage: Path) -> None:
    if database == storage or database.is_relative_to(storage):
        raise ValueError("Database file must be outside the managed storage directory")


def database_runtime_lock_path(database_path: str | Path) -> Path:
    """Return the stable lock path for operations that only touch SQLite."""
    database = _resolved_database_path(database_path)
    return database.with_name(database.name + ".sampoagent.lock")


def runtime_lock_paths(database_path: str | Path, storage_root: str | Path) -> tuple[Path, Path]:
    """Return stable sibling lock paths shared by server, worker, restore, and erase."""
    database = _resolved_database_path(database_path)
    storage = _resolved_storage_root(storage_root)
    _validate_database_location(database, storage)
    return (
        database_runtime_lock_path(database),
        storage.parent / f".{storage.name}.sampoagent.lock",
    )


class LocalRuntimeLock:
    """Cross-process exclusive locks; lock files intentionally persist after release."""

    def __init__(self, paths: Iterable[str | Path]) -> None:
        self.paths = tuple(sorted({Path(path).expanduser().absolute() for path in paths}, key=str))
        self._handles: list[BinaryIO] = []

    def __enter__(self) -> "LocalRuntimeLock":
        try:
            for path in self.paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.is_symlink():
                    raise RuntimeError("Local runtime lock path must not be a symbolic link")
                handle = path.open("a+b")
                try:
                    handle.seek(0, os.SEEK_END)
                    if handle.tell() == 0:
                        handle.write(b"\0")
                        handle.flush()
                    handle.seek(0)
                    try:
                        if os.name == "nt":
                            import msvcrt

                            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl

                            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except (OSError, BlockingIOError) as error:
                        raise RuntimeError(
                            "SampoAgent is already running or another data operation holds the local lock"
                        ) from error
                except BaseException:
                    handle.close()
                    raise
                self._handles.append(handle)
        except BaseException:
            self._release()
            raise
        return self

    def _release(self) -> None:
        for handle in reversed(self._handles):
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            finally:
                handle.close()
        self._handles.clear()

    def __exit__(self, *_: object) -> None:
        self._release()


def _safe_archive_path(name: str) -> None:
    if not name or "\\" in name or "\x00" in name or name.startswith("/") or ":" in name:
        raise ValueError("Backup path is unsafe")
    if name != PurePosixPath(name).as_posix():
        raise ValueError("Backup path is not canonical")
    parts = PurePosixPath(name).parts
    if len(name) > 4096 or any(len(part) > 255 for part in parts):
        raise ValueError("Backup path is too long")
    if not parts or any(part in {"", ".", ".."} or part.endswith((".", " ")) for part in parts):
        raise ValueError("Backup path is unsafe")
    if any(_WINDOWS_RESERVED_NAMES.match(part) for part in parts):
        raise ValueError("Backup path is unsafe")
    if name == "database.sqlite3":
        return
    if len(parts) < 3 or parts[0] != "storage" or parts[1] not in BACKUP_DIRECTORIES:
        raise ValueError("Backup path is outside the allowed data set")


def _validate_sqlite_database(data: bytes) -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(data)
        connection.execute("PRAGMA trusted_schema = OFF")
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise ValueError("Backup SQLite integrity check failed")
        objects = connection.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall()
        tables = {str(name) for kind, name, _ in objects if kind == "table"}
        if not _REQUIRED_TABLES.issubset(tables):
            raise ValueError("Backup does not contain a SampoAgent database")
        if any(kind not in {"table", "index"} or (sql and "VIRTUAL TABLE" in str(sql).upper()) for kind, _, sql in objects):
            raise ValueError("Backup database contains unsupported schema objects")
    except sqlite3.Error as error:
        raise ValueError("Backup SQLite database is invalid") from error
    finally:
        connection.close()


def _read_storage_files(storage: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    if storage.is_symlink():
        raise ValueError("Storage root must not be a symbolic link")
    for directory in BACKUP_DIRECTORIES:
        root = storage / directory
        if root.is_symlink():
            raise ValueError("Managed storage directories must not be symbolic links")
        if not root.exists():
            continue
        if not root.is_dir():
            raise ValueError("Managed storage path is not a directory")
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise ValueError("Backup will not follow symbolic links")
            if path.is_dir():
                continue
            if not path.is_file():
                raise ValueError("Backup encountered an unsupported filesystem entry")
            relative = path.relative_to(storage).as_posix()
            archive_name = f"storage/{relative}"
            _safe_archive_path(archive_name)
            data = path.read_bytes()
            if len(data) > MAX_FILE_BYTES:
                raise ValueError("A managed file exceeds the local backup size limit")
            files[archive_name] = data
            if len(files) > MAX_ARCHIVE_FILES:
                raise ValueError("Local backup contains too many files")
    return files


def create_backup_archive(repository: object, storage_root: str | Path) -> bytes:
    """Build a consistent SQLite snapshot plus only allowlisted SampoAgent files."""
    storage = _resolved_storage_root(storage_root)
    source = getattr(repository, "connection", None)
    if not isinstance(source, sqlite3.Connection):
        raise ValueError("An open SampoAgent SQLite repository is required")
    snapshot = sqlite3.connect(":memory:")
    try:
        source.backup(snapshot)
        database = snapshot.serialize()
    finally:
        snapshot.close()
    _validate_sqlite_database(database)
    files = {"database.sqlite3": database, **_read_storage_files(storage)}
    if sum(map(len, files.values())) > MAX_UNCOMPRESSED_BYTES:
        raise ValueError("Local backup exceeds the maximum uncompressed size")
    records = [
        {"path": name, "size": len(data), "sha256": sha256(data).hexdigest()}
        for name, data in sorted(files.items())
    ]
    manifest = {
        "format": BACKUP_FORMAT,
        "version": BACKUP_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": records,
    }
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        for name, data in sorted(files.items()):
            archive.writestr(name, data)
    result = output.getvalue()
    if len(result) > MAX_ARCHIVE_BYTES:
        raise ValueError("Local backup exceeds the maximum archive size")
    return result


def verify_backup_archive(payload: bytes) -> VerifiedBackup:
    if not isinstance(payload, bytes) or len(payload) > MAX_ARCHIVE_BYTES:
        raise ValueError("Backup archive is invalid or exceeds the size limit")
    try:
        with zipfile.ZipFile(BytesIO(payload), "r") as archive:
            infos = archive.infolist()
            if not infos or len(infos) > MAX_ARCHIVE_FILES + 1:
                raise ValueError("Backup archive contains an invalid number of files")
            names = [item.filename for item in infos]
            if len(names) != len(set(names)) or names.count("manifest.json") != 1:
                raise ValueError("Backup archive has duplicate or missing manifest paths")
            normalized_names = [unicodedata.normalize("NFC", name).casefold() for name in names]
            if len(normalized_names) != len(set(normalized_names)):
                raise ValueError("Backup archive has colliding file names")
            total_size = 0
            for item in infos:
                if item.is_dir() or item.flag_bits & 0x1:
                    raise ValueError("Backup archive contains unsupported entries")
                mode = item.external_attr >> 16
                if mode and stat.S_ISLNK(mode):
                    raise ValueError("Backup archive contains a symbolic link")
                if item.filename != "manifest.json":
                    _safe_archive_path(item.filename)
                elif item.file_size > 4_194_304:
                    raise ValueError("Backup manifest exceeds the size limit")
                if item.file_size > MAX_FILE_BYTES and item.filename != "database.sqlite3":
                    raise ValueError("A backup file exceeds the size limit")
                if item.file_size > MAX_UNCOMPRESSED_BYTES:
                    raise ValueError("Backup archive exceeds the uncompressed size limit")
                total_size += item.file_size
            if total_size > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("Backup archive exceeds the uncompressed size limit")
            manifest = json.loads(archive.read("manifest.json"))
            if not isinstance(manifest, dict) or manifest.get("format") != BACKUP_FORMAT or manifest.get("version") != BACKUP_VERSION:
                raise ValueError("Backup format or version is unsupported")
            records = manifest.get("files")
            if not isinstance(records, list) or not records:
                raise ValueError("Backup manifest has no file inventory")
            files: dict[str, bytes] = {}
            listed: set[str] = set()
            info_by_name = {item.filename: item for item in infos if item.filename != "manifest.json"}
            if set(info_by_name) != {str(record.get("path", "")) for record in records if isinstance(record, dict)}:
                raise ValueError("Backup manifest inventory does not match archive contents")
            for record in records:
                if not isinstance(record, dict):
                    raise ValueError("Backup manifest contains an invalid file record")
                name = record.get("path")
                if not isinstance(name, str):
                    raise ValueError("Backup manifest contains an invalid path")
                _safe_archive_path(name)
                if name in listed:
                    raise ValueError("Backup manifest contains duplicate file paths")
                listed.add(name)
                data = archive.read(name)
                if record.get("size") != len(data) or record.get("sha256") != sha256(data).hexdigest():
                    raise ValueError("Backup file checksum or size verification failed")
                files[name] = data
            if "database.sqlite3" not in files:
                raise ValueError("Backup is missing its SQLite database")
            _validate_sqlite_database(files["database.sqlite3"])
            return VerifiedBackup(manifest=manifest, files=files)
    except ValueError:
        raise
    except (zipfile.BadZipFile, OSError, RuntimeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError("Backup archive is corrupt or malformed") from error


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def restore_backup_archive(
    payload: bytes,
    database_path: str | Path,
    storage_root: str | Path,
    *,
    confirmation: str,
) -> None:
    """Restore a verified archive; caller must hold the runtime lock and stop SampoAgent."""
    if confirmation != RESTORE_CONFIRMATION:
        raise ValueError("Explicit restore confirmation is required")
    verified = verify_backup_archive(payload)
    database = _resolved_database_path(database_path)
    storage = _resolved_storage_root(storage_root)
    _validate_database_location(database, storage)
    if database.exists() and database.is_dir():
        raise ValueError("Database path must not be a directory")
    database.parent.mkdir(parents=True, exist_ok=True)
    storage.parent.mkdir(parents=True, exist_ok=True)
    if storage.exists() and (storage.is_symlink() or not storage.is_dir()):
        raise ValueError("Storage root must be a real directory")

    db_stage = Path(tempfile.mkdtemp(prefix=".sampoagent-restore-db-", dir=database.parent))
    storage_stage = Path(tempfile.mkdtemp(prefix=".sampoagent-restore-storage-", dir=storage.parent))
    db_file = db_stage / "database.sqlite3"
    staged_directories: dict[str, Path] = {}
    try:
        db_file.write_bytes(verified.files["database.sqlite3"])
        for directory in BACKUP_DIRECTORIES:
            staged = storage_stage / directory
            staged.mkdir()
            prefix = f"storage/{directory}/"
            for name, data in verified.files.items():
                if name.startswith(prefix):
                    relative = PurePosixPath(name).parts[2:]
                    target = staged.joinpath(*relative)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
            staged_directories[directory] = staged
    except BaseException:
        shutil.rmtree(db_stage, ignore_errors=True)
        shutil.rmtree(storage_stage, ignore_errors=True)
        raise

    displaced: list[tuple[Path, Path]] = []
    installed: list[Path] = []
    try:
        for suffix in ("", "-wal", "-shm"):
            target = Path(str(database) + suffix)
            if target.exists() or target.is_symlink():
                backup = db_stage / ("previous-db" + suffix.replace("-", "_"))
                os.replace(target, backup)
                displaced.append((target, backup))
        if storage.is_symlink():
            raise ValueError("Storage root must not be a symbolic link")
        storage.mkdir(parents=True, exist_ok=True)
        for directory in BACKUP_DIRECTORIES:
            target = storage / directory
            if target.exists() or target.is_symlink():
                backup = storage_stage / ("previous-" + directory)
                os.replace(target, backup)
                displaced.append((target, backup))
        os.replace(db_file, database)
        installed.append(database)
        for directory in BACKUP_DIRECTORIES:
            target = storage / directory
            os.replace(staged_directories[directory], target)
            installed.append(target)
    except BaseException as error:
        rollback_errors: list[str] = []
        for path in reversed(installed):
            try:
                _remove_path(path)
            except OSError as rollback_error:
                rollback_errors.append(str(rollback_error))
        for target, backup in reversed(displaced):
            try:
                if backup.exists() or backup.is_symlink():
                    os.replace(backup, target)
            except OSError as rollback_error:
                rollback_errors.append(str(rollback_error))
        if rollback_errors:
            raise RuntimeError(
                f"Restore failed and rollback was incomplete; recovery files remain in {db_stage} and {storage_stage}"
            ) from error
        shutil.rmtree(db_stage, ignore_errors=True)
        shutil.rmtree(storage_stage, ignore_errors=True)
        raise RuntimeError("Restore failed; the previous local data was restored") from error
    shutil.rmtree(db_stage, ignore_errors=True)
    shutil.rmtree(storage_stage, ignore_errors=True)


def erase_local_data(
    database_path: str | Path,
    storage_root: str | Path,
    *,
    confirmation: str,
) -> None:
    """Delete the selected local database and managed data directories only."""
    if confirmation != ERASE_CONFIRMATION:
        raise ValueError("Exact deletion confirmation is required")
    database = _resolved_database_path(database_path)
    storage = _resolved_storage_root(storage_root)
    _validate_database_location(database, storage)
    for suffix in ("", "-wal", "-shm"):
        _remove_path(Path(str(database) + suffix))
    for directory in ERASABLE_DIRECTORIES:
        _remove_path(storage / directory)
