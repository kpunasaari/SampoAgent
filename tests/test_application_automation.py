from dataclasses import replace
from hashlib import sha256
from pathlib import Path

from sampoagent.agents.browser import FormInspection, SubmissionResult
from sampoagent.agents.forms import FormSchema
from sampoagent.applications.field_resolver import FormField
from sampoagent.applications.runner import process_application
from sampoagent.db.repository import Repository


class FakeBrowser:
    configured = True

    def __init__(self, fields, *, captcha=False, authentication=False, errors=(), result=None, on_open=None, on_fill=None, on_upload=None, on_pre_click=None):
        self.inspection = FormInspection(tuple(fields), captcha_detected=captcha, authentication_required=authentication, validation_errors=tuple(errors), signature="form-v1", final_url="https://careers.northstar-logistics.fi/apply/warehouse")
        self.result = result or SubmissionResult(True, False, "Application received", final_url="https://careers.northstar-logistics.fi/confirmation", confirmation_id="receipt-123")
        self.on_fill = on_fill
        self.on_upload = on_upload
        self.on_pre_click = on_pre_click
        self.on_open = on_open
        self.opened = []
        self.filled = {}
        self.uploads = []
        self.uploaded_files = {}
        self.submitted = 0

    def open(self, url):
        self.opened.append(url)
        if self.on_open:
            self.on_open()

    def inspect_form(self):
        return replace(self.inspection, values={**self.inspection.values, **self.filled}, uploaded_files=dict(self.uploaded_files))

    def fill(self, field_id, value):
        self.filled[field_id] = value
        if self.on_fill:
            self.on_fill()

    def upload(self, field_id, path):
        self.uploads.append((field_id, path))
        self.uploaded_files[field_id] = {"name": Path(path).name, "sha256": sha256(Path(path).read_bytes()).hexdigest()}
        if self.on_upload:
            self.on_upload()

    def validation_errors(self):
        return self.inspection.validation_errors

    def submit(self, url, *, expected_signature=None, expected_uploads=None, pre_click_check=None):
        if self.on_pre_click:
            self.on_pre_click()
        if pre_click_check is not None and not pre_click_check():
            return SubmissionResult(False, True, "Authorization changed before the click", final_url=url)
        self.submitted += 1
        return self.result


class MultiStepFakeBrowser(FakeBrowser):
    def __init__(self, steps, *, result=None, on_advance=None, on_fill=None):
        super().__init__(steps[0].fields, result=result, on_fill=on_fill)
        self.steps = tuple(steps)
        self.step_index = 0
        self.advance_calls = 0
        self.on_advance = on_advance

    def inspect_form(self):
        return replace(
            self.steps[self.step_index],
            values={**self.steps[self.step_index].values, **self.filled},
            uploaded_files=dict(self.uploaded_files),
        )

    def validation_errors(self):
        return self.steps[self.step_index].validation_errors

    def advance_step(self, *, expected_signature, pre_click_check=None):
        inspection = self.inspect_form()
        if inspection.signature != expected_signature or not inspection.schema.has_next_step:
            raise RuntimeError("step changed before Continue")
        if pre_click_check is not None and not pre_click_check():
            raise RuntimeError("authorization changed before Continue")
        self.advance_calls += 1
        if self.on_advance:
            self.on_advance()
        self.step_index += 1
        return self.inspect_form()


def _step_inspection(fields, number, *, has_next, page_url=None):
    page_url = page_url or f"https://careers.northstar-logistics.fi/apply/step-{number}"
    schema = FormSchema.build(
        fields=tuple(fields),
        page_url=page_url,
        action_url="https://careers.northstar-logistics.fi/apply/continue",
        navigation_checkpoint=f"{number} · Application details",
        has_next_step=has_next,
    )
    return FormInspection(
        tuple(fields),
        signature=f"step-{number}",
        final_url=page_url,
        submit_control_ready=not has_next,
        schema=schema,
        next_step_control_ready=has_next,
    )


def _authorized_repository(tmp_path):
    repository = Repository(tmp_path / "automation.db")
    repository.initialize()
    repository.load_demo()
    repository.add_target_occupation("Warehouse Worker", "Varastotyöntekijä")
    repository.connection.execute("UPDATE jobs SET application_url=? WHERE id=1", ("https://careers.northstar-logistics.fi/apply/warehouse",))
    repository.connection.commit()
    repository.mark_job_user_reviewed(1, reviewed_current=True)
    repository.set_setting("application_mode", "autopilot")
    repository.set_setting("daily_limit", "5")
    repository.set_setting("dry_run", "false")
    repository.set_setting("automation_paused", "false")
    repository.grant_autopilot()
    application_id = repository.queue_application(1, language="en", cv_path=None)
    return repository, application_id


def test_autopilot_holds_job_outside_selected_roles_before_opening_browser(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)
    warehouse = next(item for item in repository.target_occupations() if item["title_en"] == "Warehouse Worker")
    repository.set_target_occupation_enabled(int(warehouse["id"]), False)
    repository.connection.execute("UPDATE career_profiles SET enabled=0")
    repository.connection.commit()
    repository.add_target_occupation("Nurse", "Sairaanhoitaja")
    repository.revoke_autopilot("Selected target changed")
    repository.grant_autopilot()
    browser = FakeBrowser(_basic_fields())

    result = process_application(repository, application_id, browser)

    assert result == "BLOCKED"
    assert browser.opened == []
    assert browser.filled == {}
    assert browser.submitted == 0
    repository.connection.close()


def test_autopilot_grant_is_invalidated_by_career_profile_or_source_policy_changes(tmp_path):
    repository, _application_id = _authorized_repository(tmp_path)
    assert repository.autopilot_authorized()
    repository.connection.execute("UPDATE career_profiles SET name='Different user-selected scope'")
    repository.connection.commit()
    assert not repository.autopilot_authorized()

    repository.grant_autopilot()
    source = repository.rows("job_sources")[0]
    repository.connection.execute("UPDATE job_sources SET url='https://changed.example.org/feed' WHERE id=?", (source["id"],))
    repository.connection.commit()
    assert not repository.autopilot_authorized()
    repository.connection.close()


def _basic_fields():
    return [
        FormField("name", "Full name", True),
        FormField("email", "Email address", True, kind="email"),
    ]


def test_autopilot_processes_verified_job_and_records_confirmation(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)
    browser = FakeBrowser(_basic_fields())

    result = process_application(repository, application_id, browser)

    assert result == "APPLIED"
    assert browser.filled == {"name": "Aino Example", "email": "aino@example.test"}
    assert browser.submitted == 1
    assert repository.application(application_id)["status"] == "APPLIED"
    assert repository.submission_evidence_for_application(application_id)[0]["confirmation_id"] == "receipt-123"
    repository.connection.close()


def test_autopilot_holds_captcha_before_filling_or_submitting(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)
    browser = FakeBrowser(_basic_fields(), captcha=True)

    result = process_application(repository, application_id, browser)

    assert result == "CAPTCHA_HOLD"
    assert browser.filled == {}
    assert browser.submitted == 0
    assert repository.captcha_tasks()[0]["application_id"] == application_id
    repository.connection.close()


def test_network_failure_while_opening_application_page_waits_without_candidate_data(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)

    def disconnect_during_navigation():
        raise OSError("connection reset; private diagnostic must not be persisted")

    browser = FakeBrowser(_basic_fields(), on_open=disconnect_during_navigation)

    result = process_application(repository, application_id, browser)

    assert result == "NEEDS_USER"
    assert browser.filled == {}
    assert browser.uploads == []
    assert browser.submitted == 0
    application = repository.application(application_id)
    assert application["queue_state"] == "WAITING_USER"
    assert "private diagnostic" not in str(application)
    repository.connection.close()


def test_grant_revoked_after_page_load_prevents_any_candidate_form_entry(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)

    def revoke_during_navigation():
        repository.revoke_autopilot("Candidate profile changed while the employer page was loading")
        repository.connection.commit()

    browser = FakeBrowser(_basic_fields(), on_open=revoke_during_navigation)

    result = process_application(repository, application_id, browser)

    assert browser.filled == {}
    assert browser.uploads == []
    assert browser.submitted == 0
    assert result == "NEEDS_USER"
    assert repository.application(application_id)["queue_state"] == "WAITING_USER"
    repository.connection.close()


def test_reauthorized_changed_profile_does_not_reuse_pre_navigation_answers(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)

    def update_profile_and_regrant_during_navigation():
        repository.save_profile("Updated Candidate", "en", "updated@example.test")
        repository.set_setting("dry_run", "false")
        repository.grant_autopilot()

    browser = FakeBrowser(_basic_fields(), on_open=update_profile_and_regrant_during_navigation)

    result = process_application(repository, application_id, browser)

    assert browser.filled == {}
    assert browser.uploads == []
    assert browser.submitted == 0
    assert result == "NEEDS_USER"
    assert repository.application(application_id)["queue_state"] == "WAITING_USER"
    repository.connection.close()


def test_reauthorized_changed_profile_during_fill_cannot_reach_final_submit(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)
    updated = False

    def update_after_first_field():
        nonlocal updated
        if not updated:
            updated = True
            repository.save_profile("Updated Candidate", "en", "updated@example.test")
            repository.set_setting("dry_run", "false")
            repository.grant_autopilot()

    browser = FakeBrowser(_basic_fields(), on_fill=update_after_first_field)

    result = process_application(repository, application_id, browser)

    assert browser.submitted == 0
    assert browser.filled == {"name": "Aino Example"}
    assert result == "NEEDS_USER"
    assert repository.application(application_id)["queue_state"] == "WAITING_USER"
    repository.connection.close()


def test_unknown_required_form_field_stops_without_submission(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)
    browser = FakeBrowser(_basic_fields() + [FormField("start_date", "Available start date", True, kind="date")])

    result = process_application(repository, application_id, browser)

    assert result == "NEEDS_USER"
    assert browser.submitted == 0
    assert repository.application(application_id)["queue_state"] == "WAITING_USER"
    repository.connection.close()


def test_second_required_attachment_stops_before_candidate_fields_are_filled(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)
    archived_cv = tmp_path / "approved-cv.pdf"
    archived_cv.write_bytes(b"%PDF-1.4 synthetic CV")
    repository.connection.execute(
        "UPDATE applications SET cv_path=? WHERE id=?", (str(archived_cv), application_id)
    )
    repository.connection.commit()
    fields = _basic_fields() + [
        FormField("resume", "Upload your CV", True, kind="file"),
        FormField("letter", "Upload a cover letter", True, kind="file"),
    ]
    browser = FakeBrowser(fields)

    result = process_application(repository, application_id, browser)

    assert result == "NEEDS_USER"
    assert browser.filled == {}
    assert browser.uploads == []
    assert browser.submitted == 0
    repository.connection.close()


def test_runner_does_not_submit_optional_candidate_data_already_prefilled_by_employer_site(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)
    browser = FakeBrowser(_basic_fields() + [FormField("statement", "Additional statement", False)])
    browser.inspection = replace(browser.inspection, values={"statement": "Unverified text saved on the employer site"})

    result = process_application(repository, application_id, browser)

    assert result == "NEEDS_USER"
    assert browser.filled == {}
    assert browser.submitted == 0
    assert repository.application(application_id)["queue_state"] == "WAITING_USER"
    repository.connection.close()


def test_full_autopilot_completes_same_origin_multistep_form_then_submits_once(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)
    browser = MultiStepFakeBrowser((
        _step_inspection([FormField("name", "Full name", True)], 1, has_next=True),
        _step_inspection([FormField("email", "Email address", True, kind="email")], 2, has_next=False),
    ))

    result = process_application(repository, application_id, browser)

    assert result == "APPLIED"
    assert browser.filled == {"name": "Aino Example", "email": "aino@example.test"}
    assert browser.advance_calls == 1
    assert browser.submitted == 1
    repository.connection.close()


def test_smart_approval_multistep_form_waits_before_candidate_data_or_navigation(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)
    repository.set_setting("application_mode", "smart_approval")
    repository.revoke_autopilot("Switch to Smart Approval")
    browser = MultiStepFakeBrowser((
        _step_inspection([FormField("name", "Full name", True)], 1, has_next=True),
        _step_inspection([FormField("email", "Email address", True, kind="email")], 2, has_next=False),
    ))

    result = process_application(repository, application_id, browser)

    assert result == "NEEDS_USER"
    assert browser.filled == {}
    assert browser.advance_calls == 0
    assert browser.submitted == 0
    repository.connection.close()


def test_multistep_high_risk_later_page_stops_before_filling_that_page(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)
    browser = MultiStepFakeBrowser((
        _step_inspection([FormField("name", "Full name", True)], 1, has_next=True),
        _step_inspection([FormField("health", "Do you have a medical condition?", True)], 2, has_next=False),
    ))

    result = process_application(repository, application_id, browser)

    assert result == "NEEDS_USER"
    assert browser.filled == {"name": "Aino Example"}
    assert browser.advance_calls == 1
    assert browser.submitted == 0
    assert "high-risk" in repository.application(application_id)["notes"].casefold()
    repository.connection.close()


def test_multistep_without_unique_next_control_stops_before_filling_page(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)
    first_page = replace(
        _step_inspection([FormField("name", "Full name", True)], 1, has_next=True),
        next_step_control_ready=False,
    )
    browser = MultiStepFakeBrowser((
        first_page,
        _step_inspection([FormField("email", "Email address", True, kind="email")], 2, has_next=False),
    ))

    result = process_application(repository, application_id, browser)

    assert result == "NEEDS_USER"
    assert browser.filled == {}
    assert browser.advance_calls == 0
    assert browser.submitted == 0
    repository.connection.close()


def test_multistep_scope_change_before_continue_prevents_page_advance(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)

    def revoke_during_fill():
        repository.revoke_autopilot("Candidate revoked the grant during form preparation")

    browser = MultiStepFakeBrowser((
        _step_inspection([FormField("name", "Full name", True)], 1, has_next=True),
        _step_inspection([FormField("email", "Email address", True, kind="email")], 2, has_next=False),
    ), on_fill=revoke_during_fill)

    result = process_application(repository, application_id, browser)

    assert result == "NEEDS_USER"
    assert browser.filled == {"name": "Aino Example"}
    assert browser.advance_calls == 0
    assert browser.submitted == 0
    repository.connection.close()


def test_multistep_changed_job_snapshot_prevents_saving_the_next_page(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)

    def change_listing_during_fill():
        repository.connection.execute("UPDATE jobs SET description=? WHERE id=1", ("Updated employer requirements",))
        repository.connection.commit()

    browser = MultiStepFakeBrowser((
        _step_inspection([FormField("name", "Full name", True)], 1, has_next=True),
        _step_inspection([FormField("email", "Email address", True, kind="email")], 2, has_next=False),
    ), on_fill=change_listing_during_fill)

    result = process_application(repository, application_id, browser)

    assert result == "NEEDS_USER"
    assert browser.filled == {"name": "Aino Example"}
    assert browser.advance_calls == 0
    assert browser.submitted == 0
    repository.connection.close()


def test_multistep_changed_job_snapshot_cancels_final_click_reservation(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)
    browser = MultiStepFakeBrowser((
        _step_inspection([FormField("name", "Full name", True)], 1, has_next=True),
        _step_inspection([FormField("email", "Email address", True, kind="email")], 2, has_next=False),
    ))

    def change_listing_before_final_click():
        repository.connection.execute("UPDATE jobs SET description=? WHERE id=1", ("Updated employer requirements",))
        repository.connection.commit()

    browser.on_pre_click = change_listing_before_final_click

    result = process_application(repository, application_id, browser)

    assert result == "NEEDS_USER"
    assert browser.filled == {"name": "Aino Example", "email": "aino@example.test"}
    assert browser.advance_calls == 1
    assert browser.submitted == 0
    assert repository.application(application_id)["queue_state"] == "WAITING_USER"
    repository.connection.close()


def test_multistep_later_captcha_is_queued_after_stopping_before_that_page(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)
    captcha_page = replace(
        _step_inspection([], 2, has_next=False),
        captcha_detected=True,
    )
    browser = MultiStepFakeBrowser((
        _step_inspection([FormField("name", "Full name", True)], 1, has_next=True),
        captcha_page,
    ))

    result = process_application(repository, application_id, browser)

    assert result == "CAPTCHA_HOLD"
    assert browser.filled == {"name": "Aino Example"}
    assert browser.advance_calls == 1
    assert browser.submitted == 0
    assert repository.captcha_tasks()[0]["application_id"] == application_id
    repository.connection.close()


def test_multistep_cv_upload_on_intermediate_page_holds_before_entering_page_data(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)
    cv_path = tmp_path / "candidate.pdf"
    cv_path.write_bytes(b"%PDF synthetic")
    repository.update_application_cv_path(application_id, str(cv_path))
    browser = MultiStepFakeBrowser((
        _step_inspection(
            [FormField("name", "Full name", True), FormField("resume", "Upload CV", True, kind="file")],
            1,
            has_next=True,
        ),
        _step_inspection([FormField("email", "Email address", True, kind="email")], 2, has_next=False),
    ))

    result = process_application(repository, application_id, browser)

    assert result == "NEEDS_USER"
    assert browser.filled == {}
    assert browser.uploads == []
    assert browser.advance_calls == 0
    assert browser.submitted == 0
    repository.connection.close()


def test_multistep_cross_origin_result_is_held_before_filling_new_page(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)
    browser = MultiStepFakeBrowser((
        _step_inspection([FormField("name", "Full name", True)], 1, has_next=True),
        _step_inspection(
            [FormField("email", "Email address", True, kind="email")],
            2,
            has_next=False,
            page_url="https://forms.other.fi/apply/step-2",
        ),
    ))

    result = process_application(repository, application_id, browser)

    assert result == "NEEDS_USER"
    assert browser.filled == {"name": "Aino Example"}
    assert browser.advance_calls == 1
    assert browser.submitted == 0
    repository.connection.close()


def test_multistep_page_count_is_bounded_before_entering_eighth_next_step(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)
    steps = tuple(
        _step_inspection([FormField(f"unknown-{number}", f"Optional prompt {number}", False)], number, has_next=True)
        for number in range(1, 9)
    )
    browser = MultiStepFakeBrowser(steps)

    result = process_application(repository, application_id, browser)

    assert result == "NEEDS_USER"
    assert browser.filled == {}
    assert browser.advance_calls == 7
    assert browser.submitted == 0
    repository.connection.close()


def test_legacy_multistep_snapshot_still_holds_before_candidate_data(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)
    browser = FakeBrowser(_basic_fields())
    browser.inspection = replace(
        browser.inspection,
        schema=FormSchema.build(
            fields=tuple(_basic_fields()),
            page_url="https://careers.northstar-logistics.fi/apply/step-1",
            action_url="https://careers.northstar-logistics.fi/apply/submit",
            navigation_checkpoint="1 of 3 · Contact details",
            has_next_step=True,
        ),
    )

    result = process_application(repository, application_id, browser)

    assert result == "NEEDS_USER"
    assert browser.filled == {}
    assert browser.submitted == 0
    assert "next/continue" in repository.application(application_id)["notes"].casefold()
    repository.connection.close()


def test_cv_and_questionnaire_conflict_waits_on_only_the_conflicting_field(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)
    repository.connection.execute(
        "INSERT INTO facts(type,value,provenance,source_id,confidence,confirmed,rejected) VALUES ('licence','B','CV_CONFIRMED','cv:synthetic',1,1,0)"
    )
    repository.connection.commit()
    repository.add_answer(
        "FACT",
        "Driving licence categories",
        "C",
        "USER_CONFIRMED",
    )
    repository.grant_autopilot()
    browser = FakeBrowser(_basic_fields() + [FormField("licence", "What driving licence categories do you hold?", True)])

    result = process_application(repository, application_id, browser)

    application = repository.application(application_id)
    assert result == "NEEDS_USER"
    assert application["queue_state"] == "WAITING_USER"
    assert "licence" in application["notes"]
    assert browser.submitted == 0
    assert browser.filled == {}
    repository.connection.close()


def test_unexpected_public_redirect_stops_before_sending_candidate_data(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)
    browser = FakeBrowser(_basic_fields())
    browser.inspection = FormInspection(
        tuple(_basic_fields()),
        signature="form-v1",
        final_url="https://unrelated-employer.fi/collect",
    )

    result = process_application(repository, application_id, browser)

    assert result == "NEEDS_USER"
    assert browser.filled == {}
    assert browser.submitted == 0
    repository.connection.close()


def test_high_risk_form_declaration_never_uses_autopilot_grant(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)
    browser = FakeBrowser(_basic_fields() + [FormField("record", "Do you have a criminal record?", True, kind="select", options=("Yes", "No"))])

    result = process_application(repository, application_id, browser)

    assert result == "NEEDS_USER"
    assert browser.submitted == 0
    repository.connection.close()


def test_autopilot_may_reuse_a_confirmed_medium_risk_answer_inside_saved_scope(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)
    repository.add_answer("PREFERENCE", "Expected gross pay: range, currency and hourly/monthly/annual basis", "15 EUR/hour", "USER_CONFIRMED")
    repository.grant_autopilot()
    browser = FakeBrowser(_basic_fields() + [FormField("salary", "What is your salary expectation?", True)])

    result = process_application(repository, application_id, browser)

    assert result == "APPLIED"
    assert browser.filled["salary"] == "15 EUR/hour"
    repository.connection.close()


def test_smart_approval_waits_and_rechecks_the_exact_reviewed_package(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)
    repository.set_setting("application_mode", "smart_approval")
    repository.revoke_autopilot("Switch to Smart Approval")
    browser = FakeBrowser(_basic_fields())

    first_result = process_application(repository, application_id, browser)
    review = repository.application_review(application_id)

    assert first_result == "NEEDS_REVIEW"
    assert browser.submitted == 0
    assert browser.filled == {}
    assert browser.uploads == []
    assert review["state"] == "WAITING"
    assert review["package"]["answers"][0]["value"] == "Aino Example"
    assert review["package"]["job"]["title"] == "Warehouse Worker"
    assert review["package"]["cv"]["sha256"] == ""
    assert repository.approve_application_review(application_id, review["package_hash"])

    final_result = process_application(repository, application_id, browser)

    assert final_result == "APPLIED"
    assert browser.submitted == 1
    repository.connection.close()


def test_smart_approval_is_invalidated_if_form_changes_after_approval(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)
    repository.set_setting("application_mode", "smart_approval")
    repository.revoke_autopilot("Switch to Smart Approval")
    first_browser = FakeBrowser(_basic_fields())
    assert process_application(repository, application_id, first_browser) == "NEEDS_REVIEW"
    review = repository.application_review(application_id)
    assert repository.approve_application_review(application_id, review["package_hash"])

    changed_browser = FakeBrowser(_basic_fields() + [FormField("optional", "Optional portfolio link", False)])
    result = process_application(repository, application_id, changed_browser)

    assert result == "NEEDS_REVIEW"
    assert changed_browser.submitted == 0
    assert repository.application_review(application_id)["state"] == "WAITING"
    repository.connection.close()


def test_smart_approval_can_submit_confirmed_medium_risk_after_exact_review(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)
    repository.set_setting("application_mode", "smart_approval")
    repository.revoke_autopilot("Switch to Smart Approval")
    repository.add_answer(
        "PREFERENCE",
        "Expected gross pay: range, currency and hourly/monthly/annual basis",
        "15 EUR/hour",
        "USER_CONFIRMED",
    )
    browser = FakeBrowser(_basic_fields() + [FormField("salary", "What is your salary expectation?", True)])

    assert process_application(repository, application_id, browser) == "NEEDS_REVIEW"
    review = repository.application_review(application_id)
    assert review["package"]["answers"][-1]["value"] == "15 EUR/hour"
    assert repository.approve_application_review(application_id, review["package_hash"])

    result = process_application(repository, application_id, browser)

    assert result == "APPLIED"
    assert browser.filled["salary"] == "15 EUR/hour"
    assert browser.submitted == 1
    repository.connection.close()


def test_revoked_pause_is_rechecked_immediately_before_submit(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)
    browser = FakeBrowser(_basic_fields(), on_fill=lambda: repository.set_setting("automation_paused", "true"))

    result = process_application(repository, application_id, browser)

    assert result == "PAUSED"
    assert browser.submitted == 0
    assert repository.application(application_id)["queue_state"] == "READY"
    repository.connection.close()


def test_pause_at_adapter_preclick_gate_cancels_reservation_without_consuming_daily_slot(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)
    browser = FakeBrowser(
        _basic_fields(),
        on_pre_click=lambda: repository.set_setting("automation_paused", "true"),
    )

    result = process_application(repository, application_id, browser)

    assert result == "PAUSED"
    assert browser.submitted == 0
    assert repository.application(application_id)["queue_state"] == "READY"
    assert repository.connection.execute("SELECT state FROM application_attempts").fetchone()[0] == "CANCELLED"
    assert repository.submissions_reserved_today() == 0
    repository.set_setting("automation_paused", "false")
    assert process_application(repository, application_id, FakeBrowser(_basic_fields())) == "APPLIED"
    repository.connection.close()


def test_profile_change_invalidates_saved_autopilot_authorization(tmp_path):
    repository, _ = _authorized_repository(tmp_path)

    repository.add_candidate_record("licence", {"title": "B licence", "details": "Confirmed by user"})

    assert not repository.autopilot_authorized()
    repository.connection.close()


def test_interrupted_submission_is_marked_unverified_and_not_retried(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)
    preparation_token = "test-worker"
    assert repository.claim_application_preparation(application_id, owner_token=preparation_token)
    attempt_id = repository.reserve_submission_attempt(application_id, daily_limit=5, package_hash="package-hash", preparation_token=preparation_token)

    repository.recover_interrupted_submissions()

    assert repository.application(application_id)["status"] == "SUBMITTED_UNVERIFIED"
    assert repository.application(application_id)["queue_state"] == "DO_NOT_RETRY"
    assert repository.reserve_submission_attempt(application_id, daily_limit=5, package_hash="package-hash", preparation_token=preparation_token) is None
    assert repository.connection.execute("SELECT state FROM application_attempts WHERE id=?", (attempt_id,)).fetchone()[0] == "UNKNOWN"
    repository.connection.close()


def test_cross_origin_submission_redirect_is_not_recorded_as_confirmation(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)
    browser = FakeBrowser(
        _basic_fields(),
        result=SubmissionResult(
            submitted=True,
            outcome_unknown=False,
            manual_action_required=False,
            message="Application received",
            final_url="https://careers.other.fi/confirmation",
            confirmation_id="untrusted-123",
        ),
    )

    result = process_application(repository, application_id, browser)

    assert result == "SUBMITTED_UNVERIFIED"
    assert browser.submitted == 1
    assert repository.submission_evidence_for_application(application_id) == []
    assert repository.application(application_id)["queue_state"] == "DO_NOT_RETRY"
    repository.connection.close()


def test_runner_stops_when_browser_does_not_retain_the_approved_field_values(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)

    class MisreportingBrowser(FakeBrowser):
        def inspect_form(self):
            inspection = super().inspect_form()
            if self.filled:
                return replace(inspection, values={"name": "Wrong name", "email": "wrong@example.test"})
            return inspection

    browser = MisreportingBrowser(_basic_fields())

    result = process_application(repository, application_id, browser)

    assert result == "NEEDS_USER"
    assert browser.submitted == 0
    assert repository.application(application_id)["queue_state"] == "WAITING_USER"
    assert browser.inspect_form().values["name"] == "Wrong name"
    repository.connection.close()


def test_runner_stops_when_browser_uploaded_a_different_cv_checksum(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)
    cv_path = tmp_path / "resume.pdf"
    cv_path.write_bytes(b"approved resume bytes")
    repository.update_application_cv_path(application_id, str(cv_path))

    class WrongUploadBrowser(FakeBrowser):
        def inspect_form(self):
            inspection = super().inspect_form()
            if self.uploads:
                return replace(inspection, uploaded_files={"cv": {"name": "resume.pdf", "sha256": "wrong-hash"}})
            return inspection

    browser = WrongUploadBrowser(_basic_fields() + [FormField("cv", "Upload your CV", True, kind="file")])

    result = process_application(repository, application_id, browser)

    assert result == "NEEDS_USER"
    assert browser.submitted == 0
    assert repository.application(application_id)["queue_state"] == "WAITING_USER"
    repository.connection.close()


def test_runner_stops_if_cv_bytes_change_while_the_form_is_being_filled(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)
    cv_path = tmp_path / "resume.pdf"
    cv_path.write_bytes(b"approved resume bytes")
    repository.update_application_cv_path(application_id, str(cv_path))
    changed = False

    def change_cv_after_field_fill():
        nonlocal changed
        if not changed:
            cv_path.write_bytes(b"different resume bytes")
            changed = True

    browser = FakeBrowser(
        _basic_fields() + [FormField("cv", "Upload your CV", True, kind="file")],
        on_fill=change_cv_after_field_fill,
    )

    result = process_application(repository, application_id, browser)

    assert result == "NEEDS_USER"
    assert browser.submitted == 0
    assert repository.application(application_id)["queue_state"] == "WAITING_USER"
    repository.connection.close()


def test_smart_approval_requeues_review_if_cv_changes_during_form_fill(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)
    repository.set_setting("application_mode", "smart_approval")
    repository.revoke_autopilot("Switch to Smart Approval")
    cv_path = tmp_path / "approved.pdf"
    cv_path.write_bytes(b"approved resume bytes")
    repository.update_application_cv_path(application_id, str(cv_path))
    fields = _basic_fields() + [FormField("cv", "Upload your CV", True, kind="file")]

    assert process_application(repository, application_id, FakeBrowser(fields)) == "NEEDS_REVIEW"
    original_review = repository.application_review(application_id)
    assert original_review is not None
    assert repository.approve_application_review(application_id, original_review["package_hash"])
    changed = False

    def change_cv_after_field_fill():
        nonlocal changed
        if not changed:
            cv_path.write_bytes(b"updated resume bytes")
            changed = True

    browser = FakeBrowser(fields, on_fill=change_cv_after_field_fill)
    result = process_application(repository, application_id, browser)
    updated_review = repository.application_review(application_id)

    assert result == "NEEDS_REVIEW"
    assert browser.submitted == 0
    assert updated_review is not None
    assert updated_review["package_hash"] != original_review["package_hash"]
    assert updated_review["package"]["cv"]["sha256"] == sha256(b"updated resume bytes").hexdigest()
    assert updated_review["state"] == "WAITING"
    repository.connection.close()


def test_exact_package_approval_survives_repository_restart(tmp_path):
    database_path = tmp_path / "automation.db"
    repository, application_id = _authorized_repository(tmp_path)
    repository.set_setting("application_mode", "smart_approval")
    repository.revoke_autopilot("Switch to Smart Approval")
    cv_path = tmp_path / "approved.pdf"
    cv_path.write_bytes(b"approved resume bytes")
    repository.update_application_cv_path(application_id, str(cv_path))
    fields = _basic_fields() + [FormField("cv", "Upload your CV", True, kind="file")]

    assert process_application(repository, application_id, FakeBrowser(fields)) == "NEEDS_REVIEW"
    review = repository.application_review(application_id)
    assert review is not None
    assert repository.approve_application_review(application_id, review["package_hash"])
    package_hash = review["package_hash"]
    repository.connection.close()

    repository = Repository(database_path)
    repository.initialize()
    assert repository.application_review_approved(application_id, package_hash)

    browser = FakeBrowser(fields)
    result = process_application(repository, application_id, browser)

    assert result == "APPLIED"
    assert browser.submitted == 1
    repository.connection.close()


def test_restart_with_changed_form_requires_a_new_exact_package_review(tmp_path):
    database_path = tmp_path / "automation.db"
    repository, application_id = _authorized_repository(tmp_path)
    repository.set_setting("application_mode", "smart_approval")
    repository.revoke_autopilot("Switch to Smart Approval")
    original_fields = _basic_fields()

    assert process_application(repository, application_id, FakeBrowser(original_fields)) == "NEEDS_REVIEW"
    review = repository.application_review(application_id)
    assert review is not None
    assert repository.approve_application_review(application_id, review["package_hash"])
    approved_hash = review["package_hash"]
    repository.connection.close()

    repository = Repository(database_path)
    repository.initialize()
    assert repository.application_review_approved(application_id, approved_hash)
    changed_browser = FakeBrowser(original_fields + [FormField("portfolio", "Portfolio link", False)])

    result = process_application(repository, application_id, changed_browser)
    changed_review = repository.application_review(application_id)

    assert result == "NEEDS_REVIEW"
    assert changed_browser.submitted == 0
    assert changed_review is not None
    assert changed_review["package_hash"] != approved_hash
    assert changed_review["state"] == "WAITING"
    repository.connection.close()


def test_runner_passes_the_reviewed_cv_checksum_to_the_final_submit_guard(tmp_path):
    repository, application_id = _authorized_repository(tmp_path)
    cv_path = tmp_path / "approved.pdf"
    cv_path.write_bytes(b"approved resume bytes")
    repository.update_application_cv_path(application_id, str(cv_path))

    class ChangedUploadBeforeSubmitBrowser(FakeBrowser):
        def submit(self, url, *, expected_signature=None, expected_uploads=None, pre_click_check=None):
            self.inspection = replace(
                self.inspection,
                uploaded_files={"cv": {"name": "replacement.pdf", "sha256": "changed-hash"}},
            )
            if expected_uploads is None:
                return super().submit(url, expected_signature=expected_signature, expected_uploads=expected_uploads, pre_click_check=pre_click_check)
            assert expected_uploads == {
                "cv": {"name": "approved.pdf", "sha256": sha256(b"approved resume bytes").hexdigest()}
            }
            reviewed_file = (expected_uploads or {}).get("cv")
            if reviewed_file != self.inspection.uploaded_files["cv"]:
                return SubmissionResult(
                    False,
                    True,
                    "The selected CV file changed after review; no submit action was taken.",
                    final_url=url,
                )
            return super().submit(url, expected_signature=expected_signature, expected_uploads=expected_uploads)

    browser = ChangedUploadBeforeSubmitBrowser(
        _basic_fields() + [FormField("cv", "Upload your CV", True, kind="file")]
    )

    result = process_application(repository, application_id, browser)

    assert result == "NEEDS_USER"
    assert browser.submitted == 0
    assert repository.application(application_id)["queue_state"] == "WAITING_USER"
    repository.connection.close()


def test_synthetic_employer_e2e_posts_exact_cv_once_and_records_same_origin_receipt(tmp_path):
    from datetime import datetime, timedelta, timezone
    from email.parser import BytesParser
    from email.policy import default
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import socket
    import ssl
    import threading
    from urllib.parse import urlsplit
    import pytest

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from playwright.sync_api import Error as PlaywrightError, sync_playwright
    from reportlab.pdfgen.canvas import Canvas
    from pypdf import PdfReader

    from sampoagent.agents.playwright_adapter import PlaywrightBrowserAgent
    from sampoagent.agents.pinned_proxy import PinnedHttpsProxy
    from sampoagent.applications.packages import enqueue_eligible_applications

    repository = Repository(":memory:")
    repository.initialize()
    repository.load_demo()
    repository.add_target_occupation("Warehouse Worker", "Varastotyöntekijä")
    repository.set_setting("application_mode", "autopilot")
    repository.set_setting("daily_limit", "5")
    repository.set_setting("dry_run", "false")
    repository.set_setting("automation_paused", "false")
    repository.grant_autopilot()
    weak_template = tmp_path / "old-general-cv.pdf"
    canvas = Canvas(str(weak_template))
    canvas.drawString(50, 780, "Old general CV")
    canvas.save()
    repository.archive_cv(
        path=str(weak_template), checksum=sha256(weak_template.read_bytes()).hexdigest(),
        language="en", role_family="universal", source_job_id=None,
        fit_score=30, ats_score=100, strategy="uploaded",
    )
    host = "careers.northstar-logistics.fi"
    html = """<!doctype html><meta charset="utf-8"><form method="post" enctype="multipart/form-data" action="/apply/warehouse">
      <label for="full-name">Full name</label><input id="full-name" name="full_name" required>
      <label for="email">Email address</label><input id="email" name="email" type="email" required>
      <label for="resume">Upload your CV</label><input id="resume" name="resume" type="file" accept="application/pdf,.pdf" required>
      <button type="submit">Apply</button></form>"""
    captured_gets = []
    captured_posts = []
    response_body = b"<!doctype html><h1>Application received</h1>"

    class SyntheticEmployer(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            return

        def do_GET(self):
            if self.path != "/apply/warehouse":
                self.send_error(404)
                return
            captured_gets.append(self.path)
            payload = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            content_length = int(self.headers.get("Content-Length", "0"))
            captured_posts.append((self.path, dict(self.headers.items()), self.rfile.read(content_length)))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    certificate_path = tmp_path / "synthetic-employer.crt"
    key_path = tmp_path / "synthetic-employer.key"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    server = ThreadingHTTPServer(("127.0.0.1", 0), SyntheticEmployer)
    server.daemon_threads = True
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(certificate_path, key_path)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    application_url = f"https://{host}:{server.server_port}/apply/warehouse"
    proxy_connections = []
    pinned_ip = "93.184.216.34"

    def fixture_resolver(requested_host, port, *, type):
        assert requested_host == host
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (pinned_ip, port))]

    def fixture_connector(address, *, timeout):
        proxy_connections.append(address)
        assert address == (pinned_ip, server.server_port)
        return socket.create_connection(("127.0.0.1", server.server_port), timeout=timeout)

    proxy = PinnedHttpsProxy(application_url, resolver=fixture_resolver, connector=fixture_connector)
    proxy.start()
    repository.connection.execute("UPDATE jobs SET application_url=? WHERE id=1", (application_url,))
    repository.connection.commit()
    repository.mark_job_user_reviewed(1, reviewed_current=True)
    assert enqueue_eligible_applications(repository, tmp_path / "application-data") == 1
    application_row = repository.connection.execute("SELECT * FROM applications").fetchone()
    assert application_row is not None
    application_id = int(application_row["id"])
    application = repository.application(application_id)
    archived_cv = next(item for item in repository.cv_archives() if item["source_job_id"] == 1)
    cv_path = Path(str(application["cv_path"]))
    cv_bytes = cv_path.read_bytes()
    generated_text = "\n".join(page.extract_text() or "" for page in PdfReader(str(cv_path)).pages)
    assert archived_cv["strategy"] == "generated"
    assert sha256(cv_bytes).hexdigest() == archived_cv["checksum"]
    assert "Target role: Warehouse Worker" in generated_text
    assert "forklift operation" in generated_text
    assert "Old general CV" not in generated_text

    try:
        with sync_playwright() as playwright:
            chromium = playwright.chromium.launch(
                headless=True,
                args=[
                    f"--host-resolver-rules=MAP {host} ~NOTFOUND",
                ],
                proxy=proxy.playwright_proxy,
            )
            context = chromium.new_context(ignore_https_errors=True)
            page = context.new_page()

            def contain_fixture_egress(route):
                destination = urlsplit(route.request.url)
                if (destination.scheme, destination.hostname, destination.port) == (
                    "https", host, server.server_port,
                ):
                    route.continue_()
                else:
                    route.abort("blockedbyclient")

            context.route("**/*", contain_fixture_egress)
            browser = PlaywrightBrowserAgent(tmp_path / "browser-profile")
            browser._egress_proxy = proxy
            browser._context = context
            browser._page = page
            try:
                result = process_application(repository, application_id, browser)
                # With the proxy gone, the unavailable browser-side hostname
                # resolution prevents a direct connection from escaping.
                proxy.close()
                with pytest.raises(PlaywrightError):
                    page.goto(application_url, timeout=3000)
                assert captured_gets == ["/apply/warehouse"]
            finally:
                browser.close()
                chromium.close()
    finally:
        proxy.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)

    application = repository.application(application_id)
    attempts = repository.connection.execute(
        "SELECT state FROM application_attempts WHERE application_id=?",
        (application_id,),
    ).fetchall()
    assert result == "APPLIED", application["notes"]
    assert application["status"] == "APPLIED"
    assert len(attempts) == 1
    assert attempts[0]["state"] == "SUBMITTED"
    assert len(captured_posts) == 1
    assert captured_gets == ["/apply/warehouse"]
    assert proxy_connections

    post_path, headers, body = captured_posts[0]
    assert post_path == "/apply/warehouse"
    message = BytesParser(policy=default).parsebytes(
        f"Content-Type: {headers['Content-Type']}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body
    )
    submitted_fields = {}
    uploaded_parts = []
    for part in message.iter_parts():
        field_name = part.get_param("name", header="content-disposition")
        payload = part.get_payload(decode=True) or b""
        if part.get_filename():
            uploaded_parts.append((part.get_filename(), payload))
        elif field_name:
            submitted_fields[field_name] = payload.decode("utf-8")

    assert submitted_fields == {"full_name": "Aino Example", "email": "aino@example.test"}
    assert len(uploaded_parts) == 1
    assert uploaded_parts[0][0] == cv_path.name
    assert sha256(uploaded_parts[0][1]).hexdigest() == sha256(cv_bytes).hexdigest()
    evidence = repository.submission_evidence_for_application(application_id)
    assert evidence[0]["final_url"] == application_url

    repository.connection.close()
