"""Deterministically identify application-by-email addresses in verified listings."""

from __future__ import annotations

from dataclasses import dataclass
from email.utils import parseaddr
from hashlib import sha256
from html import unescape
import re


_EMAIL = re.compile(
    r"(?<![\w.+-])[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,63}(?![\w-])",
    re.IGNORECASE,
)
_EMAIL_LIKE = re.compile(
    r"(?<![\w.+-])[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@(?:[A-Z0-9@.-]+)",
    re.IGNORECASE,
)
_CUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("en_apply_by_email", re.compile(r"\bapply\s+(?:for\s+(?:the\s+)?(?:role|position)\s+)?(?:by|via)\s+e-?mail(?:\s+(?:to|at))?\b", re.IGNORECASE)),
    ("en_send_application", re.compile(r"\b(?:send|email)\s+(?:your\s+)?(?:job\s+)?(?:application|cv|resume|curriculum\s+vitae)\s+(?:(?:by|via)\s+e-?mail\s+)?to\b", re.IGNORECASE)),
    ("en_application_sent_by_email", re.compile(r"\bapplications?\s+(?:(?:must|should|can|may)\s+be\s+)?sent\s+(?:by|via)\s+e-?mail(?:\s+(?:to|at))?\b", re.IGNORECASE)),
    ("en_application_email_submission", re.compile(r"\b(?:applications?|CVs?|resumes?|curriculum vitae)\s+(?:must|should|can|may|need to|needs to)\s+be\s+(?:sent|submitted|emailed)\s+(?:by|via)\s+e-?mail(?:\s+(?:to|at))?\b", re.IGNORECASE)),
    ("en_application_email_submission", re.compile(r"\b(?:the\s+)?applications?\s+(?:must|should|can|may|needs to|has to)\s+be\s+emailed\s+(?:to|at)\b", re.IGNORECASE)),
    ("fi_send_application_email", re.compile(r"\blähetä\s+(?:työ)?hakemuksesi?\s+(?:sähköpostitse|sähköpostilla)\s+(?:sähköposti)?osoitteeseen\b", re.IGNORECASE)),
    ("fi_send_application_email", re.compile(r"\b(?:lähetä|toimita)\s+(?:työ)?hakemus\s+(?:sähköpostitse|sähköpostilla)\s+(?:(?:sähköposti)?osoitteeseen)?\b", re.IGNORECASE)),
    ("fi_application_sent_by_email", re.compile(r"\bhakemus\s+(?:lähetetään|toimitetaan)\s+(?:sähköpostitse|sähköpostilla)\s+(?:(?:sähköposti)?osoitteeseen)?\b", re.IGNORECASE)),
    ("fi_apply_by_email", re.compile(r"\bhaku\s+(?:sähköpostitse|sähköpostilla)\s+(?:(?:sähköposti)?osoitteeseen)?\b", re.IGNORECASE)),
    ("fi_application_email_submission", re.compile(r"\bhakemuksen?\s+(?:voi|voidaan|pitää|täytyy|tulee)\s+(?:lähettää|toimittaa)\s+(?:sähköpostitse|sähköpostilla)\b", re.IGNORECASE)),
    ("sv_send_application_email", re.compile(r"\bskicka\s+(?:din\s+)?(?:jobb)?ansökan\s+(?:(?:via|per)\s+e-?post\s+)?(?:till|till\s+e-postadressen)\b", re.IGNORECASE)),
    ("sv_application_sent_by_email", re.compile(r"\bansökan\s+(?:skickas|sänds)\s+(?:(?:via|per)\s+e-?post\s+)?(?:till\s+)?\b", re.IGNORECASE)),
    ("sv_apply_by_email", re.compile(r"\bansök\s+(?:via|per)\s+e-?post\s+(?:till\s+)?\b", re.IGNORECASE)),
    ("sv_application_email_submission", re.compile(r"\bansökan\s+(?:kan|ska|måste|bör)\s+(?:skickas|sändas)\s+(?:(?:via|per)\s+)?e-?post\b", re.IGNORECASE)),
)
_UNSUPPORTED_REQUIREMENTS = (
    re.compile(r"\b(?:include|attach|enclose|submit|provide|state|mention)\b.{0,140}\b(?:cover letter|motivation(?:al)? letter|salary expectation|salary request|references?|reference letters?|diploma|degree certificate|certificate copies|transcripts?|portfolio|work samples?)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\b(?:liitä|liittäkää|liittaa|lisää|ilmoita|kerro|toimita|sisällytä)\b.{0,140}\b(?:palkkatoive|palkkavaatimus|hakemuskirje|motivaatiokirje|suosittelijat|todistuskopiot|tutkintotodistus|portfolio|työnäyte)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\b(?:bifoga|ange|inkludera|skicka med|skicka in)\b.{0,140}\b(?:löneanspråk|personligt brev|motivationsbrev|referenser|betyg|examensbevis|portfolio|arbetsprov)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\b(?:cover letter|motivation(?:al)? letter|salary expectation|references?|degree certificate|transcript|portfolio)\b.{0,80}\b(?:required|must be included|must be attached|should be included)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\b(?:palkkatoive|palkkavaatimus|hakemuskirje|suosittelijat|todistuskopiot|tutkintotodistus)\b.{0,80}\b(?:vaaditaan|pakollinen|liitettävä|ilmoitettava)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\b(?:löneanspråk|personligt brev|referenser|betyg|examensbevis)\b.{0,80}\b(?:krävs|obligatorisk|måste bifogas|ska inkluderas)\b", re.IGNORECASE | re.DOTALL),
)
_MAX_ADDRESS_DISTANCE = 180
_CONTACT_BOUNDARY = re.compile(
    r"\b(?:for\s+questions|questions|contact(?:\s+us)?|for\s+more\s+information|"
    r"lisätietoja|lisätieto|ota\s+yhteyttä|yhteydenotot|kysymykset|kysymyksiä|"
    r"frågor|kontakta|kontakt)\b",
    re.IGNORECASE,
)
_NEGATED_EMAIL_INSTRUCTIONS = (
    re.compile(r"\b(?:do\s+not|don't|must\s+not|should\s+not|never)\s+(?:apply|send|email|submit)\b.{0,120}\b(?:by|via)\s+e-?mail\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\b(?:do\s+not|don't|must\s+not|should\s+not|never)\s+(?:email|send|submit)\b.{0,120}\b(?:applications?|CVs?|resumes?|curriculum\s+vitae)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\b(?:älä|älkää)\b.{0,120}\b(?:lähetä|toimita|hae)\b.{0,120}\bsähköpost", re.IGNORECASE | re.DOTALL),
    re.compile(r"\b(?:skicka|skickas|ansök|ansökan)\b.{0,50}\binte\b.{0,120}\be-?post\b", re.IGNORECASE | re.DOTALL),
)
_UNRECOGNIZED_EMAIL_APPLICATION_HINTS = (
    re.compile(r"\b(?:apply|applications?|CVs?|resumes?|curriculum vitae)\b[^.!?;\n]{0,100}\b(?:e-?mail|email address)\b", re.IGNORECASE),
    re.compile(r"\b(?:e-?mail|email address)\b[^.!?;\n]{0,100}\b(?:apply|applications?|CVs?|resumes?|curriculum vitae)\b", re.IGNORECASE),
    re.compile(r"\bhakem\w*\b[^.!?;\n]{0,100}\bsähköpost\w*\b", re.IGNORECASE),
    re.compile(r"\bsähköpost\w*\b[^.!?;\n]{0,100}\bhakem\w*\b", re.IGNORECASE),
    re.compile(r"\bansök\w*\b[^.!?;\n]{0,100}\be-?post\b", re.IGNORECASE),
    re.compile(r"\be-?post\b[^.!?;\n]{0,100}\bansök\w*\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class EmailRecipientEvidence:
    status: str
    recipient: str | None
    cue_id: str
    verified_snapshot_hash: str
    description_sha256: str = ""


def _clean_listing(text: str) -> str:
    with_line_breaks = re.sub(r"<\s*/?\s*(?:p|div|li|br|h[1-6])\b[^>]*>", "\n", text, flags=re.IGNORECASE)
    no_tags = re.sub(r"<[^>]{0,500}>", " ", with_line_breaks)
    return unescape(no_tags).replace("\u00a0", " ")


def _valid_email(value: str) -> bool:
    parsed = parseaddr(value)
    if parsed[1] != value or value.count("@") != 1 or len(value) > 320:
        return False
    local, domain = value.rsplit("@", 1)
    return bool(
        local and domain and "." in domain
        and not any(char.isspace() or char in "\r\n\x00<>;,\"'" for char in value)
    )


def extract_application_email_recipient(
    description: str,
    *,
    language: str,
    verified_snapshot_hash: str,
) -> EmailRecipientEvidence:
    """Return one contextual address or a non-sendable hold status.

    The entire description is inspected only for application-instruction cues;
    an email address without such a cue is never considered a recipient.
    """
    text = _clean_listing(description)
    description_hash = sha256(text.encode("utf-8")).hexdigest()
    if any(pattern.search(text) for pattern in _NEGATED_EMAIL_INSTRUCTIONS):
        return EmailRecipientEvidence(
            "CONTRADICTORY_INSTRUCTION", None, "negative_application_email_instruction",
            verified_snapshot_hash if re.fullmatch(r"[a-f0-9]{64}", verified_snapshot_hash or "", flags=re.IGNORECASE) else "",
            description_hash,
        )
    cues = [(cue_id, match) for cue_id, pattern in _CUES for match in pattern.finditer(text)]
    if not cues:
        if any(pattern.search(text) for pattern in _UNRECOGNIZED_EMAIL_APPLICATION_HINTS):
            return EmailRecipientEvidence(
                "UNSUPPORTED_INSTRUCTION", None, "unrecognized_email_application_instruction",
                verified_snapshot_hash if re.fullmatch(r"[a-f0-9]{64}", verified_snapshot_hash or "", flags=re.IGNORECASE) else "",
                description_hash,
            )
        return EmailRecipientEvidence("NOT_EMAIL_APPLICATION", None, "", "", description_hash)
    if not re.fullmatch(r"[a-f0-9]{64}", verified_snapshot_hash or "", flags=re.IGNORECASE):
        return EmailRecipientEvidence("MISSING_VERIFICATION", None, "", "", description_hash)
    if language.casefold() not in {"fi", "en", "sv"}:
        return EmailRecipientEvidence("UNSUPPORTED_LANGUAGE", None, "", verified_snapshot_hash, description_hash)

    associated: list[tuple[str, str]] = []
    malformed_in_context = False
    for cue_id, cue_match in cues:
        window_end = min(len(text), cue_match.end() + _MAX_ADDRESS_DISTANCE)
        for candidate_match in _EMAIL_LIKE.finditer(text, cue_match.end(), window_end):
            gap = text[cue_match.end():candidate_match.start()]
            gap_without_candidates = _EMAIL_LIKE.sub(" ", gap)
            if re.search(r"[.!?]", gap_without_candidates) or _CONTACT_BOUNDARY.search(gap_without_candidates):
                break
            candidate = candidate_match.group(0).rstrip(".,:;!?)]}")
            if not _valid_email(candidate):
                malformed_in_context = True
        for address_match in _EMAIL.finditer(text, cue_match.end(), window_end):
            gap = text[cue_match.end():address_match.start()]
            # A prior address can be listed among alternatives. Mask those
            # address tokens before treating punctuation as a sentence stop.
            gap_without_addresses = _EMAIL.sub(" ", gap)
            if re.search(r"[.!?]", gap_without_addresses) or _CONTACT_BOUNDARY.search(gap_without_addresses):
                break
            address = address_match.group(0)
            if _valid_email(address):
                associated.append((cue_id, address))

    unique_addresses = {address.casefold(): address for _, address in associated}
    cue_ids = {cue_id for cue_id, _ in associated}
    if malformed_in_context:
        return EmailRecipientEvidence("AMBIGUOUS_RECIPIENT", None, "+".join(sorted(cue_ids)) or cues[0][0], verified_snapshot_hash, description_hash)
    if len(unique_addresses) > 1:
        return EmailRecipientEvidence("AMBIGUOUS_RECIPIENT", None, "+".join(sorted(cue_ids)) or cues[0][0], verified_snapshot_hash, description_hash)
    if not unique_addresses:
        return EmailRecipientEvidence("MISSING_RECIPIENT", None, cues[0][0], verified_snapshot_hash, description_hash)
    if any(pattern.search(text) for pattern in _UNSUPPORTED_REQUIREMENTS):
        return EmailRecipientEvidence("UNSUPPORTED_REQUIREMENTS", None, "application_materials_required", verified_snapshot_hash, description_hash)
    cue_id = "+".join(sorted(cue_ids)) or cues[0][0]
    return EmailRecipientEvidence("READY", next(iter(unique_addresses.values())), cue_id, verified_snapshot_hash, description_hash)
