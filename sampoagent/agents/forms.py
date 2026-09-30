"""Versioned semantic snapshots for safely reviewing employer forms."""

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from urllib.parse import urlsplit

from sampoagent.applications.field_resolver import FormField


_FILE_SIZE = re.compile(r"(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>bytes?|b|kib|kb|mib|mb|gib|gb)\b", re.IGNORECASE)


def parse_file_size_limit(value: str) -> int | None:
    """Parse an explicitly stated byte limit; ambiguous bare numbers are ignored."""
    match = _FILE_SIZE.search(value)
    if not match:
        return None
    unit = match.group("unit").casefold()
    multiplier = {
        "b": 1, "byte": 1, "bytes": 1,
        "kb": 1000, "kib": 1024,
        "mb": 1000**2, "mib": 1024**2,
        "gb": 1000**3, "gib": 1024**3,
    }[unit]
    return int(float(match.group("amount")) * multiplier)


@dataclass(frozen=True)
class FormSchema:
    schema_version: str
    page_url: str
    page_origin: str
    action_url: str
    navigation_checkpoint: str
    fields: tuple[FormField, ...]
    has_next_step: bool
    single_form_context: bool
    signature: str

    @classmethod
    def build(
        cls,
        *,
        fields: tuple[FormField, ...],
        page_url: str,
        action_url: str = "",
        navigation_checkpoint: str = "",
        has_next_step: bool = False,
        single_form_context: bool = True,
    ) -> "FormSchema":
        parsed = urlsplit(page_url)
        origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
        field_payload = [
            {
                "id": field.field_id,
                "name": field.name,
                "label": field.label,
                "group_label": field.group_label,
                "description": field.description,
                "source": field.source,
                "required": field.required,
                "kind": field.kind,
                "options": field.options,
                "autocomplete": field.autocomplete,
                "accepted_types": field.accepted_types,
                "max_file_size_bytes": field.max_file_size_bytes,
                "allows_multiple_files": field.allows_multiple_files,
                "constraints": field.constraints,
            }
            for field in fields
        ]
        payload = {
            "schema_version": "1.2",
            "page_origin": origin,
            "action_url": action_url,
            "navigation_checkpoint": navigation_checkpoint,
            "has_next_step": has_next_step,
            "single_form_context": single_form_context,
            "fields": field_payload,
        }
        signature = sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        return cls(
            schema_version="1.2",
            page_url=page_url,
            page_origin=origin,
            action_url=action_url,
            navigation_checkpoint=navigation_checkpoint,
            fields=fields,
            has_next_step=has_next_step,
            single_form_context=single_form_context,
            signature=signature,
        )
