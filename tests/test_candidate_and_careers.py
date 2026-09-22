from pathlib import Path


def test_cv_text_extraction_creates_unconfirmed_provenanced_facts(tmp_path: Path) -> None:
    from sampoagent.candidate.service import ingest_text_cv

    result = ingest_text_cv(
        "Aino Example\naino@example.test\nSkills: forklift operation, customer service\nLanguages: Finnish, English",
        source_id="cv-1",
        storage_dir=tmp_path,
    )

    assert result.warning is None
    assert {fact.value for fact in result.facts} >= {"forklift operation", "customer service"}
    assert all(fact.provenance == "CV_EXTRACTED" for fact in result.facts)
    assert all(fact.confirmed is False for fact in result.facts)


def test_confirmed_skill_recommends_related_roles_without_auto_targeting() -> None:
    from sampoagent.careers.recommendations import recommend_occupations

    recommendations = recommend_occupations(["forklift operation"], ignored=[])

    roles = {recommendation.title_en: recommendation for recommendation in recommendations}
    assert "Warehouse Worker" in roles
    assert roles["Warehouse Worker"].auto_target is False
    assert "forklift operation" in roles["Warehouse Worker"].supporting_facts


def test_cv_file_text_reader_supports_txt_and_rejects_unknown_extensions(tmp_path: Path) -> None:
    from sampoagent.candidate.service import read_cv_file

    txt = tmp_path / "candidate.txt"
    txt.write_text("Skills: cleaning", encoding="utf-8")
    assert "cleaning" in read_cv_file(txt)
    unknown = tmp_path / "candidate.exe"
    unknown.write_bytes(b"not executable")
    try:
        read_cv_file(unknown)
    except ValueError as error:
        assert "Unsupported" in str(error)
    else:
        raise AssertionError("Expected unsupported file type to be rejected")
