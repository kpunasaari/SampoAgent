from sampoagent.cv.service import check_pdf_text, generate_cv_pdf


def test_wrapped_candidate_phrase_is_present_but_missing_claim_is_not(tmp_path):
    phrase = "Windows workstation installation and troubleshooting " * 9
    path = generate_cv_pdf(
        output_dir=tmp_path, language="en", role_family="it_software",
        candidate={"name": "Synthetic Candidate"},
        facts=[{"type": "skill", "value": phrase.strip(), "confirmed": True, "rejected": False}],
    )
    assert check_pdf_text(path, required=[phrase.strip()]).passed
    report = check_pdf_text(path, required=["Nonexistent certified qualification"])
    assert not report.passed
    assert report.missing == ["Nonexistent certified qualification"]
