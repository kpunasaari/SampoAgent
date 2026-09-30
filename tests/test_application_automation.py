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


def test_multistep_schema_pauses_before_any_candidate_value_is_entered(tmp_path):
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
    assert "multi-step" in repository.application(application_id)["notes"]
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
    from email.parser import BytesParser
    from email.policy import default

    from playwright.sync_api import sync_playwright

    from sampoagent.agents.playwright_adapter import PlaywrightBrowserAgent

    repository, application_id = _authorized_repository(tmp_path)
    cv_path = tmp_path / "approved-resume.pdf"
    cv_bytes = b"%PDF-1.4\nsynthetic reviewed candidate CV\n%%EOF"
    cv_path.write_bytes(cv_bytes)
    repository.update_application_cv_path(application_id, str(cv_path))
    application_url = str(repository.job(1)["application_url"])
    html = """<!doctype html><meta charset="utf-8"><form method="post" enctype="multipart/form-data" action="/apply/warehouse">
      <label for="full-name">Full name</label><input id="full-name" name="full_name" required>
      <label for="email">Email address</label><input id="email" name="email" type="email" required>
      <label for="resume">Upload your CV</label><input id="resume" name="resume" type="file" accept="application/pdf,.pdf" required>
      <button type="submit">Apply</button></form>
      <script>
        document.querySelector('form').addEventListener('submit', async event => {
          event.preventDefault();
          const form = event.currentTarget;
          const values = new FormData(form);
          const file = values.get('resume');
          const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
          const sha256 = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
          await window.recordSyntheticForm({
            fullName: values.get('full_name'), email: values.get('email'),
            fileName: file.name, fileSize: file.size, fileSha256: sha256,
          });
          HTMLFormElement.prototype.submit.call(form);
        }, { once: true });
      </script>"""
    captured_posts = []
    captured_forms = []

    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch(headless=True)
        page = chromium.new_page()
        page.expose_function("recordSyntheticForm", lambda data: captured_forms.append(data))

        def employer_page(route):
            request = route.request
            if request.method == "POST":
                captured_posts.append((dict(request.headers), request.post_data_buffer))
                route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="<!doctype html><h1>Application received</h1>",
                )
            else:
                route.fulfill(status=200, content_type="text/html", body=html)

        page.route("https://careers.northstar-logistics.fi/**", employer_page)
        browser = PlaywrightBrowserAgent(tmp_path / "browser-profile")
        browser._context = chromium
        browser._page = page
        try:
            result = process_application(repository, application_id, browser)
        finally:
            browser.close()

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
    assert captured_forms == [{
        "fullName": "Aino Example",
        "email": "aino@example.test",
        "fileName": cv_path.name,
        "fileSize": len(cv_bytes),
        "fileSha256": sha256(cv_bytes).hexdigest(),
    }]

    headers, body = captured_posts[0]
    message = BytesParser(policy=default).parsebytes(
        f"Content-Type: {headers['content-type']}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body
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
    evidence = repository.submission_evidence_for_application(application_id)
    assert evidence[0]["final_url"] == application_url
    repository.connection.close()
