"""Generate readable one-column PDFs from confirmed candidate facts only."""

from dataclasses import dataclass
from pathlib import Path
import re

from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas


@dataclass(frozen=True)
class AtsReport:
    passed: bool
    score: int
    text: str
    missing: list[str]


ROLE_FAMILIES = ("universal", "entry_level", "cleaning_facilities", "hotel_hospitality", "restaurant_kitchen", "retail", "customer_service", "warehouse_logistics", "transport_delivery", "manufacturing_production", "construction", "maintenance_technical", "office_administration", "finance_accounting", "sales", "marketing_communications", "it_software", "engineering_technical", "healthcare", "social_care", "education_childcare", "public_sector", "security", "agriculture_outdoor", "creative_media")


def _filename(name: str) -> str:
    compact = re.sub(r"[^a-z0-9]+", "", name.casefold())
    return f"001_{compact}.pdf"


def render_filename(pattern: str, values: dict[str, str]) -> str:
    """Render supported placeholders and sanitize path separators/control characters."""
    rendered = pattern
    for key in ("first", "last", "fullname", "role", "company", "language"):
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
    }
    filename = render_filename(filename_pattern, values) if filename_pattern else _filename(candidate["name"])
    if not filename.casefold().endswith(".pdf"):
        filename += ".pdf"
    path = output_dir / filename
    labels = {
        "en": ("Curriculum Vitae", "Skills", "Languages", "Work Experience", "Education", "Certificates and Licences"),
        "fi": ("Ansioluettelo", "Osaaminen", "Kielet", "Työkokemus", "Koulutus", "Todistukset ja luvat"),
    }
    heading, skills_label, languages_label, experience_label, education_label, credentials_label = labels.get(language, labels["en"])
    confirmed = [fact for fact in facts if fact.get("confirmed") is True]
    skills = [str(fact["value"]) for fact in confirmed if fact.get("type") == "skill"]
    languages = [str(fact["value"]) for fact in confirmed if fact.get("type") == "language"]
    structured = records or {}

    def record_lines(record_type: str) -> list[str]:
        lines: list[str] = []
        for record in structured.get(record_type, []):
            title = str(record.get("title") or record.get("name") or "").strip()
            details = str(record.get("details") or "").strip()
            if title:
                lines.append(f"{title} — {details}" if details else title)
        return lines

    experience = record_lines("experience")
    education = record_lines("education")
    credentials = record_lines("certificate") + record_lines("licence")
    canvas = Canvas(str(path), pagesize=A4)
    y = 800
    lines = [heading, candidate["name"], candidate.get("email", ""), f"Role family: {role_family}", f"{skills_label}: {', '.join(skills)}", f"{languages_label}: {', '.join(languages)}"]
    if experience:
        lines.extend([experience_label, *experience])
    if education:
        lines.extend([education_label, *education])
    if credentials:
        lines.extend([credentials_label, *credentials])
    for line in lines:
        canvas.drawString(56, y, line)
        y -= 26
    canvas.save()
    return path


def validate_ats_pdf(path: Path, *, required: list[str]) -> AtsReport:
    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    missing = [item for item in required if item not in text]
    score = round((len(required) - len(missing)) / len(required) * 100) if required else 100
    return AtsReport(not missing, score, text, missing)
