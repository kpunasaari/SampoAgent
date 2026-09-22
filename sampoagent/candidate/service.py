"""Safe, deterministic V1 CV text parsing with provenance."""

from dataclasses import dataclass
from pathlib import Path
import re

from docx import Document
from pypdf import PdfReader


@dataclass(frozen=True)
class CandidateFact:
    type: str
    value: str
    provenance: str
    source_id: str
    confidence: float
    confirmed: bool = False


@dataclass(frozen=True)
class IngestionResult:
    facts: list[CandidateFact]
    warning: str | None = None


_SECTION_TYPES = {"skills": "skill", "languages": "language", "licences": "licence", "certificates": "certificate"}


def read_cv_file(path: Path) -> str:
    """Extract selectable text from permitted local CV formats without OCR."""
    suffix = path.suffix.casefold()
    if suffix == ".txt":
        return path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".pdf":
        return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    if suffix == ".docx":
        return "\n".join(paragraph.text for paragraph in Document(str(path)).paragraphs)
    raise ValueError("Unsupported CV file type. Use PDF, DOCX, or TXT.")


def ingest_text_cv(text: str, *, source_id: str, storage_dir: Path) -> IngestionResult:
    """Extract explicit short-list facts; never infer factual claims."""
    storage_dir.mkdir(parents=True, exist_ok=True)
    if len(text.strip()) < 10:
        return IngestionResult([], "This document does not contain useful machine-readable text.")
    facts: list[CandidateFact] = []
    email = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)
    if email:
        facts.append(CandidateFact("email", email.group(), "CV_EXTRACTED", source_id, 0.99))
    for line in text.splitlines():
        if ":" not in line:
            continue
        label, values = (part.strip() for part in line.split(":", 1))
        fact_type = _SECTION_TYPES.get(label.lower())
        if fact_type:
            for value in re.split(r"[,;]", values):
                cleaned = value.strip()
                if cleaned:
                    facts.append(CandidateFact(fact_type, cleaned, "CV_EXTRACTED", source_id, 0.9))
    return IngestionResult(facts)
