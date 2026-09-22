from pathlib import Path


def test_cv_pdf_uses_only_confirmed_facts_and_is_ats_readable(tmp_path: Path) -> None:
    from sampoagent.cv.service import generate_cv_pdf, validate_ats_pdf

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

    report = validate_ats_pdf(path, required=["Aino Example", "aino@example.test", "Forklift operation"])
    assert path.name == "001_ainoexample.pdf"
    assert report.passed is True
    assert "Invented secret skill" not in report.text


def test_cv_filename_pattern_uses_safe_placeholders(tmp_path: Path) -> None:
    from sampoagent.cv.service import render_filename

    filename = render_filename("{role}_{first}_{company}_{language}.pdf", {"first": "Aino", "last": "Example", "fullname": "Aino Example", "role": "Warehouse Worker", "company": "North/Logistics", "language": "en"})

    assert filename == "Warehouse_Worker_Aino_North_Logistics_en.pdf"
