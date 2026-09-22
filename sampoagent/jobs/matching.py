"""Exact, explainable candidate-to-requirement checks."""


def hard_requirement_failures(*, required: list[str], confirmed_facts: list[str]) -> list[str]:
    confirmed = {value.casefold() for value in confirmed_facts}
    return [f"Missing mandatory requirement: {requirement}" for requirement in required if requirement.casefold() not in confirmed]
