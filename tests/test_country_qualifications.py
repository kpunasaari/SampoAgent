from sampoagent.careers.recommendations import recommend_occupations
from sampoagent.careers.taxonomy import TaxonomyOccupation, TaxonomyOccupationSkill
from sampoagent.country_packs.finland.qualifications import advisories_for_occupation


def _occupation(title_en: str, title_fi: str) -> TaxonomyOccupation:
    return TaxonomyOccupation(
        uri="urn:test:occupation",
        labels={"en": title_en, "fi": title_fi},
        descriptions={},
        isco_code="",
        skills=(TaxonomyOccupationSkill("urn:test:skill", "ESSENTIAL", {"en": "patient care"}),),
    )


def test_finland_health_role_gets_a_sourced_authority_check_not_a_legal_gap():
    recommendation = recommend_occupations(
        ["patient care"], ignored=[], taxonomy=[_occupation("Registered Nurse", "Sairaanhoitaja")], country_code="FI"
    )[0]

    assert len(recommendation.qualification_advisories) == 1
    advisory = recommendation.qualification_advisories[0]
    assert advisory.code == "fi-health-social-practice-right"
    assert "not a finding that you are unqualified" in advisory.message
    assert advisory.source_url.startswith("https://www.suomi.fi/")
    assert advisory.is_legal_hard_gap is False
    assert recommendation.auto_target is False


def test_finnish_ecec_and_private_security_titles_have_distinct_primary_sources():
    ecec = advisories_for_occupation("Early Childhood Education and Care Teacher", "Varhaiskasvatuksen opettaja", country_code="FI")
    security = advisories_for_occupation("Security Guard", "Vartija", country_code="FI")

    assert ecec[0].code == "fi-ecec-eligibility"
    assert "oph.fi" in ecec[0].source_url
    assert security[0].code == "fi-private-security-approval"
    assert "poliisi.fi" in security[0].source_url
    assert all(not item.is_legal_hard_gap for item in (*ecec, *security))


def test_no_finland_legal_advisory_without_an_explicit_matching_country_or_for_generic_cleaning():
    assert advisories_for_occupation("Registered Nurse", "Sairaanhoitaja", country_code="SE") == ()
    assert advisories_for_occupation("Cleaner", "Siivooja", country_code="FI") == ()
