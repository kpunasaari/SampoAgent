"""Conservative extraction of explicitly stated monthly EUR salary information."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re


@dataclass(frozen=True)
class MonthlyEurSalary:
    minimum_eur: int | None
    maximum_eur: int | None
    qualifier: str


_NUMBER = (
    r"(?:\d{1,3}(?:[ \u00a0.]\d{3})+(?:[,.]\d{1,2})?"
    r"|\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?"
    r"|\d+(?:[,.]\d{1,2})?)"
)
_CURRENCY = r"(?:\bEUR\b|€|\beuros?\b|\beuroa\b)"
_SALARY_AMOUNT = re.compile(
    rf"(?P<prefix>{_CURRENCY})?\s*(?P<first>{_NUMBER})"
    rf"(?:\s*(?:-|–|—|\bto\b)\s*(?P<second_currency>{_CURRENCY})?\s*(?P<second>{_NUMBER}))?"
    rf"\s*(?P<suffix>{_CURRENCY})?",
    re.IGNORECASE,
)
_MONTHLY_PERIOD = re.compile(
    r"(?:\bmonthly\b|\bper\s+month\b|\ba\s+month\b|/\s*(?:month|mo\.?)\b"
    r"|/\s*kk\b|\bper\s+kk\b|\bkuukaudessa\b|\bkuukaudelta\b|\bkuussa\b"
    r"|\bkuukausipalk(?:ka|an|kaa)\b|\bkuukausittain\b"
    r"|\bper\s+månad(?:en)?\b|\bi\s+månaden\b|/\s*månad(?:en)?\b"
    r"|\bmånadslön\b|\bmånadsvis\b)",
    re.IGNORECASE,
)
_LOWER_BOUND = re.compile(
    r"\b(?:from|starting\s+at|at\s+least|minimum(?:\s+of)?|alkaen|vähintään|minst|från)\b",
    re.IGNORECASE,
)
_UPPER_BOUND = re.compile(
    r"\b(?:up\s+to|maximum(?:\s+of)?|at\s+most|enintään|korkeintaan|högst|upp\s+till)\b",
    re.IGNORECASE,
)


def _parse_amount(value: str) -> int | None:
    compact = re.sub(r"[\s\u00a0]", "", value)
    comma = compact.rfind(",")
    period = compact.rfind(".")
    if comma >= 0 and period >= 0:
        decimal_mark = "," if comma > period else "."
        grouping_mark = "." if decimal_mark == "," else ","
        integer, fraction = compact.rsplit(decimal_mark, 1)
        if len(fraction) == 3:
            compact = integer.replace(grouping_mark, "").replace(decimal_mark, "") + fraction
        elif len(fraction) in {1, 2}:
            compact = integer.replace(grouping_mark, "") + "." + fraction
        else:
            return None
    elif comma >= 0 or period >= 0:
        mark = "," if comma >= 0 else "."
        integer, fraction = compact.rsplit(mark, 1)
        if len(fraction) == 3:
            compact = integer.replace(mark, "") + fraction
        elif len(fraction) in {1, 2}:
            compact = integer + "." + fraction
        else:
            return None
    try:
        amount = int(Decimal(compact))
    except (InvalidOperation, ValueError):
        return None
    return amount if 0 <= amount <= 2_000_000 else None


def parse_monthly_eur_salary(text: str) -> MonthlyEurSalary | None:
    """Return salary bounds only when EUR and a monthly period are explicit.

    Unknown currencies and hourly/yearly pay are intentionally left unparsed.
    Conflicting monthly amounts are treated as ambiguous rather than guessed.
    """
    candidates: list[MonthlyEurSalary] = []
    for match in _SALARY_AMOUNT.finditer(text):
        if not any(match.group(name) for name in ("prefix", "second_currency", "suffix")):
            continue
        context_start = max(0, match.start() - 32)
        context_end = min(len(text), match.end() + 32)
        if not _MONTHLY_PERIOD.search(text[context_start:context_end]):
            continue
        first = _parse_amount(match.group("first"))
        second_text = match.group("second")
        second = _parse_amount(second_text) if second_text else None
        if first is None or (second_text and second is None):
            continue
        before = text[max(0, match.start() - 50):match.start()]
        if second is not None:
            if second < first:
                continue
            candidates.append(MonthlyEurSalary(first, second, "range"))
        elif _LOWER_BOUND.search(before):
            candidates.append(MonthlyEurSalary(first, None, "minimum"))
        elif _UPPER_BOUND.search(before):
            candidates.append(MonthlyEurSalary(None, first, "maximum"))
        else:
            candidates.append(MonthlyEurSalary(first, first, "exact"))

    distinct = {(item.minimum_eur, item.maximum_eur, item.qualifier) for item in candidates}
    if len(distinct) != 1:
        return None
    return candidates[0]


def format_monthly_eur_salary(text: str) -> str:
    salary = parse_monthly_eur_salary(text)
    if salary is None:
        return "Not stated or not comparable"
    if salary.qualifier == "minimum":
        return f"At least €{salary.minimum_eur:,} / month"
    if salary.qualifier == "maximum":
        return f"Up to €{salary.maximum_eur:,} / month"
    if salary.minimum_eur == salary.maximum_eur:
        return f"€{salary.minimum_eur:,} / month"
    return f"€{salary.minimum_eur:,}–€{salary.maximum_eur:,} / month"
