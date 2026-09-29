"""Answer-bank guardrails for factual application information."""


def save_answer(
    repository: object,
    *,
    category: str,
    question: str,
    value: str,
    source: str,
    replace_id: int | None = None,
    scope_type: str | None = None,
    scope_country: str = "",
    scope_employer: str = "",
    valid_until: str | None = None,
) -> int | None:
    if replace_id is not None:
        existing = repository.answer(replace_id)
        if existing and existing["category"] == "FACT" and source != "USER_CONFIRMED":
            return None
        repository.update_answer(replace_id, category, question, value, source)
        return replace_id
    return repository.add_answer(
        category,
        question,
        value,
        source,
        scope_type=scope_type,
        scope_country=scope_country,
        scope_employer=scope_employer,
        valid_until=valid_until,
    )
