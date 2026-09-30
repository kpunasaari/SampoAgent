from pathlib import Path


def test_cv_pdf_uses_only_confirmed_facts_and_contains_parsed_text(tmp_path: Path) -> None:
    from sampoagent.cv.service import check_pdf_text, generate_cv_pdf

    path = generate_cv_pdf(
        output_dir=tmp_path,
        language="en",
        role_family="warehouse_logistics",
        candidate={"name": "Aino Example", "email": "aino@example.test"},
        facts=[
            {"type": "skill", "value": "Forklift operation", "confirmed": True},
            {"type": "skill", "value": "Invented secret skill", "confirmed": False},
            {"type": "language", "value": "Finnish", "confirmed": True},
        ],
    )

    report = check_pdf_text(path, required=["Aino Example", "aino@example.test", "Forklift operation"])
    assert path.name == "001_ainoexample.pdf"
    assert report.passed is True
    assert "Invented secret skill" not in report.text


def test_pdf_text_check_api_describes_only_parsed_pdf_text_presence(tmp_path: Path) -> None:
    from sampoagent.cv.service import PDFTextCheck, check_pdf_text, generate_cv_pdf, validate_ats_pdf

    path = generate_cv_pdf(
        output_dir=tmp_path,
        language="en",
        role_family="cleaning_facilities",
        candidate={"name": "Aino Example", "email": "aino@example.test"},
        facts=[{"type": "skill", "value": "School cleaning", "confirmed": True}],
    )

    result = check_pdf_text(path, required=["Aino Example", "School cleaning", "Missing phrase"])

    assert isinstance(result, PDFTextCheck)
    assert result.score == 67
    assert result.missing == ["Missing phrase"]
    assert isinstance(validate_ats_pdf(path, required=["Aino Example"]), PDFTextCheck)


def test_generated_cv_includes_optional_candidate_phone_when_provided(tmp_path: Path) -> None:
    from pypdf import PdfReader
    from sampoagent.cv.service import generate_cv_pdf

    path = generate_cv_pdf(
        output_dir=tmp_path,
        language="en",
        role_family="cleaning_facilities",
        candidate={"name": "Aino Example", "email": "aino@example.test", "phone": "+358 40 000 0000"},
        facts=[],
    )

    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    assert "+358 40 000 0000" in text


def test_cv_filename_pattern_uses_safe_placeholders(tmp_path: Path) -> None:
    from sampoagent.cv.service import render_filename

    filename = render_filename("{role}_{first}_{company}_{language}.pdf", {"first": "Aino", "last": "Example", "fullname": "Aino Example", "role": "Warehouse Worker", "company": "North/Logistics", "language": "en"})

    assert filename == "Warehouse_Worker_Aino_North_Logistics_en.pdf"


def test_cv_includes_structured_experience_and_education_in_parsed_pdf_text(tmp_path: Path) -> None:
    from sampoagent.cv.service import check_pdf_text, generate_cv_pdf

    path = generate_cv_pdf(
        output_dir=tmp_path,
        language="en",
        role_family="customer_service",
        candidate={"name": "Aino Example", "email": "aino@example.test"},
        facts=[{"type": "skill", "value": "Customer service", "confirmed": True}],
        records={
            "experience": [{"title": "Service Assistant", "details": "Example Oy, 2023-01 to 2024-02"}],
            "education": [{"title": "Vocational qualification", "details": "Business"}],
        },
    )

    report = check_pdf_text(path, required=["Service Assistant", "2023-01", "Vocational qualification", "Customer service"])
    assert report.passed is True


def test_job_tailored_cv_prioritizes_confirmed_skills_relevant_to_the_vacancy(tmp_path: Path) -> None:
    from sampoagent.cv.service import check_pdf_text, generate_cv_pdf

    path = generate_cv_pdf(
        output_dir=tmp_path,
        language="en",
        role_family="cleaning_facilities",
        candidate={"name": "Aino Example", "email": "aino@example.test"},
        facts=[
            {"type": "skill", "value": "Time management", "confirmed": True},
            {"type": "skill", "value": "School cleaning", "confirmed": True},
            {"type": "skill", "value": "Customer service", "confirmed": True},
        ],
        target_title="School Cleaner",
        priority_terms=["School Cleaner", "School cleaning"],
    )

    report = check_pdf_text(path, required=["School cleaning", "Time management", "Customer service"])

    assert report.passed
    assert report.text.index("School cleaning") < report.text.index("Time management")


def test_cv_pdf_wraps_and_paginates_long_confirmed_work_history(tmp_path: Path) -> None:
    from pypdf import PdfReader
    from sampoagent.cv.service import check_pdf_text, generate_cv_pdf

    experience = [
        {"title": f"Confirmed role {index:02d}", "details": "School cleaning and customer service. " * 9}
        for index in range(1, 25)
    ]
    path = generate_cv_pdf(
        output_dir=tmp_path,
        language="en",
        role_family="cleaning_facilities",
        candidate={"name": "Aino Example", "email": "aino@example.test"},
        facts=[{"type": "skill", "value": "School cleaning", "confirmed": True}],
        records={"experience": experience},
    )

    report = check_pdf_text(path, required=[item["title"] for item in experience])

    assert report.passed
    assert len(PdfReader(str(path)).pages) >= 2
    assert "Confirmed role 24" in report.text
