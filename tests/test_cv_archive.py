from pathlib import Path
from hashlib import sha256

from sampoagent.cv.archive import choose_application_cv


def test_reuses_a_matching_archived_cv_only_when_role_language_and_requirements_fit(tmp_path: Path):
    template = tmp_path / "warehouse-fi.pdf"
    from reportlab.pdfgen.canvas import Canvas

    canvas = Canvas(str(template))
    canvas.drawString(50, 780, "Varastotyöntekijä")
    canvas.drawString(50, 750, "B-ajokortti trukkikokemus suomi")
    canvas.drawString(50, 720, "Aino Example")
    canvas.drawString(50, 690, "aino@example.test")
    canvas.save()
    choice = choose_application_cv(
        output_dir=tmp_path / "archive",
        archived=[{"path": str(template), "checksum": sha256(template.read_bytes()).hexdigest(), "language": "fi", "role_family": "warehouse_logistics"}],
        job={"id": 4, "title": "Varastotyöntekijä", "description": "B-ajokortti vaaditaan. Trukkikokemus toivotaan.", "language": "fi"},
        candidate={"name": "Aino Example", "email": "aino@example.test"},
        facts=[{"type": "skill", "value": "trukkikokemus", "confirmed": True}],
        records={},
    )
    assert choice.strategy == "reused"
    assert choice.path == template
    assert choice.score >= 85


def test_does_not_reuse_archived_cv_with_stale_candidate_identity(tmp_path: Path):
    """An otherwise matching template must not carry an old candidate name or email forward."""
    from reportlab.pdfgen.canvas import Canvas
    from pypdf import PdfReader

    template = tmp_path / "old-warehouse-cv.pdf"
    canvas = Canvas(str(template))
    canvas.drawString(50, 780, "Warehouse Worker")
    canvas.drawString(50, 750, "Forklift operation")
    canvas.drawString(50, 720, "Old Candidate")
    canvas.drawString(50, 690, "old@example.test")
    canvas.save()

    choice = choose_application_cv(
        output_dir=tmp_path / "generated",
        archived=[{
            "path": str(template), "checksum": sha256(template.read_bytes()).hexdigest(),
            "language": "en", "role_family": "warehouse_logistics", "text_check_score": 100,
        }],
        job={"id": 41, "title": "Warehouse Worker", "description": "Forklift operation preferred.", "language": "en"},
        candidate={"name": "Current Candidate", "email": "current@example.test"},
        facts=[{"type": "skill", "value": "Forklift operation", "confirmed": True}],
        records={},
    )

    assert choice.strategy == "generated"
    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(choice.path)).pages)
    assert "Current Candidate" in text
    assert "current@example.test" in text
    assert "Old Candidate" not in text
    assert "old@example.test" not in text


def test_generates_and_archives_when_existing_cv_misses_required_job_evidence(tmp_path: Path):
    template = tmp_path / "weak.pdf"
    from reportlab.pdfgen.canvas import Canvas

    canvas = Canvas(str(template))
    canvas.drawString(50, 780, "Old general CV")
    canvas.save()
    choice = choose_application_cv(
        output_dir=tmp_path / "archive",
        archived=[{"path": str(template), "language": "fi", "role_family": "cleaning_facilities"}],
        job={"id": 7, "title": "Siivooja", "description": "Hygieniapassi eduksi. Siivouskokemus.", "language": "fi"},
        candidate={"name": "Aino Example", "email": "aino@example.test"},
        facts=[
            {"type": "skill", "value": "siivouskokemus", "confirmed": True},
            {"type": "certificate", "value": "Hygieniapassi", "confirmed": False},
        ],
        records={"experience": [{"title": "Siivooja", "details": "School cleaning"}]},
    )
    assert choice.strategy == "generated"
    assert choice.path.is_file()
    assert choice.path != template
    from pypdf import PdfReader

    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(choice.path)).pages)
    assert "Siivooja" in text
    assert "siivouskokemus" in text
    assert "Hygieniapassi" not in text
    assert choice.checksum


def test_queue_preparation_creates_and_archives_an_automatic_job_cv(tmp_path):
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    app = create_app(database_path=":memory:", demo_data=True, storage_dir=tmp_path)
    client = TestClient(app)
    response = client.post("/queue/prepare/1", follow_redirects=True)
    application = app.state.repository.application(1)
    archive = app.state.repository.cv_archives()
    assert response.status_code == 200
    assert application["cv_path"]
    assert Path(application["cv_path"]).is_file()
    assert Path(application["cv_path"]).parent.name == "1"
    assert archive[0]["source_job_id"] == 1
    assert archive[0]["strategy"] == "generated"
    assert "Tailored CV" in application["notes"]


def test_autopilot_enqueue_uses_verified_snapshot_and_archives_its_tailored_cv(tmp_path: Path):
    from sampoagent.applications.packages import enqueue_eligible_applications
    from sampoagent.db.repository import Repository

    repository = Repository(":memory:")
    repository.initialize()
    repository.load_demo()
    repository.add_target_occupation("Warehouse Worker", "Varastotyöntekijä")
    repository.connection.execute(
        "UPDATE jobs SET application_url=? WHERE id=1",
        ("https://careers.northstar-logistics.fi/apply/warehouse",),
    )
    repository.connection.commit()
    repository.mark_job_user_reviewed(1, reviewed_current=True)
    repository.set_setting("application_mode", "autopilot")
    repository.set_setting("daily_limit", "5")
    repository.set_setting("dry_run", "false")
    repository.set_setting("automation_paused", "false")
    repository.grant_autopilot()

    queued = enqueue_eligible_applications(repository, tmp_path / "application-data")

    applications = repository.connection.execute("SELECT * FROM applications").fetchall()
    assert queued == 1
    assert len(applications) == 1
    application = dict(applications[0])
    archive = repository.cv_archives()
    assert application["job_id"] == 1
    assert application["cv_path"]
    assert Path(application["cv_path"]).is_file()
    assert archive[0]["strategy"] == "generated"
    assert archive[0]["source_job_id"] == 1
    assert sha256(Path(application["cv_path"]).read_bytes()).hexdigest() == archive[0]["checksum"]
    assert "Tailored CV generated" in application["notes"]
    assert "CV text check" in application["notes"]
    assert "ATS text check" not in application["notes"]
    repository.connection.close()


def test_manual_generated_cvs_have_distinct_archived_paths(tmp_path):
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    app = create_app(database_path=":memory:", demo_data=True, storage_dir=tmp_path)
    client = TestClient(app)
    client.post("/cvs/generate", data={"language": "en", "role_family": "warehouse_logistics"})
    client.post("/cvs/generate", data={"language": "en", "role_family": "warehouse_logistics"})
    archived = app.state.repository.cv_archives()
    assert len(archived) == 2
    assert archived[0]["path"] != archived[1]["path"]


def test_archived_cv_is_not_reused_after_its_bytes_change(tmp_path: Path):
    from reportlab.pdfgen.canvas import Canvas

    template = tmp_path / "warehouse.pdf"
    canvas = Canvas(str(template))
    canvas.drawString(50, 780, "Warehouse Worker")
    canvas.drawString(50, 750, "Forklift operation")
    canvas.save()
    original_checksum = sha256(template.read_bytes()).hexdigest()

    canvas = Canvas(str(template))
    canvas.drawString(50, 780, "Warehouse Worker")
    canvas.drawString(50, 750, "Forklift operation plus unreviewed change")
    canvas.save()

    choice = choose_application_cv(
        output_dir=tmp_path / "generated",
        archived=[{
            "path": str(template), "checksum": original_checksum,
            "language": "en", "role_family": "warehouse_logistics", "text_check_score": 100,
        }],
        job={"id": 19, "title": "Warehouse Worker", "description": "Forklift operation preferred.", "language": "en"},
        candidate={"name": "Aino Example", "email": "aino@example.test"},
        facts=[{"type": "skill", "value": "Forklift operation", "confirmed": True}],
        records={},
    )

    assert choice.strategy == "generated"
    assert choice.checksum == sha256(choice.path.read_bytes()).hexdigest()


def test_user_selected_archived_cv_also_requires_its_original_checksum(tmp_path: Path):
    from sampoagent.applications.packages import prepare_job_cv
    from sampoagent.db.repository import Repository

    repository = Repository(":memory:")
    repository.initialize()
    repository.load_demo()
    cv = tmp_path / "candidate-template.pdf"
    cv.write_bytes(b"original reviewed bytes")
    repository.archive_cv(
        path=str(cv), checksum=sha256(cv.read_bytes()).hexdigest(), language="en",
        role_family="warehouse_logistics", source_job_id=None, fit_score=90,
        text_check_score=100, strategy="uploaded",
    )
    cv.write_bytes(b"modified after review")

    try:
        prepare_job_cv(repository, repository.job(1), tmp_path / "generated", requested_path=str(cv))
    except ValueError as error:
        assert "checksum" in str(error).casefold()
    else:
        raise AssertionError("A changed archived CV must not be prepared for an application")


def test_legacy_cv_archive_migration_marks_old_text_scores_unchecked(tmp_path: Path):
    import sqlite3
    from sampoagent.db.repository import Repository

    database_path = tmp_path / "legacy-cv-archive.db"
    connection = sqlite3.connect(database_path)
    connection.execute(
        "CREATE TABLE cv_archive (id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, checksum TEXT NOT NULL, "
        "language TEXT NOT NULL, role_family TEXT NOT NULL, source_job_id INTEGER, fit_score INTEGER NOT NULL DEFAULT 0, "
        "ats_score INTEGER NOT NULL DEFAULT 0, strategy TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO cv_archive(path, checksum, language, role_family, fit_score, ats_score, strategy, created_at) "
        "VALUES ('/local/legacy.pdf', 'old-hash', 'en', 'universal', 0, 0, 'uploaded', '2026-09-30T00:00:00+00:00')"
    )
    connection.commit()
    connection.close()

    repository = Repository(database_path)
    repository.initialize()

    archive = repository.cv_archives()
    assert len(archive) == 1
    assert archive[0]["ats_score"] == 0
    assert archive[0]["text_check_performed"] == 0
    repository.connection.close()


def test_cv_archive_renames_checked_legacy_score_and_accepts_new_text_check_name(tmp_path: Path):
    import sqlite3
    from sampoagent.db.repository import Repository

    database_path = tmp_path / "checked-legacy-cv-archive.db"
    connection = sqlite3.connect(database_path)
    connection.execute(
        "CREATE TABLE cv_archive (id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, checksum TEXT NOT NULL, "
        "language TEXT NOT NULL, role_family TEXT NOT NULL, source_job_id INTEGER, fit_score INTEGER NOT NULL DEFAULT 0, "
        "ats_score INTEGER NOT NULL DEFAULT 0, text_check_performed INTEGER NOT NULL DEFAULT 0, "
        "strategy TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO cv_archive(path, checksum, language, role_family, fit_score, ats_score, text_check_performed, strategy, created_at) "
        "VALUES ('/local/checked.pdf', 'old-hash', 'en', 'universal', 0, 88, 1, 'generated', '2026-09-30T00:00:00+00:00')"
    )
    connection.commit()
    connection.close()

    repository = Repository(database_path)
    repository.initialize()
    archive = repository.cv_archives()[0]

    assert archive["text_check_score"] == 88
    assert archive["text_check_performed"] == 1
    repository.archive_cv(
        path="/local/new.pdf", checksum="new-hash", language="en", role_family="universal",
        source_job_id=None, fit_score=0, text_check_score=67, strategy="generated",
    )
    added = next(item for item in repository.cv_archives() if item["path"] == "/local/new.pdf")
    assert added["text_check_score"] == 67
    assert added["text_check_performed"] == 1
    repository.archive_cv(
        path="/local/legacy-api.pdf", checksum="legacy-api-hash", language="en", role_family="universal",
        source_job_id=None, fit_score=0, ats_score=72, strategy="generated",
    )
    compatible = next(item for item in repository.cv_archives() if item["path"] == "/local/legacy-api.pdf")
    assert compatible["text_check_score"] == 72
    assert compatible["text_check_performed"] == 1
    repository.connection.close()


def test_confirmed_cv_outcomes_break_ties_only_between_qualified_templates(tmp_path: Path):
    from reportlab.pdfgen.canvas import Canvas

    templates = [tmp_path / "template-a.pdf", tmp_path / "template-b.pdf"]
    for path in templates:
        canvas = Canvas(str(path))
        canvas.drawString(50, 780, "Warehouse Worker")
        canvas.drawString(50, 750, "Forklift operation")
        canvas.drawString(50, 720, "Aino Example")
        canvas.drawString(50, 690, "aino@example.test")
        canvas.save()

    job = {"id": 20, "title": "Warehouse Worker", "description": "Forklift operation preferred.", "language": "en"}
    candidate = {"name": "Aino Example", "email": "aino@example.test"}
    facts = [{"type": "skill", "value": "Forklift operation", "confirmed": True}]
    archived = [
        {"path": str(path), "checksum": sha256(path.read_bytes()).hexdigest(), "language": "en", "role_family": "warehouse_logistics", "text_check_score": 100}
        for path in templates
    ]

    choice = choose_application_cv(
        output_dir=tmp_path / "generated", archived=archived, job=job,
        candidate=candidate, facts=facts, records={},
        learning_adjustments={archived[1]["checksum"]: 5},
    )

    assert choice.path == templates[1]
    assert "confirmed application outcomes" in " ".join(choice.reasons).casefold()


def test_cv_learning_adjustments_use_only_confirmed_outcome_records(tmp_path: Path):
    from sampoagent.db.repository import Repository

    repository = Repository(tmp_path / "learning.db")
    repository.initialize()
    repository.load_demo()
    cv = tmp_path / "successful-cv.pdf"
    cv.write_bytes(b"local synthetic CV")
    checksum = sha256(cv.read_bytes()).hexdigest()
    repository.archive_cv(
        path=str(cv), checksum=checksum, language="en", role_family="warehouse_logistics",
        source_job_id=1, fit_score=92, text_check_score=100, strategy="generated",
    )
    job_ids = [1]
    for index in range(4):
        cursor = repository.connection.execute(
            "INSERT INTO jobs(title, company, location, language, description, application_url, fingerprint, verification_state) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("Warehouse Worker", f"Local example {index}", "Vantaa", "en", "Forklift operation", f"https://example.test/learning/{index}", f"learning-{index}", "VERIFIED"),
        )
        job_ids.append(int(cursor.lastrowid))
    repository.connection.commit()
    for index, job_id in enumerate(job_ids):
        app_cv = tmp_path / f"application-{index}.pdf"
        app_cv.write_bytes(cv.read_bytes())
        application_id = repository.queue_application(job_id, language="en", cv_path=str(app_cv))
        repository.add_document(kind="application_cv", path=str(app_cv), checksum=checksum)
        repository.update_application_status(application_id, "INTERVIEW", "Candidate confirmed interview invitation")
        if index == 0:
            assert repository.cv_learning_adjustments()[checksum] == 3
        if index == 3:
            assert repository.cv_learning_adjustments()[checksum] > 0
    repository.update_application_status(application_id, "CAPTCHA_HOLD", "Not an outcome")

    adjustments = repository.cv_learning_adjustments()

    assert adjustments[checksum] == 8


def test_archived_cv_reuse_fit_includes_confirmed_experience_records(tmp_path: Path):
    from reportlab.pdfgen.canvas import Canvas

    template = tmp_path / "school-cleaner.pdf"
    canvas = Canvas(str(template))
    canvas.drawString(50, 780, "School Cleaner")
    canvas.drawString(50, 750, "School cleaning")
    canvas.drawString(50, 720, "Aino Example")
    canvas.drawString(50, 690, "aino@example.test")
    canvas.save()

    choice = choose_application_cv(
        output_dir=tmp_path / "generated",
        archived=[{
            "path": str(template), "checksum": sha256(template.read_bytes()).hexdigest(),
            "language": "en", "role_family": "cleaning_facilities", "text_check_score": 100,
        }],
        job={
            "id": 31, "title": "School Cleaner",
            "description": "School Cleaner with School cleaning experience preferred.", "language": "en",
        },
        candidate={"name": "Aino Example", "email": "aino@example.test"},
        facts=[],
        records={"experience": [{
            "review_state": "CONFIRMED", "title": "School Cleaner", "details": "School cleaning",
        }]},
    )

    assert choice.strategy == "reused"
    assert choice.path == template
    assert choice.score == 100


def test_unconfirmed_cv_history_is_not_used_in_generated_cv(tmp_path: Path):
    from pypdf import PdfReader

    choice = choose_application_cv(
        output_dir=tmp_path / "generated",
        archived=[],
        job={"id": 32, "title": "School Cleaner", "description": "School cleaning experience preferred.", "language": "en"},
        candidate={"name": "Aino Example", "email": "aino@example.test"},
        facts=[],
        records={"experience": [{
            "review_state": "DRAFT", "title": "School Cleaner", "details": "Unreviewed school cleaning claim",
        }]},
    )

    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(choice.path)).pages)
    assert "Target role: School Cleaner" in text
    assert "Work experience" not in text
    assert "Unreviewed school cleaning claim" not in text
    assert choice.score == 0
