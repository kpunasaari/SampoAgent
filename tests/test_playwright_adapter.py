import socket
from dataclasses import replace
import re
import pytest

from sampoagent.agents.browser import FormInspection
from sampoagent.agents.playwright_adapter import BrowserUnavailable, PlaywrightBrowserAgent
from sampoagent.applications.field_resolver import FormField
from sampoagent.agents.forms import FormSchema, parse_file_size_limit


class _FakeButton:
    def __init__(self):
        self.clicks = 0

    def click(self, **kwargs):
        self.clicks += 1


class _FakeRequest:
    def __init__(self, url: str):
        self.url = url


class _FakeRoute:
    def __init__(self, url: str):
        self.request = _FakeRequest(url)
        self.continued = False
        self.abort_error = None

    def continue_(self):
        self.continued = True

    def abort(self, error_code: str):
        self.abort_error = error_code


class _FakeWebSocketRoute:
    def __init__(self):
        self.closed = None

    def close(self, *, code: int, reason: str):
        self.closed = (code, reason)


class _FakePage:
    url = "https://careers.employer.fi/apply"


def _request_guarded_agent() -> PlaywrightBrowserAgent:
    agent = object.__new__(PlaywrightBrowserAgent)
    agent._restrict_cross_origin = True
    agent._allowed_origin = "https://jobs.employer.fi/apply"
    return agent


def _agent_with_inspection(inspection: FormInspection) -> tuple[PlaywrightBrowserAgent, _FakeButton]:
    agent = object.__new__(PlaywrightBrowserAgent)
    agent._context = object()
    agent._page = _FakePage()
    button = _FakeButton()
    agent._submit_buttons = [button]
    agent.inspect_form = lambda: inspection
    return agent, button


def test_submit_does_not_click_if_form_signature_changed_since_review():
    agent, button = _agent_with_inspection(
        FormInspection(
            (FormField("new-question", "Unexpected required question", True),),
            signature="new-signature",
            final_url="https://careers.employer.fi/apply",
            submit_control_ready=True,
        )
    )

    result = agent.submit("https://careers.employer.fi/apply", expected_signature="approved-signature")

    assert button.clicks == 0
    assert not result.submitted
    assert result.manual_action_required
    assert "changed" in result.message.casefold()


def test_submit_does_not_click_after_redirect_to_a_different_origin():
    agent, button = _agent_with_inspection(
        FormInspection(
            (FormField("name", "Full name", True),),
            signature="same-signature",
            final_url="https://collect.other.fi/apply",
            submit_control_ready=True,
        )
    )

    result = agent.submit("https://careers.employer.fi/apply", expected_signature="same-signature")

    assert button.clicks == 0
    assert not result.submitted
    assert result.manual_action_required


def test_submit_does_not_click_if_uploaded_cv_no_longer_matches_the_reviewed_package():
    agent, button = _agent_with_inspection(
        FormInspection(
            (FormField("cv", "Upload your CV", True, kind="file"),),
            signature="same-signature",
            final_url="https://careers.employer.fi/apply",
            submit_control_ready=True,
            uploaded_files={"cv": {"name": "replacement.pdf", "sha256": "changed-hash"}},
        )
    )

    result = agent.submit(
        "https://careers.employer.fi/apply",
        expected_signature="same-signature",
        expected_uploads={"cv": {"name": "approved.pdf", "sha256": "approved-hash"}},
    )

    assert button.clicks == 0
    assert not result.submitted
    assert not result.outcome_unknown
    assert result.manual_action_required
    assert "cv" in result.message.casefold() or "file" in result.message.casefold()


def test_submit_rechecks_authorization_at_the_last_possible_point_before_click():
    agent, button = _agent_with_inspection(
        FormInspection(
            (FormField("name", "Full name", True),),
            signature="approved-signature",
            final_url="https://careers.employer.fi/apply",
            submit_control_ready=True,
        )
    )

    result = agent.submit(
        "https://careers.employer.fi/apply",
        expected_signature="approved-signature",
        pre_click_check=lambda: False,
    )

    assert button.clicks == 0
    assert not result.submitted
    assert not result.outcome_unknown
    assert result.manual_action_required
    assert "before the final click" in result.message.casefold()


def test_form_schema_hash_binds_semantic_fields_origin_action_and_step_checkpoint():
    base = FormSchema.build(
        fields=(FormField("email", "Application email", True, "email", autocomplete="email", name="email", description="Use an address you check", source="label"),),
        page_url="https://careers.employer.fi/apply/step-1",
        action_url="https://careers.employer.fi/apply/submit",
        navigation_checkpoint="1 of 2 · Contact details",
        has_next_step=True,
    )
    changed_field = FormSchema.build(
        fields=(FormField("email", "Application email", True, "email", autocomplete="email", name="email", description="Use an address you check", source="label"),),
        page_url="https://careers.employer.fi/apply/step-1",
        action_url="https://careers.employer.fi/apply/submit",
        navigation_checkpoint="2 of 2 · Questions",
        has_next_step=True,
    )
    changed_prompt = FormSchema.build(
        fields=(FormField("email", "Application email", True, "email", autocomplete="email", name="email", description="Use a work address", source="label"),),
        page_url="https://careers.employer.fi/apply/step-1",
        action_url="https://careers.employer.fi/apply/submit",
        navigation_checkpoint="1 of 2 · Contact details",
        has_next_step=True,
    )

    assert base.page_origin == "https://careers.employer.fi"
    assert base.signature != changed_field.signature
    assert base.signature != changed_prompt.signature
    assert base.schema_version == "1.1"


def test_declared_file_type_and_size_are_included_in_the_schema_signature():
    common = {
        "fields": (FormField("cv", "Upload CV", True, "file", name="resume", accepted_types=("application/pdf", ".pdf"), max_file_size_bytes=5_000_000),),
        "page_url": "https://careers.employer.fi/apply",
        "action_url": "https://careers.employer.fi/apply",
    }
    original = FormSchema.build(**common)
    changed = FormSchema.build(**{
        **common,
        "fields": (FormField("cv", "Upload CV", True, "file", name="resume", accepted_types=("application/pdf", ".pdf"), max_file_size_bytes=1_000_000),),
    })

    assert original.signature != changed.signature
    assert parse_file_size_limit("Maximum file size: 5 MB") == 5_000_000
    assert parse_file_size_limit("Limit 5120 KiB") == 5_242_880
    assert parse_file_size_limit("Maximum size: 0 bytes") == 0
    assert parse_file_size_limit("Maximum 5000000") is None


def test_multiple_file_upload_is_captured_in_schema_and_never_filled_by_single_cv_upload(tmp_path):
    from playwright.sync_api import sync_playwright

    page_html = """<!doctype html><form action='/apply'>
      <label for='attachments'>Upload CV and certificates</label>
      <input id='attachments' name='attachments' type='file' accept='.pdf' multiple required>
      <button type='submit'>Apply</button>
    </form>"""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route(
            "https://forms.synthetic.test/**",
            lambda route: route.fulfill(status=200, body=page_html, content_type="text/html"),
        )
        page.goto("https://forms.synthetic.test/application")
        agent = object.__new__(PlaywrightBrowserAgent)
        agent._context = browser
        agent._page = page
        agent._fields = {}
        agent._kinds = {}
        agent._radio_options = {}
        agent._submit_buttons = []

        inspection = agent.inspect_form()
        field = inspection.fields[0]
        assert field.allows_multiple_files is True
        assert inspection.schema is not None
        assert inspection.schema.schema_version == "1.1"
        assert inspection.schema.signature
        single_file_schema = FormSchema.build(
            fields=(replace(field, allows_multiple_files=False),),
            page_url=inspection.schema.page_url,
            action_url=inspection.schema.action_url,
        )
        assert inspection.schema.signature != single_file_schema.signature

        cv = tmp_path / "candidate.pdf"
        cv.write_bytes(b"%PDF-1.4 synthetic")
        try:
            agent.upload("attachments", str(cv))
        except ValueError as error:
            assert "multiple" in str(error).casefold()
        else:
            raise AssertionError("One reviewed CV must not be treated as a completed multiple-file selection")
        browser.close()


def test_synthetic_finnish_swedish_english_ats_form_shape_matrix():
    from playwright.sync_api import sync_playwright

    cases = [
        ("fi-name-required", "<label for='v'>Koko nimi</label><input id='v' name='full_name_fi' required>", [("full_name_fi", "Koko nimi", True, "text", (), "label")]),
        ("en-name-optional", "<label for='v'>Full name</label><input id='v' name='full_name_en'>", [("full_name_en", "Full name", False, "text", (), "label")]),
        ("sv-name-required", "<label for='v'>Fullständigt namn</label><input id='v' name='full_name_sv' required>", [("full_name_sv", "Fullständigt namn", True, "text", (), "label")]),
        ("fi-email", "<label for='v'>Sähköposti</label><input id='v' name='email_fi' type='email' required>", [("email_fi", "Sähköposti", True, "email", (), "label")]),
        ("en-email", "<label for='v'>Email address</label><input id='v' name='email_en' type='email'>", [("email_en", "Email address", False, "email", (), "label")]),
        ("sv-phone", "<label for='v'>Telefonnummer</label><input id='v' name='phone_sv' type='tel' required>", [("phone_sv", "Telefonnummer", True, "tel", (), "label")]),
        ("fi-date", "<label for='v'>Aloituspäivä</label><input id='v' name='start_date' type='date' required>", [("start_date", "Aloituspäivä", True, "date", (), "label")]),
        ("en-textarea", "<label for='v'>Additional details</label><textarea id='v' name='details'></textarea>", [("details", "Additional details", False, "text", (), "label")]),
        ("sv-checkbox", "<label for='v'>Nyhetsbrev</label><input id='v' name='newsletter' type='checkbox'>", [("newsletter", "Nyhetsbrev", False, "checkbox", (), "label")]),
        ("fi-checkbox-group", "<fieldset><legend>Työvuorot</legend><label><input type='checkbox' name='shift_fi' value='day'>Päivä</label><label><input type='checkbox' name='shift_fi' value='night'>Yö</label></fieldset>", [("shift_fi", "Työvuorot", False, "checkbox_group", ("Päivä", "Yö"), "fieldset legend")]),
        ("en-checkbox-group", "<fieldset><legend>Preferred contact</legend><label><input type='checkbox' name='contact' value='email'>Email</label><label><input type='checkbox' name='contact' value='phone'>Phone</label></fieldset>", [("contact", "Preferred contact", False, "checkbox_group", ("Email", "Phone"), "fieldset legend")]),
        ("sv-checkbox-group", "<fieldset><legend>Arbetstid</legend><label><input type='checkbox' name='shift_sv' value='day'>Dag</label><label><input type='checkbox' name='shift_sv' value='night'>Natt</label></fieldset>", [("shift_sv", "Arbetstid", False, "checkbox_group", ("Dag", "Natt"), "fieldset legend")]),
        ("fi-radio", "<fieldset><legend>Onko sinulla työlupa?</legend><label><input type='radio' name='permit' value='yes' required>Kyllä</label><label><input type='radio' name='permit' value='no' required>Ei</label></fieldset>", [("permit", "Onko sinulla työlupa?", True, "radio", ("Kyllä", "Ei"), "fieldset legend")]),
        ("en-radio", "<fieldset><legend>Can you work weekends?</legend><label><input type='radio' name='weekends_en' value='yes' required>Yes</label><label><input type='radio' name='weekends_en' value='no' required>No</label></fieldset>", [("weekends_en", "Can you work weekends?", True, "radio", ("Yes", "No"), "fieldset legend")]),
        ("sv-radio", "<fieldset><legend>Kan du arbeta helger?</legend><label><input type='radio' name='weekends_sv' value='yes' required>Ja</label><label><input type='radio' name='weekends_sv' value='no' required>Nej</label></fieldset>", [("weekends_sv", "Kan du arbeta helger?", True, "radio", ("Ja", "Nej"), "fieldset legend")]),
        ("fi-select", "<label for='v'>Työsuhde</label><select id='v' name='employment_fi' required><option value=''>Valitse</option><option>Vakituinen</option><option>Määräaikainen</option></select>", [("employment_fi", "Työsuhde", True, "select", ("Valitse", "Vakituinen", "Määräaikainen"), "label")]),
        ("en-select", "<label for='v'>Work setting</label><select id='v' name='work_setting'><option>On-site</option><option>Remote</option></select>", [("work_setting", "Work setting", False, "select", ("On-site", "Remote"), "label")]),
        ("sv-select", "<label for='v'>Anställningsform</label><select id='v' name='employment_sv' required><option>Tillsvidare</option><option>Visstid</option></select>", [("employment_sv", "Anställningsform", True, "select", ("Tillsvidare", "Visstid"), "label")]),
        ("aria-label", "<input name='postcode' aria-label='Postnummer' required>", [("postcode", "Postnummer", True, "text", (), "aria-label")]),
        ("aria-labelledby", "<span id='field-label'>Ort</span><input name='location' aria-labelledby='field-label'>", [("location", "Ort", False, "text", (), "aria-labelledby")]),
        ("placeholder", "<input name='salary' placeholder='Salary expectation'>", [("salary", "Salary expectation", False, "text", (), "placeholder")]),
        ("name-fallback", "<input name='application_reference'>", [("application_reference", "application_reference", False, "text", (), "name")]),
        ("fi-file", "<label for='v'>Lataa CV</label><input id='v' name='cv_fi' type='file' accept='.pdf' required>", [("cv_fi", "Lataa CV", True, "file", (), "label")]),
        ("en-file", "<label for='v'>Upload résumé</label><input id='v' name='resume' type='file' accept='application/pdf,.pdf'>", [("resume", "Upload résumé", False, "file", (), "label")]),
        ("sv-file", "<label for='v'>Bifoga CV</label><input id='v' name='cv_sv' type='file' accept='.pdf' multiple>", [("cv_sv", "Bifoga CV", False, "file", (), "label")]),
        ("two-uploads", "<label for='a'>Upload CV</label><input id='a' name='cv_general' type='file'><label for='b'>Upload cover letter</label><input id='b' name='letter_general' type='file' required>", [("cv_general", "Upload CV", False, "file", (), "label"), ("letter_general", "Upload cover letter", True, "file", (), "label")]),
        ("description", "<label for='v'>Phone</label><input id='v' name='phone_description' type='tel' aria-describedby='help'><span id='help'>Use a number you can answer</span>", [("phone_description", "Phone", False, "tel", (), "label")]),
        ("multi-step", "<label for='v'>City</label><input id='v' name='city'><button type='button'>Continue</button>", [("city", "City", False, "text", (), "label")]),
        ("multi-form", "<form><label for='a'>Search</label><input id='a' name='search'></form><form><label for='b'>Applicant name</label><input id='b' name='name'></form>", [("search", "Search", False, "text", (), "label"), ("name", "Applicant name", False, "text", (), "label")]),
        ("cross-origin-action", "<form action='https://submit.other.test/application'><label for='v'>Full name</label><input id='v' name='name'></form>", [("name", "Full name", False, "text", (), "label")]),
    ]
    state = {"html": ""}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route(
            "https://forms.matrix.test/**",
            lambda route: route.fulfill(status=200, body=state["html"], content_type="text/html"),
        )
        form_fragments = []
        for index, (case_name, body, _expected_fields) in enumerate(cases):
            unique_ids = re.sub(
                r"\b(id|for|aria-labelledby|aria-describedby)='([^']+)'",
                lambda match: f"{match.group(1)}='case-{index}-{match.group(2)}'",
                body,
            )
            if case_name in {"multi-form", "cross-origin-action"}:
                form_fragments.append(unique_ids)
            else:
                form_fragments.append(f"<form action='/apply'>{unique_ids}<button type='submit'>Apply</button></form>")
        state["html"] = "<!doctype html><meta charset='utf-8'>" + "".join(form_fragments)
        page.goto("https://forms.matrix.test/base")
        agent = object.__new__(PlaywrightBrowserAgent)
        agent._context = browser
        agent._page = page
        agent._fields = {}
        agent._kinds = {}
        agent._radio_options = {}
        agent._submit_buttons = []

        inspection = agent.inspect_form()
        expected_fields = [field for _case_name, _body, expected in cases for field in expected]
        actual_fields = [
            (field.field_id, field.label, field.required, field.kind, field.options, field.source)
            for field in inspection.fields
        ]
        assert [field[1:] for field in actual_fields] == [field[1:] for field in expected_fields]
        assert inspection.schema is not None
        assert not inspection.schema.single_form_context
        assert inspection.schema.has_next_step
        assert len(cases) >= 20
        browser.close()


def test_browser_request_guard_does_not_resolve_dns_before_proxy(monkeypatch):
    def should_not_resolve(*_args, **_kwargs):
        raise AssertionError("The pinned proxy performs the definitive DNS resolution")

    monkeypatch.setattr("sampoagent.applications.urls.socket.getaddrinfo", should_not_resolve)
    public_route = _FakeRoute("https://jobs.employer.fi/assets/app.js")
    _request_guarded_agent()._guard_request(public_route)
    assert public_route.continued
    assert public_route.abort_error is None


def test_browser_request_guard_blocks_insecure_local_and_credential_bearing_urls():
    for url in (
        "http://jobs.employer.fi/apply",
        "https://127.0.0.1/admin",
        "https://user:secret@jobs.employer.fi/apply",
        "file:///C:/Users/candidate/private.pdf",
    ):
        route = _FakeRoute(url)
        _request_guarded_agent()._guard_request(route)
        assert route.abort_error == "blockedbyclient"
        assert not route.continued

    for url in ("data:text/plain,ok", "blob:https://jobs.employer.fi/opaque", "about:blank"):
        route = _FakeRoute(url)
        _request_guarded_agent()._guard_request(route)
        assert route.continued
        assert route.abort_error is None


def test_browser_request_guard_blocks_cross_origin_public_requests(monkeypatch):
    def should_not_resolve(*_args, **_kwargs):
        raise AssertionError("Cross-origin requests should be denied before DNS resolution")

    monkeypatch.setattr("sampoagent.applications.urls.socket.getaddrinfo", should_not_resolve)
    route = _FakeRoute("https://analytics.other.fi/collect")

    _request_guarded_agent()._guard_request(route)

    assert route.abort_error == "blockedbyclient"
    assert not route.continued


def test_browser_websocket_guard_closes_all_socket_connections():
    route = _FakeWebSocketRoute()
    PlaywrightBrowserAgent._block_websocket(route)

    assert route.closed == (1008, "WebSocket connections are not supported by the safe application adapter")


def test_browser_start_installs_local_pinned_proxy_before_page_use(tmp_path, monkeypatch):
    class FakePage:
        url = "about:blank"

    class FakeBrowserContext:
        def __init__(self):
            self.pages = [FakePage()]
            self.routes = []
            self.websocket_routes = []
            self.closed = False

        def route(self, pattern, handler):
            self.routes.append((pattern, handler))

        def route_web_socket(self, pattern, handler):
            self.websocket_routes.append((pattern, handler))

        def close(self):
            self.closed = True

    context = FakeBrowserContext()
    agent_reference = {}

    class FakeChromium:
        launch_options = None

        def launch_persistent_context(self, profile_dir, **kwargs):
            assert str(profile_dir) == str(tmp_path / "profile")
            assert kwargs["service_workers"] == "block"
            assert agent_reference["agent"]._egress_proxy.started
            self.launch_options = kwargs
            return context

    class FakePlaywright:
        chromium = FakeChromium()

        def start(self):
            return self

        def stop(self):
            return None

    fake_playwright = FakePlaywright()
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: fake_playwright)
    agent = PlaywrightBrowserAgent(tmp_path / "profile")
    agent_reference["agent"] = agent

    agent.start()

    assert len(context.routes) == 1
    assert context.routes[0][0] == "**/*"
    assert context.routes[0][1] == agent._guard_request
    assert len(context.websocket_routes) == 1
    assert context.websocket_routes[0][0] == "**/*"
    assert context.websocket_routes[0][1] == agent._block_websocket
    proxy_config = fake_playwright.chromium.launch_options["proxy"]
    assert proxy_config["server"].startswith("http://127.0.0.1:")
    assert proxy_config["username"]
    assert proxy_config["password"]
    agent.close()
    assert context.closed
    assert agent._egress_proxy is None


def test_proxy_is_closed_when_browser_start_fails(tmp_path, monkeypatch):
    class FakeChromium:
        def launch_persistent_context(self, _profile_dir, **_kwargs):
            raise RuntimeError("synthetic Chromium start failure")

    class FakePlaywright:
        chromium = FakeChromium()

        def start(self):
            return self

        def stop(self):
            return None

    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: FakePlaywright())
    agent = PlaywrightBrowserAgent(tmp_path / "profile")

    with pytest.raises(BrowserUnavailable):
        agent.start()

    assert agent._egress_proxy is None


def test_proxy_is_closed_even_if_browser_context_close_fails(tmp_path):
    class BrokenContext:
        def close(self):
            raise RuntimeError("synthetic browser shutdown failure")

    agent = PlaywrightBrowserAgent(tmp_path / "profile")
    from sampoagent.agents.pinned_proxy import PinnedHttpsProxy

    proxy = PinnedHttpsProxy("https://jobs.example.fi/apply")
    proxy.start()
    agent._context = BrokenContext()
    agent._egress_proxy = proxy

    with pytest.raises(RuntimeError, match="synthetic browser shutdown failure"):
        agent.close()

    assert not proxy.started


def test_inspector_extracts_aria_descriptions_radio_groups_file_constraints_and_step_metadata(tmp_path):
    from hashlib import sha256

    from playwright.sync_api import sync_playwright

    page_html = """<!doctype html><div role='form' action='/apply/submit'>
      <fieldset><legend>Do you have the right to work in Finland?</legend>
        <p id='permission-help'>This answer applies to Finland only.</p>
        <label><input type='radio' name='work_permission' value='yes' aria-describedby='permission-help' required>Yes</label>
        <label><input type='radio' name='work_permission' value='no' aria-describedby='permission-help' required>No</label>
      </fieldset>
      <p id='phone-label'>Phone number</p><input name='phone' aria-labelledby='phone-label' aria-describedby='phone-help'>
      <span id='phone-help'>Include a reachable number.</span>
      <label for='cv'>Upload CV</label><input id='cv' name='cv' type='file' accept='application/pdf,.pdf' data-max-file-size='100'>
      <label for='zero'>Zero-byte limit</label><input id='zero' name='zero' type='file' data-max-file-size='0'>
    </div><button type='button'>Next</button>"""

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route("https://careers.employer.test/**", lambda route: route.fulfill(status=200, body=page_html, content_type="text/html"))
        page.goto("https://careers.employer.test/apply")
        agent = object.__new__(PlaywrightBrowserAgent)
        agent._context = browser
        agent._page = page
        agent._fields = {}
        agent._kinds = {}
        agent._radio_options = {}
        agent._submit_buttons = []

        inspection = agent.inspect_form()
        fields = {field.field_id: field for field in inspection.fields}
        assert set(fields) == {"work_permission", "phone", "cv", "zero"}
        assert fields["work_permission"].label == "Do you have the right to work in Finland?"
        assert fields["work_permission"].options == ("Yes", "No")
        assert "applies to Finland only" in fields["work_permission"].description
        assert fields["phone"].label == "Phone number"
        assert fields["phone"].source == "aria-labelledby"
        assert "reachable number" in fields["phone"].description
        assert fields["cv"].accepted_types == ("application/pdf", ".pdf")
        assert fields["cv"].max_file_size_bytes == 100
        assert fields["zero"].max_file_size_bytes == 0
        assert inspection.schema is not None
        assert inspection.schema.action_url == "https://careers.employer.test/apply/submit"
        assert inspection.schema.has_next_step
        assert inspection.schema.navigation_checkpoint == ""
        assert inspection.schema.single_form_context

        wrong_type = tmp_path / "notes.txt"
        wrong_type.write_text("not a CV", encoding="utf-8")
        try:
            agent.upload("cv", str(wrong_type))
        except ValueError as error:
            assert "type" in str(error).casefold()
        else:
            raise AssertionError("The adapter must reject a file type excluded by the employer form")

        too_large = tmp_path / "large.pdf"
        too_large.write_bytes(b"%PDF-1.4 " + b"x" * 120)
        try:
            agent.upload("cv", str(too_large))
        except ValueError as error:
            assert "size" in str(error).casefold()
        else:
            raise AssertionError("The adapter must reject a file over the declared limit")

        accepted = tmp_path / "candidate.pdf"
        accepted.write_bytes(b"%PDF-1.4 synthetic")
        agent.upload("cv", str(accepted))
        uploaded = agent.inspect_form().uploaded_files["cv"]
        assert uploaded == {"name": "candidate.pdf", "sha256": sha256(accepted.read_bytes()).hexdigest()}
        browser.close()
