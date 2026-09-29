from sampoagent.applications.field_resolver import FormField, resolve_application_fields
from sampoagent.candidate.questions import question_id_for_form_label


def test_resolves_contact_fields_only_from_confirmed_candidate_profile():
    fields = [
        FormField("field-0", "Full legal name", True),
        FormField("field-1", "Email address", True, "email"),
        FormField("field-2", "Phone number", False, "tel"),
    ]
    result = resolve_application_fields(fields, profile={"name": "Aino Example", "email": "aino@example.test"}, facts=[], answers=[])
    assert result.ready is True
    assert result.values == {"field-0": "Aino Example", "field-1": "aino@example.test"}
    assert result.skipped == ("field-2",)


def test_required_unknown_field_waits_without_asking_or_inventing():
    result = resolve_application_fields(
        [FormField("field-0", "Do you have a hygieniapassi?", True, "select", ("Yes", "No"))],
        profile={}, facts=[], answers=[],
    )
    assert result.ready is False
    assert result.needs_input == ("field-0",)
    assert result.values == {}


def test_reuses_only_exact_user_confirmed_answer_and_checks_select_options():
    result = resolve_application_fields(
        [FormField("field-0", "What driving licence categories do you hold?", True, "select", ("A", "B"))],
        profile={}, facts=[], answers=[{
            "question_id": "experience:licences",
            "category": "FACT",
            "question": "Driving licence categories, restrictions and validity",
            "value": "B",
            "source": "USER_CONFIRMED",
            "answer_state": "CONFIRMED",
            "scope_type": "GLOBAL",
        }],
    )
    assert result.ready is True
    assert result.values == {"field-0": "B"}
    invalid = resolve_application_fields(
        [FormField("field-0", "What driving licence categories do you hold?", True, "select", ("A", "B"))],
        profile={}, facts=[], answers=[{
            "question_id": "experience:licences",
            "category": "FACT",
            "question": "Driving licence categories, restrictions and validity",
            "value": "B, unrestricted",
            "source": "USER_CONFIRMED",
            "answer_state": "CONFIRMED",
            "scope_type": "GLOBAL",
        }],
    )
    assert invalid.ready is False


def test_conflicting_confirmed_answers_wait_instead_of_silent_choice():
    result = resolve_application_fields(
        [FormField("f", "What is your notice period?", True)],
        profile={}, facts=[], answers=[
            {"category": "FACT", "question": "What is your notice period?", "value": "One month", "source": "USER_CONFIRMED"},
            {"category": "FACT", "question": "What is your notice period?", "value": "Two weeks", "source": "USER_CONFIRMED"},
        ],
    )
    assert result.ready is False
    assert result.conflicts == ("f",)


def test_explicitly_confirmed_onboarding_answers_match_safe_common_form_aliases():
    result = resolve_application_fields(
        [FormField("location", "Preferred work location", True)],
        profile={}, facts=[], answers=[{
            "category": "PREFERENCE",
            "question": "Preferred work locations",
            "value": "Vantaa",
            "source": "USER_CONFIRMED",
        }],
    )
    assert result.ready is True
    assert result.values == {"location": "Vantaa"}


def test_onboarding_draft_or_per_application_prompt_is_not_reused():
    fields = [FormField("motivation", "Why do you want to work for this employer?", True)]
    for source in ("QUESTIONNAIRE_DRAFT", "USER_CONFIRMED"):
        result = resolve_application_fields(
            fields, profile={}, facts=[], answers=[{
                "category": "MOTIVATION",
                "question": "General interests and motivation (tailor for each employer)",
                "value": "I am motivated",
                "source": source,
            }],
        )
        assert result.ready is False


def test_localized_salary_and_start_date_fields_resolve_confirmed_answers():
    cases = (
        ('Mikä on palkkatoiveesi?', 'availability:salary', '3 000 EUR/kk'),
        ('Vilket löneanspråk har du?', 'availability:salary', '3 000 EUR/kk'),
        ('Milloin voit aloittaa aikaisintaan?', 'availability:start_date', '2026-10-01'),
        ('När kan du tidigast börja?', 'availability:start_date', '2026-10-01'),
    )

    for label, question_id, value in cases:
        assert question_id_for_form_label(label) == question_id
        result = resolve_application_fields(
            [FormField('field', label, True)],
            profile={}, facts=[],
            answers=[{
                'question_id': question_id,
                'category': 'PREFERENCE',
                'question': 'confirmed onboarding answer',
                'value': value,
                'source': 'USER_CONFIRMED',
                'scope_type': 'GLOBAL',
                'answer_state': 'CONFIRMED',
            }],
        )
        assert result.ready is True
        assert result.values == {'field': value}


def test_work_permission_aliases_keep_finland_and_sweden_scopes_distinct():
    label = 'Har du rätt att arbeta i Sverige?'
    assert question_id_for_form_label(label) == 'eligibility:work_permission'
    answer = {
        'question_id': 'eligibility:work_permission',
        'category': 'FACT',
        'question': 'Do you currently have permission to work in that country?',
        'value': 'Yes',
        'source': 'USER_CONFIRMED',
        'answer_state': 'CONFIRMED',
        'scope_type': 'COUNTRY',
        'scope_country': 'Sweden',
    }

    sweden = resolve_application_fields(
        [FormField('permit', label, True)], profile={}, facts=[], answers=[answer], country='Sweden',
    )
    finland = resolve_application_fields(
        [FormField('permit', label, True)], profile={}, facts=[], answers=[answer], country='Finland',
    )

    assert sweden.ready is True
    assert sweden.values == {'permit': 'Yes'}
    assert finland.ready is False
    assert finland.needs_input == ('permit',)


def test_explicit_foreign_work_country_in_form_cannot_use_job_country_answer():
    label = 'Do you have the right to work in Sweden?'
    answer = {
        'question_id': 'eligibility:work_permission',
        'category': 'FACT',
        'question': 'Do you currently have permission to work in that country?',
        'value': 'Yes',
        'source': 'USER_CONFIRMED',
        'answer_state': 'CONFIRMED',
        'scope_type': 'COUNTRY',
        'scope_country': 'Finland',
    }

    result = resolve_application_fields(
        [FormField('permit', label, True)], profile={}, facts=[], answers=[answer], country='Finland',
    )

    assert result.ready is False
    assert result.needs_input == ('permit',)


def test_swedish_country_name_in_form_cannot_use_finnish_work_permission_answer():
    result = resolve_application_fields(
        [FormField('permit', 'Har du rätt att arbeta i Sverige?', True)],
        profile={}, facts=[], country='Finland', answers=[{
            'question_id': 'eligibility:work_permission',
            'category': 'FACT',
            'question': 'Do you currently have permission to work in that country?',
            'value': 'Yes',
            'source': 'USER_CONFIRMED',
            'answer_state': 'CONFIRMED',
            'scope_type': 'COUNTRY',
            'scope_country': 'Finland',
        }],
    )

    assert question_id_for_form_label('Har du rätt att arbeta i Sverige?') == 'eligibility:work_permission'
    assert result.ready is False
    assert result.needs_input == ('permit',)


def test_core_application_field_aliases_cover_finnish_swedish_and_english():
    cases = (
        ('What is your expected salary?', 'availability:salary'),
        ('Mikä on palkkatoiveesi?', 'availability:salary'),
        ('Vilket löneanspråk har du?', 'availability:salary'),
        ('What is your notice period?', 'availability:notice_period'),
        ('Mikä on irtisanomisaikasi?', 'availability:notice_period'),
        ('Vilken uppsägningstid har du?', 'availability:notice_period'),
        ('When can you start?', 'availability:start_date'),
        ('Milloin voit aloittaa?', 'availability:start_date'),
        ('När kan du börja?', 'availability:start_date'),
        ('Are you willing to relocate?', 'preferences:relocation'),
        ('Oletko valmis muuttamaan?', 'preferences:relocation'),
        ('Är du villig att flytta?', 'preferences:relocation'),
        ('Which shifts can you work?', 'availability:shifts'),
        ('Mitkä työvuorot sopivat sinulle?', 'availability:shifts'),
        ('Vilka arbetsskift kan du arbeta?', 'availability:shifts'),
        ('What languages do you speak?', 'skills:languages'),
        ('Mitä kieliä puhut?', 'skills:languages'),
        ('Vilka språk talar du?', 'skills:languages'),
        ('What driving licence categories do you hold?', 'experience:licences'),
        ('Mitkä ajokorttiluokat sinulla on?', 'experience:licences'),
        ('Vilka körkortskategorier har du?', 'experience:licences'),
        ('What is your education background?', 'experience:education'),
        ('Mikä on koulutustaustasi?', 'experience:education'),
        ('Vilken utbildningsbakgrund har du?', 'experience:education'),
        ('Describe your employment history', 'experience:employment_history'),
        ('Kuvaile työhistoriaasi', 'experience:employment_history'),
        ('Beskriv din arbetshistorik', 'experience:employment_history'),
        ('Can you provide references?', 'materials:references_available'),
        ('Voitko antaa suosittelijoita?', 'materials:references_available'),
        ('Kan du lämna referenser?', 'materials:references_available'),
    )

    for label, question_id in cases:
        assert question_id_for_form_label(label) == question_id


def test_licence_category_answer_does_not_answer_a_yes_no_licence_question():
    result = resolve_application_fields(
        [FormField('licence', 'Do you have a B driving licence?', True, 'select', ('Yes', 'No'))],
        profile={}, facts=[], answers=[{
            'question_id': 'experience:licences',
            'category': 'FACT',
            'question': 'Driving licence categories, restrictions and validity',
            'value': 'Yes',
            'source': 'USER_CONFIRMED',
            'answer_state': 'CONFIRMED',
            'scope_type': 'GLOBAL',
        }],
    )

    assert result.ready is False
    assert result.needs_input == ('licence',)


def test_application_specific_motivation_assessment_adjustment_demographic_and_privacy_fields_stay_manual():
    labels = (
        ('motivation-en', 'Why do you want to work for this employer and role?', True),
        ('motivation-fi', 'Miksi haet juuri tätä työnantajaa?', True),
        ('motivation-sv', 'Varför vill du arbeta hos den här arbetsgivaren?', True),
        ('assessment-en', 'Are you willing to complete this assessment?', True),
        ('assessment-fi', 'Suostutko soveltuvuusarviointiin?', True),
        ('assessment-sv', 'Är du villig att göra lämplighetstestet?', True),
        ('adjustment-en', 'Do you need an adjustment to the recruitment process?', True),
        ('adjustment-fi', 'Tarvitsetko mukautuksia rekrytointiprosessiin?', True),
        ('adjustment-sv', 'Behöver du anpassning av rekryteringsprocessen?', True),
        ('demographic-en', 'Gender (optional)', False),
        ('demographic-fi', 'Sukupuoli (vapaaehtoinen)', False),
        ('demographic-sv', 'Kön (frivilligt)', False),
        ('privacy-en', 'Do you consent to the privacy notice and data retention terms?', True),
        ('privacy-fi', 'Hyväksytkö tietosuojaselosteen ja tietojen säilytyksen?', True),
        ('privacy-sv', 'Godkänner du integritetspolicyn och datalagringen?', True),
    )
    generic_answers = [{
        'question_id': 'materials:motivation',
        'category': 'MOTIVATION',
        'question': 'General interests and motivation (tailor for each employer)',
        'value': 'I want to do good work.',
        'source': 'USER_CONFIRMED',
        'answer_state': 'CONFIRMED',
        'scope_type': 'GLOBAL',
    }]

    for field_id, label, required in labels:
        assert question_id_for_form_label(label) is None
        result = resolve_application_fields(
            [FormField(field_id, label, required)],
            profile={}, facts=[], answers=generic_answers,
        )
        assert field_id not in result.values
        assert (field_id in result.needs_input) is required
        assert (field_id in result.skipped) is not required


def test_current_employer_contact_answer_is_never_reused_as_a_profile_preference():
    result = resolve_application_fields(
        [FormField('reference-contact', 'Får vi kontakta din nuvarande arbetsgivare?', True, 'select', ('Ja', 'Nej'))],
        profile={}, facts=[], country='Finland', employer='Northstar', answers=[{
            'question_id': 'materials:reference_contact',
            'category': 'FACT',
            'question': 'May a current employer be contacted?',
            'value': 'No',
            'source': 'USER_CONFIRMED',
            'answer_state': 'CONFIRMED',
            'scope_type': 'APPLICATION',
            'scope_employer': 'Northstar',
        }],
    )

    assert result.ready is False
    assert result.needs_input == ('reference-contact',)
