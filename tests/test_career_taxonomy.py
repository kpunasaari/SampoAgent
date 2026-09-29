from pathlib import Path

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
