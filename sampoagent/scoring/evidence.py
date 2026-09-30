"""Shared lexical matching for confirmed candidate records and job text."""

import re


_GENERIC_RECORD_WORDS = {
    "a", "an", "and", "at", "as", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with",
    "experience", "experienced", "job", "position", "work", "worker", "responsible", "duties",
}


def record_text_matches(record_text: str, job_text: str) -> bool:
    """Return whether meaningful words in a candidate record overlap job text."""
    record_words = {
        word for word in re.findall(r"[^\W_]+", record_text.casefold(), flags=re.UNICODE)
        if len(word) > 2 and word not in _GENERIC_RECORD_WORDS
    }
    if not record_words:
        return False
    job_words = set(re.findall(r"[^\W_]+", job_text.casefold(), flags=re.UNICODE))
    overlap = len(record_words & job_words)
    minimum_overlap = 1 if len(record_words) <= 2 else (len(record_words) + 1) // 2
    return overlap >= minimum_overlap
