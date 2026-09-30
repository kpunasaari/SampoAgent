from sampoagent.applications.workflow import ApplicationMode, can_submit, classify_question
from sampoagent.db.repository import Repository


def test_autopilot_sends_without_per_job_prompt_only_inside_grant_scope():
    assert can_submit(
        ApplicationMode.AUTOPILOT,
        dry_run=False,
        risk="LOW",
        applied_today=0,
        daily_limit=5,
        autopilot_authorized=True,
        within_scope=True,
        required_answers_resolved=True,
        job_active=True,
        duplicate=False,
        captcha_detected=False,
        paused=False,
    )


def test_medium_risk_confirmed_fields_are_autopilot_only():
    common = dict(
        dry_run=False,
        risk="MEDIUM",
        applied_today=0,
        daily_limit=5,
        autopilot_authorized=True,
        within_scope=True,
        required_answers_resolved=True,
        job_active=True,
        duplicate=False,
        captcha_detected=False,
        paused=False,
    )
    assert can_submit(ApplicationMode.AUTOPILOT, **common)
    assert not can_submit(ApplicationMode.SMART_APPROVAL, **common)
    assert can_submit(ApplicationMode.SMART_APPROVAL, **(common | {"review_approved": True}))
    assert can_submit(ApplicationMode.SMART_APPROVAL, **(common | {"risk": "LOW", "review_approved": True}))
    assert not can_submit(ApplicationMode.SMART_APPROVAL, **(common | {"risk": "LOW", "review_approved": False}))


def test_autopilot_never_submits_high_risk_declarations_even_with_grant():
    assert not can_submit(
        ApplicationMode.AUTOPILOT,
        dry_run=False,
        risk="HIGH",
        applied_today=0,
        daily_limit=5,
        autopilot_authorized=True,
        within_scope=True,
        required_answers_resolved=True,
        job_active=True,
        duplicate=False,
        captcha_detected=False,
        paused=False,
    )


def test_finnish_and_swedish_work_permission_prompts_are_high_risk():
    for label in (
        'Onko sinulla oikeus työskennellä Suomessa?',
        'Har du rätt att arbeta i Finland?',
    ):
        assert classify_question(label) == 'HIGH'


def test_assessment_adjustment_demographic_and_privacy_prompts_are_high_risk_in_all_ui_languages():
    for label in (
        'Are you willing to complete this assessment?',
        'Suostutko soveltuvuusarviointiin?',
        'Är du villig att göra lämplighetstestet?',
        'Do you need an adjustment to the recruitment process?',
        'Tarvitsetko mukautuksia rekrytointiprosessiin?',
        'Behöver du anpassning av rekryteringsprocessen?',
        'Gender (optional)',
        'Sukupuoli (vapaaehtoinen)',
        'Kön (frivilligt)',
        'Do you consent to the privacy notice and data retention terms?',
        'Hyväksytkö tietosuojaselosteen ja tietojen säilytyksen?',
        'Godkänner du integritetspolicyn och datalagringen?',
    ):
        assert classify_question(label) == 'HIGH'


def test_autopilot_pauses_for_captcha_missing_answers_scope_and_limit():
    common = dict(
        mode=ApplicationMode.AUTOPILOT,
        dry_run=False,
        risk="LOW",
        applied_today=0,
        daily_limit=5,
        autopilot_authorized=True,
        within_scope=True,
        required_answers_resolved=True,
        job_active=True,
        duplicate=False,
        captcha_detected=False,
        paused=False,
    )
    assert not can_submit(**(common | {"captcha_detected": True}))
    assert not can_submit(**(common | {"required_answers_resolved": False}))
    assert not can_submit(**(common | {"within_scope": False}))
    assert not can_submit(**(common | {"duplicate": True}))
    assert not can_submit(**(common | {"paused": True}))
    assert not can_submit(**(common | {"daily_limit": 0}))


def test_captcha_tasks_accumulate_and_are_handled_one_at_a_time():
    repository = Repository(":memory:")
    repository.initialize()
    repository.load_demo()
    first = repository.queue_application(1, language="en", cv_path=None)
    second = repository.queue_application(2, language="fi", cv_path=None)
    repository.hold_for_captcha(first, detected_url="https://example.test/apply/1")
    repository.hold_for_captcha(second, detected_url="https://example.test/apply/2")
    assert [task["application_id"] for task in repository.captcha_tasks()] == [first, second]
    repository.begin_captcha_task(first)
    try:
        repository.begin_captcha_task(second)
    except ValueError as error:
        assert "one at a time" in str(error)
    else:
        raise AssertionError("A second CAPTCHA task started while the first was active")
    repository.finish_captcha_task(first, outcome="submitted", confirmation_message="Application received")
    assert repository.application(first)["status"] == "APPLIED_MANUAL"
    assert repository.application(second)["status"] == "CAPTCHA_HOLD"
    repository.connection.close()


def test_concurrent_captcha_completion_records_one_terminal_outcome_and_one_receipt(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    database_path = tmp_path / "captcha-race.db"
    repository = Repository(database_path)
    repository.initialize()
    repository.load_demo()
    application_id = repository.queue_application(1, language="en", cv_path=None)
    task_id = repository.hold_for_captcha(
        application_id, detected_url="https://careers.example.fi/apply/1",
    )
    repository.begin_captcha_task(task_id)
    competing_repository = Repository(database_path)
    transaction_barrier = Barrier(2)

    def synchronize_completion_transaction(connection):
        state = {"blocked": False}

        def trace(sql):
            if not state["blocked"] and sql == "BEGIN IMMEDIATE":
                state["blocked"] = True
                transaction_barrier.wait(timeout=5)

        connection.set_trace_callback(trace)

    synchronize_completion_transaction(repository.connection)
    synchronize_completion_transaction(competing_repository.connection)

    def finish(instance):
        try:
            instance.finish_captcha_task(
                task_id, outcome="submitted", confirmation_message="Application received",
            )
            return "completed"
        except ValueError:
            return "already_completed"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(finish, (repository, competing_repository)))

    assert sorted(results) == ["already_completed", "completed"]
    assert repository.connection.execute(
        "SELECT COUNT(*) FROM submission_evidence WHERE application_id=?", (application_id,),
    ).fetchone()[0] == 1
    assert repository.application(application_id)["status"] == "APPLIED_MANUAL"
    assert repository.connection.execute(
        "SELECT outcome FROM captcha_tasks WHERE id=?", (task_id,),
    ).fetchone()[0] == "submitted"
    repository.connection.close()
    competing_repository.connection.close()


def test_full_autopilot_requires_explicit_authorization_and_daily_limit(tmp_path):
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    app = create_app(database_path=str(tmp_path / "settings.db"), demo_data=True)
    client = TestClient(app)
    settings_page = client.get("/settings").text
    assert "explicitly select at least one target occupation" in settings_page
    assert "Next/Continue steps may save candidate information page by page" in settings_page
    payload = {"application_mode": "autopilot", "daily_limit": "4", "dry_run": "false", "ai_usage_mode": "minimal"}
    client.post("/settings", data=payload)
    assert app.state.repository.setting("dry_run") == "true"
    assert app.state.repository.setting("autopilot_authorized") == "false"
    client.post("/settings", data=payload | {"autopilot_ack": "yes"})
    assert app.state.repository.setting("dry_run") == "false"
    assert app.state.repository.setting("autopilot_authorized") == "true"
    client.post("/settings", data=payload)
    assert app.state.repository.setting("dry_run") == "true"
    assert app.state.repository.setting("autopilot_authorized") == "false"


def test_legacy_autopilot_grant_is_invalidated_when_stepwise_save_consent_changes(tmp_path):
    repository = Repository(tmp_path / "legacy-grant.db")
    repository.initialize()
    repository.load_demo()
    repository.add_target_occupation("Warehouse Worker", "Varastotyöntekijä")
    repository.set_setting("application_mode", "autopilot")
    repository.set_setting("daily_limit", "2")
    repository.set_setting("dry_run", "false")
    repository.grant_autopilot()
    assert repository.autopilot_authorized() is True
    repository.set_setting("autopilot_grant_policy_version", "legacy")

    assert repository.autopilot_authorized() is False
    repository.connection.close()


def test_autopilot_grant_requires_a_user_selected_target_or_explicit_search_scope():
    repository = Repository(":memory:")
    repository.initialize()
    repository.save_profile("Synthetic Candidate", "en")
    repository.set_setting("application_mode", "autopilot")
    repository.set_setting("daily_limit", "2")
    repository.set_setting("dry_run", "false")

    try:
        repository.grant_autopilot()
    except ValueError as error:
        assert "target occupation" in str(error).casefold() or "search scope" in str(error).casefold()
    else:
        raise AssertionError("Autopilot must require an explicit role/search scope")

    repository.connection.close()


def test_autopilot_grant_cannot_exceed_thirty_days():
    repository = Repository(":memory:")
    repository.initialize()
    repository.save_profile("Synthetic Candidate", "en")
    repository.add_target_occupation("Warehouse Worker", "Varastotyöntekijä")
    repository.set_setting("application_mode", "autopilot")
    repository.set_setting("daily_limit", "2")
    repository.set_setting("dry_run", "false")

    try:
        repository.grant_autopilot(days=31)
    except ValueError as error:
        assert "30 days" in str(error)
    else:
        raise AssertionError("Autopilot authorization must expire within 30 days")

    repository.connection.close()


def test_captcha_ui_requires_manual_single_item_completion():
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:", demo_data=True))
    client.post("/queue/prepare/1")
    client.post("/queue/1/captcha")
    client.post("/captcha/1/start")
    page = client.get("/captcha")
    assert "Open official application" in page.text
    assert "Complete its CAPTCHA yourself" in page.text
    assert "I submitted and saw confirmation" in page.text


def test_captcha_queue_offers_only_one_waiting_task_until_it_is_finished():
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    app = create_app(database_path=":memory:", demo_data=True)
    repository = app.state.repository
    first_application = repository.queue_application(1, language="en", cv_path=None)
    second_application = repository.queue_application(2, language="fi", cv_path=None)
    first_task = repository.hold_for_captcha(
        first_application, detected_url="https://example.test/apply/1",
    )
    second_task = repository.hold_for_captcha(
        second_application, detected_url="https://example.test/apply/2",
    )
    client = TestClient(app, follow_redirects=False)

    waiting_page = client.get("/captcha").text
    assert f"action='/captcha/{first_task}/start'" in waiting_page
    assert f"action='/captcha/{second_task}/start'" not in waiting_page
    assert "href='https://example.test/apply/1'" not in waiting_page
    assert "href='https://example.test/apply/2'" not in waiting_page
    assert "Waiting for the current CAPTCHA task to finish" in waiting_page

    assert client.post(f"/captcha/{first_task}/start").status_code == 303
    active_page = client.get("/captcha").text
    assert f"action='/captcha/{first_task}/finish'" in active_page
    assert f"action='/captcha/{second_task}/start'" not in active_page
    assert "href='https://example.test/apply/1'" in active_page
    assert "href='https://example.test/apply/2'" not in active_page

    assert client.post(
        f"/captcha/{first_task}/finish",
        data={"outcome": "skip", "confirmation_message": ""},
    ).status_code == 303
    next_page = client.get("/captcha").text
    assert f"action='/captcha/{second_task}/start'" in next_page
    assert "href='https://example.test/apply/2'" not in next_page

    assert client.post(f"/captcha/{second_task}/start").status_code == 303
    next_active_page = client.get("/captcha").text
    assert f"action='/captcha/{second_task}/finish'" in next_active_page
    assert "href='https://example.test/apply/2'" in next_active_page


def test_dashboard_calls_for_user_help_when_captcha_tasks_are_waiting():
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    app = create_app(database_path=":memory:", demo_data=True)
    repository = app.state.repository
    application_id = repository.queue_application(1, language="en", cv_path=None)
    repository.hold_for_captcha(application_id, detected_url="https://example.test/apply/1")

    page = TestClient(app).get("/")

    assert "1 CAPTCHA task needs your help" in page.text
    assert "href='/captcha'>Handle CAPTCHA tasks" in page.text


def test_submission_result_pauses_for_captcha_and_never_retries_unknown_outcome():
    from sampoagent.agents.browser import SubmissionResult
    from sampoagent.applications.workflow import record_submission_result

    repository = Repository(":memory:")
    repository.initialize()
    repository.load_demo()
    application_id = repository.queue_application(1, language="en", cv_path=None)
    captcha = SubmissionResult(False, True, "Challenge detected", "https://example.test/apply/1", captcha_detected=True)
    assert record_submission_result(repository, application_id, captcha) == "CAPTCHA_HOLD"
    task = repository.captcha_tasks()[0]
    assert task["state"] == "WAITING_USER"

    other = repository.queue_application(2, language="en", cv_path=None)
    uncertain = SubmissionResult(False, True, "Connection dropped", "https://example.test/apply/2", outcome_unknown=True)
    assert record_submission_result(repository, other, uncertain) == "SUBMITTED_UNVERIFIED"
    assert repository.application(other)["queue_state"] == "DO_NOT_RETRY"


def test_application_claim_is_unique_even_if_reached_concurrently():
    repository = Repository(":memory:")
    repository.initialize()
    repository.load_demo()
    repository.queue_application(1, language="en", cv_path=None)
    try:
        repository.queue_application(1, language="en", cv_path=None)
    except ValueError as error:
        assert "already exists" in str(error)
    else:
        raise AssertionError("The same job was reserved twice")


def test_application_outcomes_update_a_role_specific_learning_prior_only_after_evidence():
    repository = Repository(":memory:")
    repository.initialize()
    repository.load_demo()
    job_ids = [1, 2]
    for index in range(3, 8):
        cursor = repository.connection.execute("INSERT INTO jobs(title, company, location, language, description, application_url, fingerprint, verification_state) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ("Warehouse Operator", f"Example {index}", "Vantaa", "en", "Forklift operation", f"https://example.test/{index}", f"learn-{index}", "VERIFIED"))
        repository.connection.commit()
        job_ids.append(int(cursor.lastrowid))
    apps = [repository.queue_application(job_id, language="en", cv_path=None) for job_id in job_ids]
    assert repository.learning_adjustment("warehouse_logistics") == 0
    for application_id in apps:
        repository.update_application_status(application_id, "INTERVIEW", "Candidate confirmed outcome")
    assert repository.learning_adjustment("warehouse_logistics") > 0
    assert repository.count("application_learning") == 7
