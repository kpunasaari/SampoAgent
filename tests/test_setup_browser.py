"""Real browser coverage of the complete first-run UI, using temporary data only."""
import socket
import threading
import time

import pytest
import uvicorn

from sampoagent.app.main import create_app


def test_browser_can_finish_sequential_setup(tmp_path):
    playwright_api = pytest.importorskip('playwright.sync_api')
    app = create_app(database_path=tmp_path / 'browser.db', storage_dir=tmp_path / 'storage', require_onboarding=True)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started
        with playwright_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={'width': 1280, 'height': 900})
                page.goto(f'http://127.0.0.1:{port}/')
                playwright_api.expect(page).to_have_url(f'http://127.0.0.1:{port}/onboarding?section=contact')
                page.get_by_role('button', name='Save and continue').click()
                assert page.locator('#q-full_name').evaluate('(input) => input.validity.valueMissing')
                page.get_by_label('Full legal name').fill('Synthetic Browser Candidate')
                page.get_by_label('Application email address').fill('browser@example.org')
                for next_section in ['eligibility', 'preferences', 'availability', 'experience', 'skills', 'materials', 'review']:
                    page.get_by_label('I have reviewed this section;', exact=False).check()
                    page.get_by_role('button', name='Save and continue').click()
                    playwright_api.expect(page).to_have_url(f'http://127.0.0.1:{port}/onboarding?section={next_section}')
                page.get_by_label('I have reviewed my answers and CV information.', exact=False).check()
                page.get_by_label('Continue with a manually entered profile', exact=False).check()
                page.get_by_role('button', name='Continue to job targets and working mode').click()
                playwright_api.expect(page).to_have_url(f'http://127.0.0.1:{port}/onboarding/ready')
                page.locator('[name="search_terms_include"]').fill('warehouse worker')
                page.locator('[name="daily_limit"]').fill('5')
                page.get_by_role('button', name='Finish setup · start job search in Dry Run').click()
                playwright_api.expect(page.get_by_role('heading', name='Dashboard', exact=True)).to_be_visible()
                assert app.state.repository.setting('setup_finished') == 'true'
                assert app.state.repository.setting('dry_run') == 'true'
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        app.state.repository.connection.close()
