"""Resolve ATS form fields only from explicit, confirmed candidate records."""

from dataclasses import dataclass
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone

from sampoagent.candidate.questions import question_id_for_form_label, question_id_for_label

@dataclass(frozen=True)
class FormField:
    field_id: str
    label: str
    required: bool
    kind: str = "text"
    options: tuple[str, ...] = ()
    autocomplete: str = ""
    name: str = ""
    description: str = ""
    source: str = ""
    group_label: str = ""
    accepted_types: tuple[str, ...] = ()
    max_file_size_bytes: int | None = None
    allows_multiple_files: bool = False


@dataclass(frozen=True)
class FormResolution:
    ready: bool
    values: dict[str, str]
    sources: dict[str, str]
    skipped: tuple[str, ...]
    needs_input: tuple[str, ...]
    conflicts: tuple[str, ...]


def _key(value: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", value.casefold()))


def _field_text(field: FormField) -> str:
    return " ".join(part.strip() for part in (field.group_label, field.label, field.description, field.name, field.autocomplete) if part.strip())


def _profile_value(field: FormField, profile: Mapping[str, object]) -> tuple[str, str] | None:
    label = _key(_field_text(field))
    autocomplete = field.autocomplete.casefold()
    if field.kind == "email" or "email" in autocomplete or "email" in label or "e mail" in label:
        value = str(profile.get("email", "")).strip()
        return (value, "candidate_profile") if value else None
    name = str(profile.get("name", "")).strip()
    if "given name" in autocomplete or "first name" in label or "forename" in label:
        value = name.split(maxsplit=1)[0] if name else ""
        return (value, "candidate_profile") if value else None
    if "family name" in autocomplete or "last name" in label or "surname" in label:
        value = name.split(maxsplit=1)[-1] if " " in name else ""
        return (value, "candidate_profile") if value else None
    if "name" in autocomplete or any(term in label for term in ("full name", "legal name", "your name", "candidate name")):
        return (name, "candidate_profile") if name else None
    return None


def _known_facts(field: FormField, facts: Sequence[Mapping[str, object]], records: Mapping[str, Sequence[Mapping[str, object]]]) -> list[tuple[str, str]]:
    label = _key(_field_text(field))
    expected_types: set[str] = set()
    if field.kind == "tel" or any(term in label for term in ("phone", "telephone", "mobile")):
        expected_types.add("phone")
    if "language" in label or "languages" in label:
        expected_types.add("language")
    if any(term in label for term in ("licence", "license", "ajokortti", "card", "certificate", "certification", "passi")):
        expected_types.update({"licence", "certificate"})
    result: list[tuple[str, str]] = []
    for fact in facts:
        if fact.get("confirmed") and not fact.get("rejected") and str(fact.get("type", "")) in expected_types:
            value = str(fact.get("value", "")).strip()
            if value:
                result.append((value, str(fact.get("provenance", "USER_CONFIRMED"))))
    for record_type in ("licence", "certificate", "language"):
        if record_type not in expected_types:
            continue
        for record in records.get(record_type, ()):
            value = str(record.get("title") or record.get("name") or "").strip()
            if value:
                result.append((value, "USER_CONFIRMED_RECORD"))
    return result


def _same_scope(value: str, context: str) -> bool:
    return bool(value.strip() and context.strip() and _key(value) == _key(context))


_COUNTRY_LABELS = {
    "finland": {"finland", "suomi", "suomessa", "suomeen", "suomesta"},
    "sweden": {"sweden", "sverige", "ruotsi", "ruotsissa", "ruotsiin", "ruotsista"},
    "norway": {"norway", "norge", "norja", "norjassa", "norjaan"},
    "denmark": {"denmark", "danmark", "tanska", "tanskassa", "tanskaan"},
    "estonia": {"estonia", "eesti", "viro", "virossa", "viroon"},
}
_GENERIC_COUNTRY_REFERENCES = {
    "this country", "that country", "the country", "your country", "relevant country",
}


def _country_key(value: str) -> str:
    normalized = _key(value)
    for canonical, aliases in _COUNTRY_LABELS.items():
        if normalized == canonical or normalized in aliases:
            return canonical
    return normalized


def _explicit_work_country_matches(label: str, answer_country: str) -> bool:
    """If a form names a country, require that exact country-scoped answer."""
    normalized = _key(label)
    patterns = (
        r"(?:right to work|permission to work|work authorization|authorized to work|eligible to work)\s+(?:in|within)\s+(.+)",
        r"rätt att arbeta\s+i\s+(.+)",
        r"oikeus työskennellä\s+(.+)",
    )
    mentioned = ""
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            mentioned = match.group(1).strip()
            break
    if not mentioned or mentioned in _GENERIC_COUNTRY_REFERENCES or any(
        mentioned.startswith(reference + " ") for reference in _GENERIC_COUNTRY_REFERENCES
    ):
        return True
    for suffix in (" right now", " currently", " today", " now"):
        if mentioned.endswith(suffix):
            mentioned = mentioned[:-len(suffix)].strip()
    return bool(answer_country.strip() and _country_key(mentioned) == _country_key(answer_country))


def _not_expired(value: object) -> bool:
    expiry = str(value or "").strip()
    if not expiry:
        return True
    try:
        return date.fromisoformat(expiry[:10]) >= datetime.now(timezone.utc).date()
    except ValueError:
        return False


def _answer_matches_field(field: FormField, answer: Mapping[str, object]) -> bool:
    answer_question = str(answer.get("question", ""))
    answer_id = str(answer.get("question_id", ""))
    field_text = _field_text(field)
    field_question_id = question_id_for_form_label(field_text) or question_id_for_label(field_text)
    answer_question_id = answer_id or question_id_for_form_label(answer_question) or question_id_for_label(answer_question)
    if not field_question_id or answer_question_id.startswith("custom:") or answer_question_id != field_question_id:
        return False
    if field_question_id == "eligibility:work_permission" and not _explicit_work_country_matches(
        field_text, str(answer.get("scope_country", ""))
    ):
        return False
    return True


def _answer_in_scope(
    answer: Mapping[str, object], *, country: str, employer: str
) -> bool:
    scope_type = str(answer.get("scope_type", "GLOBAL"))
    if scope_type == "COUNTRY":
        answer_country = str(answer.get("scope_country", ""))
        return bool(answer_country.strip() and country.strip() and _country_key(answer_country) == _country_key(country))
    if scope_type == "EMPLOYER":
        return _same_scope(str(answer.get("scope_employer", "")), employer)
    return scope_type == "GLOBAL"


def _conflicting_answers(
    field: FormField,
    answers: Sequence[Mapping[str, object]],
    *,
    country: str,
    employer: str,
) -> bool:
    return any(
        answer.get("source") == "USER_CONFIRMED"
        and answer.get("answer_state") == "CONFLICT"
        and answer.get("category") in {"FACT", "PREFERENCE", "MOTIVATION"}
        and _answer_in_scope(answer, country=country, employer=employer)
        and _not_expired(answer.get("valid_until"))
        and _answer_matches_field(field, answer)
        for answer in answers
    )


def _confirmed_answers(
    field: FormField,
    answers: Sequence[Mapping[str, object]],
    *,
    country: str,
    employer: str,
) -> list[tuple[str, str]]:
    matches = []
    for answer in answers:
        if answer.get("source") != "USER_CONFIRMED":
            continue
        if answer.get("answer_state", "CONFIRMED") != "CONFIRMED":
            continue
        if not _answer_in_scope(answer, country=country, employer=employer):
            continue
        if not _not_expired(answer.get("valid_until")):
            continue
        if answer.get("category") not in {"FACT", "PREFERENCE", "MOTIVATION"}:
            continue
        if _answer_matches_field(field, answer):
            value = str(answer.get("value", "")).strip()
            if value:
                matches.append((value, "USER_CONFIRMED_ANSWER"))
    return matches


def _questionnaire_key(value: str) -> str | None:
    return question_id_for_label(value)


def _match_option(value: str, field: FormField) -> str | None:
    if field.kind not in {"select", "radio"} or not field.options:
        return value
    return next((option for option in field.options if _key(option) == _key(value)), None)


def resolve_application_fields(
    fields: Sequence[FormField],
    *,
    profile: Mapping[str, object],
    facts: Sequence[Mapping[str, object]],
    answers: Sequence[Mapping[str, object]],
    records: Mapping[str, Sequence[Mapping[str, object]]] | None = None,
    country: str = "",
    employer: str = "",
) -> FormResolution:
    values: dict[str, str] = {}
    sources: dict[str, str] = {}
    skipped: list[str] = []
    needs_input: list[str] = []
    conflicts: list[str] = []
    for field in fields:
        if _conflicting_answers(field, answers, country=country, employer=employer):
            conflicts.append(field.field_id)
            continue
        candidates: list[tuple[str, str]] = []
        profile_value = _profile_value(field, profile)
        if profile_value:
            candidates.append(profile_value)
        candidates.extend(_known_facts(field, facts, records or {}))
        candidates.extend(_confirmed_answers(field, answers, country=country, employer=employer))
        distinct: dict[str, tuple[str, str]] = {_key(value): (value, source) for value, source in candidates}
        if len(distinct) > 1:
            conflicts.append(field.field_id)
            continue
        if not distinct:
            if field.required:
                needs_input.append(field.field_id)
            else:
                skipped.append(field.field_id)
            continue
        value, source = next(iter(distinct.values()))
        option = _match_option(value, field)
        if option is None:
            if field.required:
                needs_input.append(field.field_id)
            else:
                skipped.append(field.field_id)
            continue
        values[field.field_id] = option
        sources[field.field_id] = source
    return FormResolution(not needs_input and not conflicts, values, sources, tuple(skipped), tuple(needs_input), tuple(conflicts))
