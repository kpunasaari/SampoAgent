from io import BytesIO

from fastapi.testclient import TestClient
from reportlab.pdfgen.canvas import Canvas

from sampoagent.app.main import create_app
from sampoagent.candidate.service import ingest_text_cv, read_cv_file
from sampoagent.cv.ocr import OCRProviderUnavailable, get_ocr_provider


def _blank_pdf() -> bytes:
    output = BytesIO()
    canvas = Canvas(output)
    canvas.showPage()
    canvas.showPage()
    canvas.save()
    return output.getvalue()


def _pdf_with_rows(rows: list[tuple[str, str | None]]) -> bytes:
    output = BytesIO()
    canvas = Canvas(output)
    y = 760
    for left, right in rows:
        canvas.drawString(50, y, left)
        if right:
            canvas.drawString(320, y, right)
        y -= 24
    canvas.save()
    return output.getvalue()


class FakeLocalOCR:
    name = "fake-local"

    def extract_pdf(self, path):
        return "Skills\n- Cleaning\fWork Experience\n2022-present Cleaner | Northstar Oy"


def test_local_ocr_text_keeps_pdf_page_evidence_and_unconfirmed_state(tmp_path):
    path = tmp_path / "scan.pdf"
    path.write_bytes(_blank_pdf())

    text = read_cv_file(path, ocr_provider=FakeLocalOCR())
    result = ingest_text_cv(text, source_id=path.name, storage_dir=tmp_path)

    skill = next(fact for fact in result.facts if fact.value == "Cleaning")
    work = next(record for record in result.records if record.title == "Cleaner")
    assert skill.evidence.page == 1
    assert work.evidence.page == 2
    assert all(not fact.confirmed for fact in result.facts)
    assert not any(record.confirmed for record in result.records)


def test_disabled_ocr_returns_explicit_manual_step_instead_of_guessing(tmp_path):
    path = tmp_path / "scan.pdf"
    path.write_bytes(_blank_pdf())

    text = read_cv_file(path, ocr_provider_name="none")
    result = ingest_text_cv(text, source_id=path.name, storage_dir=tmp_path)

    assert result.facts == []
    assert result.records == []
    assert "enter the text manually" in result.warning


def test_environment_ocr_provider_is_selectable_and_bad_configuration_fails_clearly(tmp_path, monkeypatch):
    monkeypatch.setenv("SAMPOAGENT_OCR_PROVIDER", "tesseract")
    monkeypatch.setenv("SAMPOAGENT_TESSERACT_CMD", str(tmp_path / "missing-tesseract.exe"))
    provider = get_ocr_provider("environment")
    assert provider is not None

    path = tmp_path / "scan.pdf"
    path.write_bytes(_blank_pdf())
    try:
        read_cv_file(path, ocr_provider=provider)
    except OCRProviderUnavailable as error:
        assert "not installed" in str(error)
    else:
        raise AssertionError("Expected a visible manual step for an unavailable OCR binary")


def test_two_column_pdf_keeps_sections_together_and_records_source_page(tmp_path):
    path = tmp_path / "two-column.pdf"
    path.write_bytes(_pdf_with_rows([
        ("Skills", "Work Experience"),
        ("Cleaning", "2020-2022 Cleaner | Northstar Oy"),
        ("Languages", "Education"),
        ("Finnish", "Omnia Diploma"),
    ]))

    text = read_cv_file(path)
    result = ingest_text_cv(text, source_id=path.name, storage_dir=tmp_path)

    assert "Skills\nCleaning\nLanguages\nFinnish\nWork Experience" in text
    assert any(fact.type == "skill" and fact.value == "Cleaning" for fact in result.facts)
    work = next(record for record in result.records if record.record_type == "experience")
    assert work.title == "Cleaner"
    assert work.organization == "Northstar Oy"
    assert work.evidence.page == 1
    assert not work.confirmed


def test_pdf_hyphenated_word_wrap_is_rejoined_without_losing_record_evidence(tmp_path):
    path = tmp_path / "wrapped.pdf"
    path.write_bytes(_pdf_with_rows([
        ("Work Experience", None),
        ("2020-2022 Senior housekeep-", None),
        ("ing | Northstar Oy", None),
    ]))

    text = read_cv_file(path)
    result = ingest_text_cv(text, source_id=path.name, storage_dir=tmp_path)

    work = next(record for record in result.records if record.record_type == "experience")
    assert work.title == "Senior housekeeping"
    assert work.organization == "Northstar Oy"
    assert work.evidence.page == 1
    assert "Senior housekeeping" in work.evidence.excerpt
    assert not work.confirmed


def test_settings_and_upload_expose_ocr_state_without_claiming_it_processed(tmp_path, monkeypatch):
    monkeypatch.setenv("SAMPOAGENT_OCR_PROVIDER", "tesseract")
    monkeypatch.setenv("SAMPOAGENT_TESSERACT_CMD", str(tmp_path / "missing-tesseract.exe"))
    app = create_app(database_path=tmp_path / "ocr.db", storage_dir=tmp_path / "storage")
    client = TestClient(app)
    settings = client.get("/settings").text
    assert "Scanned CV OCR" in settings
    assert "Local Tesseract OCR" in settings

    response = client.post(
        "/cvs/upload",
        files={"file": ("scan.pdf", _blank_pdf(), "application/pdf")},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Local Tesseract is not installed" in response.text
    assert app.state.repository.rows("facts") == []
