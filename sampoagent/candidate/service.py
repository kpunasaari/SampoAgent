"""Safe, deterministic V1 CV text parsing with provenance."""

from dataclasses import dataclass, field
from pathlib import Path
import re
import unicodedata

from docx import Document
from pypdf import PdfReader

from sampoagent.cv.ocr import OCRProvider, get_ocr_provider


@dataclass(frozen=True)
class SourceEvidence:
    page: int | None
    line: int
    start_char: int
    end_char: int
    excerpt: str


@dataclass(frozen=True)
class CandidateFact:
    type: str
    value: str
    provenance: str
    source_id: str
    confidence: float
    confirmed: bool = False
    evidence: SourceEvidence | None = None


@dataclass(frozen=True)
class CVRecordDraft:
    """A source-linked experience or education record awaiting candidate review."""

    record_type: str
    title: str
    organization: str
    location: str
    start_date: str
    end_date: str
    is_current: bool
    details: str
    source_id: str
    evidence: SourceEvidence
    confirmed: bool = False


@dataclass(frozen=True)
class IngestionResult:
    facts: list[CandidateFact]
    warning: str | None = None
    records: list[CVRecordDraft] = field(default_factory=list)


_SECTION_ALIASES = {
    "skill": {
        "skill", "skills", "key skills", "core skills", "technical skills", "core competencies",
        "competencies", "osaaminen", "osaamiset", "tyoosaaminen", "taidot", "avainsanat",
    },
    "language": {"language", "languages", "language skills", "kielitaito", "kielet"},
    "licence": {"licence", "licences", "license", "licenses", "driving licence", "driving license", "ajokortti", "ajokortit", "luvat"},
    "certificate": {"certificate", "certificates", "certification", "certifications", "qualifications", "todistus", "todistukset", "patevyydet", "kortit"},
    "experience": {"work experience", "professional experience", "employment history", "tyokokemus", "tyokokemukset", "tyohistoria", "arbetslivserfarenhet", "arbetslivserfarenheter", "yrkeserfarenhet", "yrkeserfarenheter", "anstallningar"},
    "education": {"education", "education and training", "education & training", "training", "degree", "degrees", "koulutus", "koulutukset", "tutkinto", "tutkinnot", "opinnot", "utbildning", "utbildningar", "studier"},
}
_SECTION_TYPES = {
    re.sub(r"\s+", " ", unicodedata.normalize("NFKD", alias).encode("ascii", "ignore").decode()).casefold(): fact_type
    for fact_type, aliases in _SECTION_ALIASES.items()
    for alias in aliases
}
_BULLET_PREFIX = re.compile(r"^\s*(?:[-*•▪◦]|\d+[.)])\s*")
_DATE_TOKEN = r"(?:19|20)\d{2}(?:[-/.](?:0?[1-9]|1[0-2]))?"
_DATE_RANGE = re.compile(
    rf"(?P<start>{_DATE_TOKEN})\s*(?:[-–—]|\bto\b|\buntil\b)\s*"
    rf"(?P<end>{_DATE_TOKEN}|present|current|ongoing|now|nykyhetki|nykyinen|pågående|nuvarande|nu)",
    re.IGNORECASE,
)


def _normalize_date(value: str) -> str:
    parts = re.split(r"[-/.]", value)
    return f"{parts[0]}-{int(parts[1]):02d}" if len(parts) == 2 else parts[0]


def _record_from_line(
    fact_type: str,
    value: str,
    *,
    source_id: str,
    evidence: SourceEvidence,
) -> CVRecordDraft | None:
    if fact_type not in {"experience", "education"}:
        return None
    cleaned = " ".join(value.split()).strip(" ,;|·•")
    if not cleaned:
        return None
    date_match = _DATE_RANGE.search(cleaned)
    start_date = _normalize_date(date_match.group("start")) if date_match else ""
    end_date = ""
    is_current = False
    remainder = cleaned
    if date_match:
        end_value = date_match.group("end")
        is_current = end_value.casefold() in {"present", "current", "ongoing", "now", "nykyhetki", "nykyinen", "pågående", "nuvarande", "nu"}
        end_date = "" if is_current else _normalize_date(end_value)
        remainder = (cleaned[:date_match.start()] + " " + cleaned[date_match.end():]).strip(" ,;|·•-–—")
    parts = [part.strip(" ,;|·•-–—") for part in re.split(r"\s*(?:\||—|–|\s@\s)\s*", remainder) if part.strip(" ,;|·•-–—")]
    title = parts[0] if parts else remainder
    organization = parts[1] if len(parts) > 1 else ""
    location = parts[2] if len(parts) > 2 else ""
    if not title:
        return None
    return CVRecordDraft(
        record_type=fact_type,
        title=title,
        organization=organization,
        location=location,
        start_date=start_date,
        end_date=end_date,
        is_current=is_current,
        details=cleaned,
        source_id=source_id,
        evidence=evidence,
    )


def _normalize_heading(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", normalized.strip().rstrip(":")).casefold()


def _extract_pdf_page_text(page) -> str:
    """Prefer a repeatable two-column split when layout extraction makes one clear."""
    plain_text = page.extract_text() or ""
    try:
        layout_text = page.extract_text(extraction_mode="layout", layout_mode_space_vertically=False) or ""
    except (TypeError, ValueError):
        return plain_text
    lines = layout_text.splitlines()
    width = max((len(line) for line in lines), default=0)
    if not width:
        return plain_text
    gap_matches = [
        match
        for line in lines
        for match in re.finditer(r" {8,}", line)
        if 0.3 * width <= match.end() <= 0.8 * width
    ]
    if len(gap_matches) < 3:
        return plain_text

    # The right column usually begins at a stable visual x coordinate even when
    # the left-column text (and therefore the gap's start) changes by row.
    right_starts = [match.end() for match in gap_matches]
    right_start = sorted(right_starts)[len(right_starts) // 2]
    stable = sum(abs(candidate - right_start) <= 5 for candidate in right_starts)
    if not 0.3 * width <= right_start <= 0.8 * width or stable < max(3, len(right_starts) * 0.6):
        return plain_text

    left_column: list[str] = []
    right_column: list[str] = []
    for line in lines:
        gap = next(
            (match for match in re.finditer(r" {8,}", line) if abs(match.end() - right_start) <= 5),
            None,
        )
        if gap:
            left, right = line[:gap.start()].strip(), line[gap.end():].strip()
            if left:
                left_column.append(left)
            if right:
                right_column.append(right)
            continue
        content_start = len(line) - len(line.lstrip())
        content = line.strip()
        if not content:
            continue
        (right_column if content_start >= right_start - 5 else left_column).append(content)

    split_text = "\n".join(left_column + right_column)
    return split_text if len(split_text.strip()) >= 10 else plain_text


def _normalize_pdf_text(text: str) -> str:
    # PDF text extraction often represents a visually hyphenated word as two
    # lines. Merge only this explicit, low-risk wrap marker; ordinary line
    # breaks remain intact for section and record detection.
    return re.sub(r"(?<=[A-Za-zÅÄÖåäö])[-\u00ad]\r?\n[ \t]*(?=[a-zåäö])", "", text)


def read_cv_file(
    path: Path,
    *,
    ocr_provider: OCRProvider | None = None,
    ocr_provider_name: str | None = None,
) -> str:
    """Extract selectable text and optionally OCR a scanned PDF using a local provider."""
    suffix = path.suffix.casefold()
    if suffix == ".txt":
        return path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".pdf":
        text = _normalize_pdf_text("\f".join(_extract_pdf_page_text(page) for page in PdfReader(str(path)).pages))
        if len(text.replace("\f", "").strip()) >= 10:
            return text
        provider = ocr_provider if ocr_provider is not None else get_ocr_provider(ocr_provider_name)
        return provider.extract_pdf(path) if provider else text
    if suffix == ".docx":
        return "\n".join(paragraph.text for paragraph in Document(str(path)).paragraphs)
    raise ValueError("Unsupported CV file type. Use PDF, DOCX, or TXT.")


def ingest_text_cv(text: str, *, source_id: str, storage_dir: Path) -> IngestionResult:
    """Extract explicit short-list facts; never infer factual claims."""
    storage_dir.mkdir(parents=True, exist_ok=True)
    if len(text.replace("\f", "").strip()) < 10:
        return IngestionResult([], "No readable CV text was found. If this is a scanned PDF, enable a configured OCR provider or enter the text manually; SampoAgent did not guess or create candidate facts.")
    facts: list[CandidateFact] = []
    records: list[CVRecordDraft] = []
    seen: set[tuple[str, str]] = set()
    seen_records: set[tuple[str, str]] = set()
    has_page_markers = "\f" in text or Path(source_id).suffix.casefold() == ".pdf"

    def append_fact(fact_type: str, value: str, evidence: SourceEvidence, confidence: float = 0.82) -> None:
        cleaned = " ".join(value.split()).strip(" ,;|·•")
        key = (fact_type, cleaned.casefold())
        if not cleaned or len(cleaned) > 240 or key in seen:
            return
        seen.add(key)
        facts.append(CandidateFact(fact_type, cleaned, "CV_EXTRACTED", source_id, confidence, evidence=evidence))

    global_offset = 0
    pages = text.split("\f")
    for page_index, page_text in enumerate(pages, start=1):
        current_type: str | None = None
        local_offset = 0
        for line_number, raw_line in enumerate(page_text.splitlines(keepends=True), start=1):
            line = raw_line.rstrip("\r\n")
            stripped = line.strip()
            line_start = global_offset + local_offset
            email = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", line)
            if email:
                evidence = SourceEvidence(page_index if has_page_markers else None, line_number, line_start + email.start(), line_start + email.end(), line.strip())
                append_fact("email", email.group(), evidence, confidence=0.98)

            if not stripped:
                local_offset += len(raw_line)
                continue
            label, separator, values = stripped.partition(":")
            fact_type = _SECTION_TYPES.get(_normalize_heading(label if separator else stripped))
            if fact_type:
                current_type = fact_type
                if separator and values.strip():
                    content = values.strip()
                    value_offset = line_start + line.find(values) + (len(values) - len(values.lstrip()))
                    evidence = SourceEvidence(page_index if has_page_markers else None, line_number, value_offset, value_offset + len(content), line.strip())
                    for value in re.split(r"[,;|]", content):
                        append_fact(fact_type, value, evidence)
                local_offset += len(raw_line)
                continue
            if separator:
                # An unknown labeled section ends the previous section; do not leak unrelated text into it.
                current_type = None
            elif current_type:
                bullet = _BULLET_PREFIX.match(line)
                value = line[bullet.end():].strip() if bullet else stripped
                if value:
                    start = line_start + (bullet.end() if bullet else line.find(value))
                    evidence = SourceEvidence(page_index if has_page_markers else None, line_number, start, start + len(value), line.strip())
                    append_fact(current_type, value, evidence, confidence=0.78)
                    record = _record_from_line(current_type, value, source_id=source_id, evidence=evidence)
                    if record:
                        record_text = " ".join((record.title, record.organization, record.start_date, record.end_date, record.details)).casefold()
                        record_key = (record.record_type, record_text)
                        if record_key not in seen_records:
                            records.append(record)
                            seen_records.add(record_key)
            local_offset += len(raw_line)
        global_offset += len(page_text) + 1
    return IngestionResult(facts, records=records)
