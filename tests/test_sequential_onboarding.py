from fastapi.testclient import TestClient

from sampoagent.app.main import create_app
from sampoagent.candidate.questions import SECTIONS


def client_for(path=':memory:', **kwargs):
    return TestClient(create_app(database_path=path, require_onboarding=True, **kwargs))


def finish_questions(client):
    for section in SECTIONS:
        values = {'section_reviewed': 'yes'}
        if section == 'contact':
            values.update(full_name='Test Candidate', email='candidate@example.org')
        response = client.post('/onboarding/questions/' + section, data=values, follow_redirects=False)
        assert response.status_code == 303
    return client.post('/onboarding/review-complete', data={'reviewed': 'yes', 'without_cv': 'yes'}, follow_redirects=False)


def launch_form(**changes):
    return dict(action='save_review', application_mode='review_everything', daily_limit='5',
                locations='', locations_exclude='', work_type='any', employment_type='any',
                hours_type='any', schedule='any', search_terms_include='software developer',
                search_terms_exclude='', **changes)


def test_fresh_start_redirects_and_blocks_direct_step_and_workspace_bypass():
    client = client_for()
    assert client.get('/', follow_redirects=False).headers['location'] == '/onboarding?section=contact'
    for path in ['/jobs', '/settings', '/onboarding/ready', '/onboarding?section=skills']:
        assert client.get(path, follow_redirects=False).status_code == 303
    for path in ['/onboarding/questions/skills', '/onboarding/complete', '/onboarding/ready']:
        assert client.post(path, data={'name': 'Bypass', 'locale': 'en'}).status_code == 409
    assert client.app.state.repository.profile() is None


def test_contact_requires_valid_identity_and_explicit_section_review():
    client = client_for()
    for data in [{}, {'section_reviewed': 'yes'}, {'full_name': 'Test', 'email': 'invalid', 'section_reviewed': 'yes'}]:
        assert client.post('/onboarding/questions/contact', data=data).status_code == 422
    response = client.post('/onboarding/questions/contact', data={
        'full_name': 'Test Candidate', 'email': 'test@example.org', 'section_reviewed': 'yes'}, follow_redirects=False)
    assert response.headers['location'] == '/onboarding?section=eligibility'
    assert client.get('/jobs', follow_redirects=False).status_code == 303
    assert client.app.state.repository.profile()['name'] == 'Test Candidate'


def test_progress_survives_restart_and_prior_edits_relock_later_steps(tmp_path):
    path = str(tmp_path / 'state.db')
    client = client_for(path)
    finish_questions(client)
    client.app.state.repository.connection.close()
    client = client_for(path)
    assert client.get('/', follow_redirects=False).headers['location'] == '/onboarding/ready'
    client.post('/onboarding/questions/contact', data={
        'full_name': 'Changed Candidate', 'email': 'test@example.org', 'section_reviewed': 'yes'})
    assert client.get('/onboarding/ready', follow_redirects=False).headers['location'] == '/onboarding?section=eligibility'


def test_review_requires_cv_or_deliberate_manual_profile_choice():
    client = client_for()
    for section in SECTIONS:
        data = {'section_reviewed': 'yes'}
        if section == 'contact':
            data.update(full_name='Test', email='test@example.org')
        client.post('/onboarding/questions/' + section, data=data)
    assert client.post('/onboarding/review-complete', data={'reviewed': 'yes'}).status_code == 422
    assert client.post('/onboarding/review-complete', data={'without_cv': 'yes'}).status_code == 422
    assert client.post('/onboarding/review-complete', data={'reviewed': 'yes', 'without_cv': 'yes'}, follow_redirects=False).status_code == 303


def test_only_valid_final_review_unlocks_workspace():
    client = client_for()
    finish_questions(client)
    invalid = launch_form()
    invalid['search_terms_include'] = ''
    assert client.post('/onboarding/ready', data=invalid).status_code == 422
    assert client.get('/', follow_redirects=False).status_code == 303
    response = client.post('/onboarding/ready', data=launch_form(), follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'].startswith('/?notice=')
    assert client.get('/', follow_redirects=False).status_code == 200
    assert client.app.state.repository.setting('dry_run') == 'true'


def test_worker_cannot_start_during_setup(tmp_path):
    from sampoagent.applications.worker_controller import AutopilotWorkerController
    client = client_for(str(tmp_path / 'state.db'))
    repo = client.app.state.repository
    repo.set_setting('application_mode', 'smart_approval')
    repo.set_setting('dry_run', 'false')
    repo.set_setting('daily_limit', '5')
    worker = AutopilotWorkerController(tmp_path / 'state.db')
    assert not worker._eligible(repo)


def test_finished_dry_run_starts_discovery_without_opening_employer_browser(tmp_path):
    from threading import Event
    from sampoagent.applications.worker_controller import AutopilotWorkerController
    from sampoagent.applications.worker import run_worker_cycle
    client = client_for(str(tmp_path / 'state.db'))
    finish_questions(client)
    client.post('/onboarding/ready', data=launch_form())
    # No external services in this fixture: real discovery creates a local run.
    repo = client.app.state.repository
    repo.connection.execute('DELETE FROM job_sources')
    repo.connection.commit()
    cycle_finished = Event()

    def cycle(repository, browser, **kwargs):
        result = run_worker_cycle(repository, browser, **kwargs)
        cycle_finished.set()
        return result

    def no_browser(_):
        raise AssertionError('Dry Run must not open an employer browser')

    worker = AutopilotWorkerController(tmp_path / 'state.db', tmp_path / 'storage',
                                      browser_factory=no_browser, cycle_runner=cycle)
    try:
        assert worker.sync() == 'started'
        assert cycle_finished.wait(5)
    finally:
        worker.close()
    assert repo.latest_discovery_run() is not None
    assert repo.setting('dry_run') == 'true'


def test_headless_worker_also_waits_for_setup():
    from sampoagent.applications.worker import run_worker_cycle
    client = client_for()
    repo = client.app.state.repository
    result = run_worker_cycle(repo, None)
    assert result.status == 'setup_required'
    assert repo.latest_discovery_run() is None
