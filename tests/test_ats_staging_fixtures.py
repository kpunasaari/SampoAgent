"""Synthetic ATS-shaped fixtures exercise the generic browser adapter only.

These cases are deliberately not claims of compatibility with any ATS vendor.
"""

from pathlib import Path

import pytest

from sampoagent.applications.workflow import ApplicationMode, can_submit, classify_question


FIXTURES = Path(__file__).parent / "fixtures" / "ats"


@pytest.mark.parametrize(
    ("scenario", "expected_fields"),
    [
        (
            "laura",
            {
                "full_name": ("Full name", "text", True, "label"),
                "email": ("Email address", "email", True, "label"),
                "phone": ("Phone number", "tel", False, "aria-label"),
                "source": ("How did you hear about this role?", "select", False, "label"),
                "resume": ("Upload your CV", "file", True, "label"),
            },
        ),
        (
            "reachmee",
            {
                "work_eligibility": (
                    "Are you legally authorized to work in Finland?", "radio", True, "fieldset legend"
                ),
                "availability": ("Availability", "select", True, "label"),
            },
        ),
        (
            "likeit",
            {
                "work_shifts": ("Which shifts are you available for?", "checkbox_group", False, "fieldset legend"),
                "privacy_consent": ("I agree to the privacy notice", "checkbox", True, "label"),
            },
        ),
    ],
)
def test_synthetic_ats_fixture_is_inspected_by_real_chromium(scenario, expected_fields):
    from playwright.sync_api import sync_playwright

    from sampoagent.agents.playwright_adapter import PlaywrightBrowserAgent

    fixture = FIXTURES / scenario / "application.html"
    html = fixture.read_text(encoding="utf-8")
    fixture_url = f"https://{scenario}.ats-fixtures.invalid/application"

    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch(headless=True)
        context = chromium.new_context()
        page = context.new_page()
        page.route(
            "https://**.ats-fixtures.invalid/**",
            lambda route: route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html),
        )
        try:
            page.goto(fixture_url, wait_until="domcontentloaded")
            agent = PlaywrightBrowserAgent(fixture.parent / ".unused-browser-profile")
            agent._context = context
            agent._page = page
            inspection = agent.inspect_form()

            actual = {
                field.field_id: (field.label, field.kind, field.required, field.source)
                for field in inspection.fields
            }
            assert actual == expected_fields
            assert inspection.final_url == fixture_url
            assert inspection.schema is not None
            assert inspection.schema.page_origin == f"https://{scenario}.ats-fixtures.invalid"
            assert inspection.schema.single_form_context

            if scenario == "laura":
                upload = next(field for field in inspection.fields if field.field_id == "resume")
                assert upload.accepted_types == (".pdf", "application/pdf")
                assert upload.max_file_size_bytes == 5_000_000
                source = next(field for field in inspection.fields if field.field_id == "source")
                assert source.options == ("Company website", "Job board")
                assert inspection.submit_control_ready
            elif scenario == "reachmee":
                work_eligibility = next(field for field in inspection.fields if field.field_id == "work_eligibility")
                assert work_eligibility.options == ("Yes", "No")
                assert inspection.schema.has_next_step
                assert inspection.next_step_control_ready
                assert "Step 1 of 2" in inspection.schema.navigation_checkpoint
                assert classify_question(work_eligibility.label) == "HIGH"
            else:
                privacy_consent = next(field for field in inspection.fields if field.field_id == "privacy_consent")
                assert classify_question(privacy_consent.label) == "HIGH"
                assert not can_submit(
                    ApplicationMode.AUTOPILOT,
                    dry_run=False,
                    risk="HIGH",
                    applied_today=0,
                    daily_limit=10,
                    autopilot_authorized=True,
                    within_scope=True,
                    required_answers_resolved=True,
                    job_active=True,
                    duplicate=False,
                    paused=False,
                )
        finally:
            context.close()
            chromium.close()
