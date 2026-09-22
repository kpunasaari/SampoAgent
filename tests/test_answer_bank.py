from pathlib import Path


def test_factual_answer_bank_values_cannot_be_overwritten_by_generated_content(tmp_path: Path) -> None:
    from sampoagent.applications.answers import save_answer
    from sampoagent.db.repository import Repository

    repository = Repository(tmp_path / "agent.db")
    repository.initialize()
    answer_id = save_answer(repository, category="FACT", question="Driving licence", value="B driving licence", source="USER_CONFIRMED")

    assert save_answer(repository, category="GENERATED", question="Driving licence", value="No licence", source="AI", replace_id=answer_id) is None
    assert repository.answer(answer_id)["value"] == "B driving licence"
