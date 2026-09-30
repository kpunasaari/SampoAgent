import re
import stat
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from sampoagent.careers.recommendations import recommend_occupations
from sampoagent.careers.taxonomy_import import import_esco_package
from sampoagent.db.repository import Repository


def _package(path: Path) -> Path:
    path.mkdir()
    (path / "occupations_fi.csv").write_text(
        "conceptUri,conceptType,iscoGroup,preferredLabel,definition\n"
        "https://data.europa.eu/esco/occupation/cleaner,OC,9112,Siivooja,"
        "Puhdistaa tiloja\n",
        encoding="utf-8",
    )
    (path / "occupations_en.csv").write_text(
        "conceptUri,conceptType,iscoGroup,preferredLabel,definition\n"
        "https://data.europa.eu/esco/occupation/cleaner,OC,9112,Cleaner,Cleans spaces\n",
        encoding="utf-8",
    )
    (path / "skills_fi.csv").write_text(
        "conceptUri,conceptType,preferredLabel,definition\n"
        "https://data.europa.eu/esco/skill/cleaning,SK,siivous,\n"
        "https://data.europa.eu/esco/skill/machine,SK,lattianhoitokoneen käyttö,\n",
        encoding="utf-8",
    )
    (path / "skills_en.csv").write_text(
        "conceptUri,conceptType,preferredLabel,definition\n"
        "https://data.europa.eu/esco/skill/cleaning,SK,cleaning,\n"
        "https://data.europa.eu/esco/skill/machine,SK,operate floor-care machine,\n",
        encoding="utf-8",
    )
    (path / "occupationSkillRelations_nl.csv").write_text(
        "occupationUri,relationType,skillType,skillUri\n"
        "https://data.europa.eu/esco/occupation/cleaner,essential,skill/competence,https://data.europa.eu/esco/skill/cleaning\n"
        "https://data.europa.eu/esco/occupation/cleaner,optional,skill/competence,https://data.europa.eu/esco/skill/machine\n",
        encoding="utf-8",
    )
    return path


def _esco_zip(package: Path, *, additional_entries: dict[str, bytes] | None = None) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(package.rglob("*.csv")):
            archive.write(path, path.relative_to(package).as_posix())
        for name, payload in (additional_entries or {}).items():
            archive.writestr(name, payload)
    return output.getvalue()


def test_imports_multilingual_esco_occupations_skills_and_attribution(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "taxonomy.db")
    repository.initialize()
    package = _package(tmp_path / "esco")

    summary = import_esco_package(repository, package, version="1.2.1", languages=("fi", "en"))

    assert (summary.occupations, summary.skills, summary.relationships) == (1, 2, 2)
    occupation = repository.esco_occupations()[0]
    assert occupation.labels == {"en": "Cleaner", "fi": "Siivooja"}
    assert {item.importance for item in occupation.skills} == {"ESSENTIAL", "OPTIONAL"}
    metadata = repository.esco_taxonomy_metadata()
    assert metadata["version"] == "1.2.1"
    assert metadata["languages"] == "en,fi"
    assert metadata["attribution"] == "This service uses the ESCO classification of the European Commission."
    assert metadata["modified"] == "SampoAgent builds a local multilingual matching index from the imported ESCO files."
    assert len(metadata["source_sha256"]) == 64


def test_careers_upload_imports_downloaded_zip_locally_with_csrf_and_keeps_targets_inactive(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    package = _package(tmp_path / "esco-package")
    app = create_app(database_path=tmp_path / "careers-upload.db", storage_dir=tmp_path / "storage")
    client = TestClient(app)
    page = client.get("/careers")
    assert "SampoAgent does not request it for you" in page.text
    assert "name='file' type='file'" in page.text
    csrf = re.search(r"name='csrf_token' value='([a-f0-9]+)'", page.text)
    assert csrf

    response = client.post(
        "/careers/esco/import",
        data={"version": "1.2.1", "languages": "fi,en", "csrf_token": csrf.group(1)},
        files={"file": ("esco.zip", _esco_zip(package), "application/zip")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/careers?notice=")
    assert app.state.repository.esco_taxonomy_metadata()["version"] == "1.2.1"
    assert len(app.state.repository.esco_occupations()) == 1
    assert app.state.repository.target_occupations() == []
    assert not list((tmp_path / "storage").rglob("*.csv"))


def test_esco_zip_upload_requires_the_local_form_token(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    package = _package(tmp_path / "esco-package")
    app = create_app(database_path=tmp_path / "careers-csrf.db")
    client = TestClient(app)
    client.get("/careers")

    response = client.post(
        "/careers/esco/import",
        data={"version": "1.2.1", "languages": "fi,en", "csrf_token": "invalid"},
        files={"file": ("esco.zip", _esco_zip(package), "application/zip")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert parse_qs(urlsplit(response.headers["location"]).query)["notice"] == [
        "Refresh the Career Suggestions page before importing an ESCO package."
    ]
    assert app.state.repository.esco_taxonomy_metadata() is None


def test_esco_zip_import_rejects_traversal_without_replacing_previous_index(tmp_path: Path, monkeypatch) -> None:
    import sampoagent.careers.taxonomy_import as taxonomy_import

    repository = Repository(tmp_path / "zip-safety.db")
    repository.initialize()
    good_package = _package(tmp_path / "existing-esco")
    import_esco_package(repository, good_package, version="1.2.1", languages=("fi", "en"))
    before = repository.esco_occupations()
    dangerous = _package(tmp_path / "dangerous-esco")
    payload = _esco_zip(dangerous, additional_entries={"../../outside.csv": b"candidate data must never be extracted"})
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    temp_roots = []
    original_temporary_directory = taxonomy_import.tempfile.TemporaryDirectory

    def tracked_temporary_directory(*args, **kwargs):
        kwargs["dir"] = scratch
        temporary = original_temporary_directory(*args, **kwargs)
        temp_roots.append(Path(temporary.name))
        return temporary

    monkeypatch.setattr(taxonomy_import.tempfile, "TemporaryDirectory", tracked_temporary_directory)

    with pytest.raises(ValueError, match="unsafe archive path"):
        taxonomy_import.import_esco_archive(repository, BytesIO(payload), version="1.2.2", languages=("fi", "en"))

    assert repository.esco_occupations() == before
    assert repository.esco_taxonomy_metadata()["version"] == "1.2.1"
    assert not (tmp_path / "outside.csv").exists()
    assert len(temp_roots) == 1
    assert not temp_roots[0].exists()


def test_esco_zip_import_merges_duplicate_language_relation_files_and_cleans_temporary_files(tmp_path: Path, monkeypatch) -> None:
    import sampoagent.careers.taxonomy_import as taxonomy_import

    repository = Repository(tmp_path / "zip-relations.db")
    repository.initialize()
    package = _package(tmp_path / "esco-relations")
    relation_bytes = (package / "occupationSkillRelations_nl.csv").read_bytes()
    payload = _esco_zip(package, additional_entries={"fi/occupationSkillRelations_fi.csv": relation_bytes})
    temp_roots = []
    original_temporary_directory = taxonomy_import.tempfile.TemporaryDirectory

    def tracked_temporary_directory(*args, **kwargs):
        temporary = original_temporary_directory(*args, **kwargs)
        temp_roots.append(Path(temporary.name))
        return temporary

    monkeypatch.setattr(taxonomy_import.tempfile, "TemporaryDirectory", tracked_temporary_directory)

    summary = taxonomy_import.import_esco_archive(
        repository, BytesIO(payload), version="1.2.1", languages=("fi", "en")
    )

    assert summary.relationships == 2
    assert {item.importance for item in repository.esco_occupations()[0].skills} == {"ESSENTIAL", "OPTIONAL"}
    assert len(temp_roots) == 1
    assert not temp_roots[0].exists()


def test_esco_zip_import_rejects_symbolic_links_and_too_many_members(tmp_path: Path) -> None:
    import sampoagent.careers.taxonomy_import as taxonomy_import

    repository = Repository(tmp_path / "zip-member-limits.db")
    repository.initialize()
    package = _package(tmp_path / "zip-member-package")
    symlink_archive = BytesIO()
    with ZipFile(symlink_archive, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(package.rglob("*.csv")):
            archive.write(path, path.relative_to(package).as_posix())
        link = ZipInfo("README.md")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "../../outside.csv")

    with pytest.raises(ValueError, match="symbolic links"):
        taxonomy_import.import_esco_archive(
            repository, BytesIO(symlink_archive.getvalue()), version="1.2.1", languages=("fi", "en")
        )

    oversized_member_list = _esco_zip(
        package,
        additional_entries={f"unused-{index}.txt": b"" for index in range(512)},
    )
    with pytest.raises(ValueError, match="too many archive entries"):
        taxonomy_import.import_esco_archive(
            repository, BytesIO(oversized_member_list), version="1.2.1", languages=("fi", "en")
        )
    assert repository.esco_taxonomy_metadata() is None


def test_esco_zip_import_rejects_crc_corruption(tmp_path: Path) -> None:
    import sampoagent.careers.taxonomy_import as taxonomy_import

    repository = Repository(tmp_path / "zip-crc.db")
    repository.initialize()
    package = _package(tmp_path / "zip-crc-package")
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        for path in sorted(package.rglob("*.csv")):
            archive.write(path, path.relative_to(package).as_posix())
    payload = bytearray(output.getvalue())
    label_offset = payload.find(b"Siivooja")
    assert label_offset >= 0
    payload[label_offset] ^= 1

    with pytest.raises(ValueError, match="corrupt or cannot be read"):
        taxonomy_import.import_esco_archive(
            repository, BytesIO(payload), version="1.2.1", languages=("fi", "en")
        )
    assert repository.esco_taxonomy_metadata() is None


def test_esco_zip_upload_keeps_current_index_when_archive_is_corrupt(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    package = _package(tmp_path / "existing-esco")
    app = create_app(database_path=tmp_path / "careers-corrupt.zip.db")
    repository = app.state.repository
    import_esco_package(repository, package, version="1.2.1", languages=("fi", "en"))
    previous_occupation = repository.esco_occupations()
    client = TestClient(app)
    page = client.get("/careers")
    csrf = re.search(r"name='csrf_token' value='([a-f0-9]+)'", page.text)
    assert csrf

    response = client.post(
        "/careers/esco/import",
        data={"version": "1.2.2", "languages": "fi,en", "csrf_token": csrf.group(1)},
        files={"file": ("esco.zip", b"not a ZIP file", "application/zip")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert parse_qs(urlsplit(response.headers["location"]).query)["notice"] == [
        "Could not import this ESCO ZIP. The previous catalogue was kept; check its version, selected languages, and CSV files."
    ]
    assert repository.esco_taxonomy_metadata()["version"] == "1.2.1"
    assert repository.esco_occupations() == previous_occupation


def test_esco_upload_rejects_oversized_request_body_before_form_parsing(tmp_path: Path, monkeypatch) -> None:
    import sampoagent.app.main as app_main
    from fastapi.testclient import TestClient

    monkeypatch.setattr(app_main, "MAX_ESCO_ARCHIVE_BYTES", 0)
    app = app_main.create_app(database_path=tmp_path / "careers-oversized.db")
    client = TestClient(app)
    page = client.get("/careers")
    csrf = re.search(r"name='csrf_token' value='([a-f0-9]+)'", page.text)
    assert csrf

    response = client.post(
        "/careers/esco/import",
        data={"version": "1.2.1", "languages": "fi,en", "csrf_token": csrf.group(1)},
        files={"file": ("esco.zip", b"x" * (64 * 1024 + 1024), "application/zip")},
        follow_redirects=False,
    )

    assert response.status_code == 413
    assert app.state.repository.esco_taxonomy_metadata() is None

    boundary = "sampoagent-test-boundary"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"version\"\r\n\r\n1.2.1\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"languages\"\r\n\r\nfi,en\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"csrf_token\"\r\n\r\n{csrf.group(1)}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"esco.zip\"\r\n"
        "Content-Type: application/zip\r\n\r\n"
    ).encode() + b"x" * (64 * 1024 + 1024) + f"\r\n--{boundary}--\r\n".encode()

    chunked_response = client.post(
        "/careers/esco/import",
        content=(body[index:index + 4096] for index in range(0, len(body), 4096)),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        follow_redirects=False,
    )

    assert chunked_response.status_code == 413, chunked_response.text
    assert app.state.repository.esco_taxonomy_metadata() is None


def test_imported_taxonomy_recommends_from_linked_skills_and_explains_essential_gaps(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "recommend.db")
    repository.initialize()
    import_esco_package(repository, _package(tmp_path / "esco"), version="1.2.1", languages=("fi", "en"))

    recommendations = recommend_occupations(["cleaning"], ignored=[], taxonomy=repository.esco_occupations())

    assert len(recommendations) == 1
    assert recommendations[0].title_en == "Cleaner"
    assert recommendations[0].title_fi == "Siivooja"
    assert recommendations[0].supporting_facts == ["cleaning"]
    assert recommendations[0].missing_requirements == []
    assert recommendations[0].auto_target is False


def test_missing_essential_skill_is_shown_without_calling_it_a_legal_qualification(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "gaps.db")
    repository.initialize()
    import_esco_package(repository, _package(tmp_path / "esco"), version="1.2.1", languages=("fi", "en"))

    recommendations = recommend_occupations(["operate floor-care machine"], ignored=[], taxonomy=repository.esco_occupations())

    assert recommendations[0].supporting_facts == ["operate floor-care machine"]
    assert "cleaning" in recommendations[0].missing_requirements


def test_invalid_or_incomplete_package_does_not_replace_a_previous_import(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "atomic.db")
    repository.initialize()
    good = _package(tmp_path / "good")
    import_esco_package(repository, good, version="1.2.1", languages=("fi", "en"))
    before = repository.esco_occupations()
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "occupations_fi.csv").write_text("conceptUri,conceptType,preferredLabel\nx,OC,Työ\n", encoding="utf-8")

    with pytest.raises(ValueError, match="missing.*skills.*fi"):
        import_esco_package(repository, bad, version="1.2.2", languages=("fi",))

    assert repository.esco_occupations() == before
    assert repository.esco_taxonomy_metadata()["version"] == "1.2.1"


def test_import_rejects_invalid_version_and_non_occupation_or_group_concepts(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "validate.db")
    repository.initialize()
    package = _package(tmp_path / "esco")
    with (package / "occupations_fi.csv").open("a", encoding="utf-8") as stream:
        stream.write("https://data.europa.eu/esco/occupation/group,OG,91,Cleaning occupations,\n")

    with pytest.raises(ValueError, match="version"):
        import_esco_package(repository, package, version="latest;DROP TABLE", languages=("fi", "en"))

    summary = import_esco_package(repository, package, version="1.2.1", languages=("fi", "en"))
    assert summary.occupations == 1


def test_careers_page_uses_imported_catalog_and_displays_required_attribution(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    database = tmp_path / "careers-ui.db"
    repository = Repository(database)
    repository.initialize()
    repository.add_confirmed_fact(fact_type="skill", value="cleaning")
    import_esco_package(repository, _package(tmp_path / "esco"), version="1.2.1", languages=("fi", "en"))

    response = TestClient(create_app(database_path=database)).get("/careers")

    assert response.status_code == 200
    assert "This service uses the ESCO classification of the European Commission." in response.text
    assert "European Commission ESCO reuse conditions; CC BY 4.0 applies where indicated in the source dataset." in response.text
    assert "ESCO 1.2.1" in response.text
    assert "Essential skill links" in response.text
    assert "Cleaner" in response.text and "Siivooja" in response.text
    assert repository.target_occupations() == []


def test_import_esco_cli_indexes_the_user_selected_language_files(tmp_path: Path, monkeypatch, capsys) -> None:
    import sys

    from sampoagent.cli import main

    package = _package(tmp_path / "esco")
    database = tmp_path / "cli.db"
    monkeypatch.setattr(sys, "argv", [
        "sampoagent", "import-esco", str(package), "--database", str(database),
        "--version", "1.2.1", "--languages", "fi", "en",
    ])

    main()

    assert "Imported ESCO 1.2.1 (en, fi): 1 occupations, 2 skills, 2 relationships" in capsys.readouterr().out
    indexed = Repository(database)
    indexed.initialize()
    assert indexed.esco_taxonomy_metadata()["languages"] == "en,fi"
