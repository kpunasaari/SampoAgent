"""Generate readable one-column PDFs from confirmed candidate facts only."""

from dataclasses import dataclass
from pathlib import Path
import re
from xml.sax.saxutils import escape

from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer


@dataclass(frozen=True)
class PDFTextCheck:
    passed: bool
    score: int
    text: str
    missing: list[str]


# Kept as an import-compatibility alias for local callers from earlier versions.
# The result measures parsed-PDF text presence only, not ATS compatibility.
AtsReport = PDFTextCheck


ROLE_FAMILIES = ("universal", "entry_level", "cleaning_facilities", "hotel_hospitality", "restaurant_kitchen", "retail", "customer_service", "warehouse_logistics", "transport_delivery", "manufacturing_production", "construction", "maintenance_technical", "office_administration", "finance_accounting", "sales", "marketing_communications", "it_software", "engineering_technical", "healthcare", "social_care", "education_childcare", "public_sector", "security", "agriculture_outdoor", "creative_media")


def _filename(name: str) -> str:
    compact = re.sub(r"[^a-z0-9]+", "", name.casefold())
    return f"001_{compact}.pdf"


def render_filename(pattern: str, values: dict[str, str]) -> str:
    """Render supported placeholders and sanitize path separators/control characters."""
    rendered = pattern
    for key in ("first", "last", "fullname", "role", "company", "language", "version"):
        rendered = rendered.replace("{" + key + "}", values.get(key, ""))
    rendered = re.sub(r"[<>:\\|?*/\x00-\x1f]+", "_", rendered)
    rendered = re.sub(r"\s+", "_", rendered).strip("._ ")
    return rendered or "cv.pdf"


def generate_cv_pdf(
    *,
    output_dir: Path,
    language: str,
    role_family: str,
    candidate: dict[str, str],
    facts: list[dict[str, object]],
    filename_pattern: str | None = None,
    company: str = "",
    records: dict[str, list[dict[str, str]]] | None = None,
    target_title: str = "",
    version: str = "",
    priority_terms: list[str] | None = None,
) -> Path:
    if role_family not in ROLE_FAMILIES:
        raise ValueError("Unknown role family")
    output_dir.mkdir(parents=True, exist_ok=True)
    name_parts = candidate["name"].split(maxsplit=1)
    values = {
        "first": name_parts[0],
        "last": name_parts[1] if len(name_parts) > 1 else "",
        "fullname": candidate["name"],
        "role": role_family,
        "company": company,
        "language": language,
        "version": version,
    }
    filename = render_filename(filename_pattern, values) if filename_pattern else _filename(candidate["name"])
    if not filename.casefold().endswith(".pdf"):
        filename += ".pdf"
    path = output_dir / filename
    confirmed = [fact for fact in facts if bool(fact.get("confirmed"))]
    terms = [term.casefold() for term in (priority_terms or []) if term.strip()]

    def relevance(value: str) -> int:
        lowered = value.casefold()
        return sum(1 for term in terms if term in lowered or (lowered and lowered in term))

    def prioritize(values: list[str]) -> list[str]:
        return [value for _, value in sorted(enumerate(values), key=lambda item: (-relevance(item[1]), item[0]))]

    skills = prioritize([str(fact["value"]) for fact in confirmed if fact.get("type") == "skill"])
    languages = [str(fact["value"]) for fact in confirmed if fact.get("type") == "language"]
    structured = records or {}

    def record_lines(record_type: str) -> list[str]:
        lines: list[str] = []
        for record in structured.get(record_type, []):
            if str(record.get("review_state", "CONFIRMED")).upper() != "CONFIRMED":
                continue
            title = str(record.get("title") or record.get("name") or "").strip()
            details = str(record.get("details") or "").strip()
            if title:
                parts: list[str] = []
                start_date = str(record.get("start_date") or "").strip()
                end_date = str(record.get("end_date") or "").strip()
                if start_date:
                    parts.append(f"{start_date} – {end_date or ('Present' if record.get('is_current') else '')}".strip(" –"))
                parts.append(title)
                for key in ("organization", "location"):
                    value = str(record.get(key) or "").strip()
                    if value:
                        parts.append(value)
                evidence = record.get("evidence")
                source_excerpt = str(evidence.get("excerpt") or "").strip() if isinstance(evidence, dict) else ""
                if details and details.casefold() != source_excerpt.casefold():
                    parts.append(details)
                lines.append(" — ".join(parts))
        return prioritize(lines)

    experience = record_lines("experience")
    education = record_lines("education")
    credentials = record_lines("certificate") + record_lines("licence")
    labels = {
        "en": {
            "heading": "Curriculum Vitae", "target": "Target role", "prepared": "Prepared for",
            "skills": "Relevant skills", "languages": "Languages", "experience": "Work experience",
            "education": "Education", "credentials": "Certificates and licences",
            "profile": "Role-focused profile",
        },
        "fi": {
            "heading": "Ansioluettelo", "target": "Tavoiteltu tehtävä", "prepared": "Haettu tehtävä",
            "skills": "Tehtävään liittyvä osaaminen", "languages": "Kielet", "experience": "Työkokemus",
            "education": "Koulutus", "credentials": "Todistukset ja luvat",
            "profile": "Tehtävään sopiva osaaminen",
        },
    }
    copy = labels.get(language, labels["en"])
    navy = colors.HexColor("#16213a")
    violet = colors.HexColor("#5141b6")
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CVTitle", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=21,
        leading=25, textColor=navy, alignment=TA_LEFT, spaceAfter=3,
    )
    name_style = ParagraphStyle(
        "CVName", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=15,
        leading=19, textColor=violet, spaceAfter=2,
    )
    target_style = ParagraphStyle(
        "CVTarget", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=10,
        leading=14, textColor=navy, spaceAfter=1,
    )
    meta_style = ParagraphStyle(
        "CVMeta", parent=styles["Normal"], fontName="Helvetica", fontSize=9,
        leading=12, textColor=colors.HexColor("#46516a"),
    )
    section_style = ParagraphStyle(
        "CVSection", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=10,
        leading=13, textColor=violet, spaceBefore=13, spaceAfter=5, keepWithNext=True,
    )
    body_style = ParagraphStyle(
        "CVBody", parent=styles["BodyText"], fontName="Helvetica", fontSize=9.5,
        leading=13, textColor=navy, spaceAfter=4, alignment=TA_LEFT,
    )

    def footer(canvas: object, document: object) -> None:
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#d8dced"))
        canvas.setLineWidth(0.5)
        canvas.line(50, 34, A4[0] - 50, 34)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#69738a"))
        canvas.drawRightString(A4[0] - 50, 22, f"{document.page}")
        canvas.restoreState()

    story: list[object] = [
        Paragraph(escape(copy["heading"]), title_style),
        Paragraph(escape(candidate["name"]), name_style),
    ]
    contacts = [candidate.get(key, "").strip() for key in ("email", "phone")]
    contacts = [value for value in contacts if value]
    if contacts:
        story.append(Paragraph(escape(" | ".join(contacts)), meta_style))
    if target_title:
        story.append(Spacer(1, 7))
        story.append(Paragraph(f"{escape(copy['target'])}: {escape(target_title)}", target_style))
    if company.strip():
        story.append(Paragraph(f"{escape(copy['prepared'])}: {escape(company.strip())}", meta_style))
    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#d8dced"), spaceAfter=4))
    if skills:
        lead_skills = [skill for skill in skills if relevance(skill) > 0]
        if lead_skills:
            story.append(Paragraph(escape(copy["profile"]), section_style))
            story.append(Paragraph(
                escape(("Vahvistettu osaaminen: " if language == "fi" else "Confirmed capabilities: ") + ", ".join(lead_skills[:6])),
                body_style,
            ))
    for label_key, entries in (
        ("skills", skills),
        ("languages", languages),
        ("experience", experience),
        ("education", education),
        ("credentials", credentials),
    ):
        if not entries:
            continue
        story.append(Paragraph(escape(copy[label_key]), section_style))
        for entry in entries:
            story.append(Paragraph(f"- {escape(entry)}", body_style))

    document = SimpleDocTemplate(
        str(path), pagesize=A4, leftMargin=50, rightMargin=50,
        topMargin=44, bottomMargin=48, title=copy["heading"],
        author=candidate["name"],
    )
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return path


def check_pdf_text(path: Path, *, required: list[str]) -> PDFTextCheck:
    """Check whether expected phrases appear in text parsed from a PDF."""
    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    missing = [item for item in required if item not in text]
    score = round((len(required) - len(missing)) / len(required) * 100) if required else 100
    return PDFTextCheck(not missing, score, text, missing)


def validate_ats_pdf(path: Path, *, required: list[str]) -> PDFTextCheck:
    """Compatibility wrapper; this is a PDF text check, not an ATS score."""
    return check_pdf_text(path, required=required)
