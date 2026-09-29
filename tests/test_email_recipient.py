import pytest

from sampoagent.integrations.email_recipient import extract_application_email_recipient


@pytest.mark.parametrize("description,language,expected,cue", [
    ("Apply by email to recruitment@northstar.fi.", "en", "recruitment@northstar.fi", "en_apply_by_email"),
    ("Lähetä työhakemuksesi sähköpostitse osoitteeseen rekry@northstar.fi.", "fi", "rekry@northstar.fi", "fi_send_application_email"),
    ("Skicka din ansökan via e-post till jobb@norr.example.", "sv", "jobb@norr.example", "sv_send_application_email"),
    ("Send your application to jobs@northstar.fi", "en", "jobs@northstar.fi", "en_send_application"),
    ("Applications must be submitted by email to jobs@northstar.fi.", "en", "jobs@northstar.fi", "en_application_email_submission"),
    ("The application must be emailed to jobs@northstar.fi.", "en", "jobs@northstar.fi", "en_application_email_submission"),
    ("Hakemuksen voi lähettää sähköpostitse rekry@northstar.fi.", "fi", "rekry@northstar.fi", "fi_application_email_submission"),
    ("Ansökan kan skickas via e-post till jobb@norr.example.", "sv", "jobb@norr.example", "sv_application_email_submission"),
])
def test_extracts_one_address_tied_to_explicit_application_instruction(description, language, expected, cue):
    result = extract_application_email_recipient(description, language=language, verified_snapshot_hash="a" * 64)

    assert result.status == "READY"
    assert result.recipient == expected
    assert result.cue_id == cue
    assert result.verified_snapshot_hash == "a" * 64


@pytest.mark.parametrize("description,language,status", [
    ("Questions? Contact hr@northstar.fi. Apply online using the portal.", "en", "NOT_EMAIL_APPLICATION"),
    ("Apply by email to the recruitment team\nFor questions, contact hr@northstar.fi", "en", "MISSING_RECIPIENT"),
    ("Lähetä hakemus sähköpostitse osoitteeseen rekrytointi\nLisätietoja: hr@northstar.fi", "fi", "MISSING_RECIPIENT"),
    ("Skicka ansökan via e-post till rekryteringen\nFör frågor, kontakta hr@norr.example", "sv", "MISSING_RECIPIENT"),
    ("Apply by email to first@northstar.fi or second@northstar.fi.", "en", "AMBIGUOUS_RECIPIENT"),
    ("Apply by email to first@northstar.fi. Send your application to second@northstar.fi.", "en", "AMBIGUOUS_RECIPIENT"),
    ("Apply by email to hiring@northstar.fi. Include a cover letter and your salary expectation.", "en", "UNSUPPORTED_REQUIREMENTS"),
    ("Apply by email to rekry@northstar.fi. Ilmoita hakemuksessa palkkatoive ja liitä tutkintotodistus.", "fi", "UNSUPPORTED_REQUIREMENTS"),
    ("Ansök via e-post till jobb@norr.example. Bifoga personligt brev och löneanspråk.", "sv", "UNSUPPORTED_REQUIREMENTS"),
    ("Apply by email to the recruitment team.", "en", "MISSING_RECIPIENT"),
    ("Apply by email to not-an-address.", "en", "MISSING_RECIPIENT"),
    ("Apply by email to " + ("details " * 40) + "jobs@northstar.fi.", "en", "MISSING_RECIPIENT"),
])
def test_refuses_unrelated_ambiguous_malformed_or_distant_addresses(description, language, status):
    result = extract_application_email_recipient(description, language=language, verified_snapshot_hash="b" * 64)

    assert result.status == status
    assert result.recipient is None
    assert bool(result.cue_id) == (status != "NOT_EMAIL_APPLICATION")


def test_requires_verified_snapshot_provenance():
    result = extract_application_email_recipient("Apply by email to jobs@northstar.fi", language="en", verified_snapshot_hash="")

    assert result.status == "MISSING_VERIFICATION"
    assert result.recipient is None


@pytest.mark.parametrize("description,language", [
    ("Do not apply by email to jobs@northstar.fi.", "en"),
    ("Do not email your application to jobs@northstar.fi.", "en"),
    ("Älä lähetä hakemustasi sähköpostitse osoitteeseen rekry@northstar.fi.", "fi"),
    ("Skicka inte din ansökan via e-post till jobb@norr.example.", "sv"),
])
def test_explicitly_negated_email_routes_never_become_sendable(description, language):
    result = extract_application_email_recipient(description, language=language, verified_snapshot_hash="c" * 64)

    assert result.status == "CONTRADICTORY_INSTRUCTION"
    assert result.recipient is None


def test_malformed_second_address_makes_context_ambiguous():
    result = extract_application_email_recipient(
        "Apply by email to hiring@northstar.fi or bad@@northstar.fi.",
        language="en", verified_snapshot_hash="d" * 64,
    )

    assert result.status == "AMBIGUOUS_RECIPIENT"
    assert result.recipient is None


def test_unrecognized_likely_email_route_is_held_but_separate_contact_address_is_not_a_route():
    unrecognized = extract_application_email_recipient(
        "For this application, use the hiring email jobs@northstar.fi.",
        language="en", verified_snapshot_hash="e" * 64,
    )
    contact_only = extract_application_email_recipient(
        "Apply online. For questions, email hr@northstar.fi.",
        language="en", verified_snapshot_hash="f" * 64,
    )

    assert unrecognized.status == "UNSUPPORTED_INSTRUCTION"
    assert unrecognized.recipient is None
    assert contact_only.status == "NOT_EMAIL_APPLICATION"
