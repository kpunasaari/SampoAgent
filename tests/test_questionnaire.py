from fastapi.testclient import TestClient

from sampoagent.app.main import create_app
from sampoagent.candidate.questions import question_id_for_label


def test_questionnaire_saves_local_draft_and_escapes_output(tmp_path):
    path = str(tmp_path / 'candidate.db')
    client = TestClient(create_app(database_path=path))
    assert 'Work eligibility' in client.get('/onboarding').text
    response = client.post('/onboarding/questions/contact', data={'full_name': '<script>test</script>', 'email': 'test@example.org'})
    assert response.status_code == 200
    client = TestClient(create_app(database_path=path))
    assert '&lt;script&gt;test&lt;/script&gt;' in client.get('/onboarding?section=contact').text
    assert client.app.state.repository.profile() is None


def test_invalid_answers_are_atomic_and_blank_is_not_no():
    client = TestClient(create_app(database_path=':memory:'))
    assert client.post('/onboarding/questions/eligibility', data={'work_country': 'Finland', 'work_permission': 'No'}).status_code == 200
    assert client.post('/onboarding/questions/eligibility', data={'work_country': 'Other', 'work_permission': 'fabricated'}).status_code == 422
    assert 'Finland' in client.get('/onboarding?section=eligibility').text
    client.post('/onboarding/questions/eligibility', data={'work_country': 'Finland', 'work_permission': ''})
    assert 'value="No" selected' not in client.get('/onboarding?section=eligibility').text


def test_unknown_sections_and_fields_rejected():
    client = TestClient(create_app(database_path=':memory:'))
    assert client.get('/onboarding?section=unknown').status_code == 404
    assert client.post('/onboarding/questions/contact', data={'secret': 'value'}).status_code == 422
    assert 'CV' in client.get('/onboarding?section=review').text


def test_draft_answers_become_reusable_only_when_individually_confirmed():
    client = TestClient(create_app(database_path=':memory:'))
    client.post('/onboarding/questions/preferences', data={'locations': 'Vantaa'})

    assert client.app.state.repository.answers() == []
    page = client.get('/onboarding?section=review').text
    assert 'preferences:locations' in page
    assert 'Confirm selected answers' in page

    response = client.post('/onboarding/confirm', data={'confirmed': ['preferences:locations']}, follow_redirects=False)

    assert response.status_code == 303
    answers = client.app.state.repository.answers()
    assert len(answers) == 1
    assert answers[0]['question'] == 'Preferred work locations'
    assert answers[0]['value'] == 'Vantaa'
    assert answers[0]['source'] == 'USER_CONFIRMED'


def test_confirmation_rejects_unknown_or_blank_draft_keys_atomically():
    client = TestClient(create_app(database_path=':memory:'))
    client.post('/onboarding/questions/preferences', data={'locations': 'Vantaa'})

    assert client.post('/onboarding/confirm', data={'confirmed': ['preferences:locations', 'contact:email']}).status_code == 422
    assert client.app.state.repository.answers() == []


def test_landing_review_ends_with_cv_upload_and_explains_role_activation():
    client = TestClient(create_app(database_path=':memory:'))

    page = client.get('/landing?section=review')

    assert page.status_code == 200
    assert 'action="/cvs/upload"' in page.text
    assert 'enctype="multipart/form-data"' in page.text
    assert 'review candidate facts' in page.text
    assert 'career suggestions' in page.text
    assert 'Suggestions never start a search until you activate a target role' in page.text


def test_question_labels_resolve_common_finnish_swedish_and_english_prompts():
    cases = (
        ('What is your salary expectation?', 'availability:salary'),
        ('Mikä on palkkatoiveesi?', 'availability:salary'),
        ('Vilket löneanspråk har du?', 'availability:salary'),
        ('When can you start at the earliest?', 'availability:start_date'),
        ('Milloin voit aloittaa aikaisintaan?', 'availability:start_date'),
        ('När kan du tidigast börja?', 'availability:start_date'),
        ('Do you have the right to work in Finland?', 'eligibility:work_permission'),
        ('Onko sinulla oikeus työskennellä Suomessa?', 'eligibility:work_permission'),
        ('Har du rätt att arbeta i Finland?', 'eligibility:work_permission'),
    )

    for label, question_id in cases:
        assert question_id_for_label(label) == question_id


def test_ambiguous_sponsorship_question_does_not_receive_a_stable_answer_id():
    assert question_id_for_label('Will you need employer sponsorship?') is None
