def test_job_language_engine_selects_finnish_and_english_independently() -> None:
    from sampoagent.languages.engine import language_engine_for_job

    finnish = language_engine_for_job("Etsimme varastotyöntekijää vuorotyöhön")
    english = language_engine_for_job("We are hiring a warehouse worker for shifts")

    assert finnish.code == "fi"
    assert finnish.cv_heading == "Ansioluettelo"
    assert english.code == "en"
    assert english.cv_heading == "Curriculum Vitae"


def test_finnish_explicit_licence_requirement_is_hard_but_ambiguous_mention_is_not() -> None:
    from sampoagent.country_packs.finland.requirements import parse_requirements

    hard = parse_requirements("B-ajokortti vaaditaan tehtävässä.")
    ambiguous = parse_requirements("B-ajokortti katsotaan eduksi.")

    assert hard.hard_requirements == ["B-ajokortti"]
    assert ambiguous.hard_requirements == []
    assert ambiguous.preferred_requirements == ["B-ajokortti"]
