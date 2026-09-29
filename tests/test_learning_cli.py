import json
import sys

import pytest

from sampoagent.cli import main
from sampoagent.db.repository import Repository


def test_learning_summary_cli_requires_explicit_local_sharing_consent(tmp_path, monkeypatch, capsys):
    database_path = tmp_path / "learning.db"
    repository = Repository(database_path)
    repository.initialize()
    repository.connection.close()
    before = database_path.read_bytes()
    monkeypatch.setattr(sys, "argv", ["sampoagent", "learning-summary", "--database", str(database_path)])

    with pytest.raises(SystemExit, match="sharing is disabled"):
        main()

    assert capsys.readouterr().out == ""
    assert database_path.read_bytes() == before


def test_learning_summary_cli_does_not_create_database_for_a_missing_path(tmp_path, monkeypatch):
    database_path = tmp_path / "must-not-be-created.db"
    monkeypatch.setattr(sys, "argv", ["sampoagent", "learning-summary", "--database", str(database_path)])

    with pytest.raises(SystemExit, match="existing local database"):
        main()

    assert not database_path.exists()


def test_learning_summary_cli_outputs_only_digest_after_explicit_consent(tmp_path, monkeypatch, capsys):
    database_path = tmp_path / "learning-enabled.db"
    repository = Repository(database_path)
    repository.initialize()
    repository.set_setting("codex_learning_summary_enabled", "true")
    repository.connection.close()
    monkeypatch.setattr(sys, "argv", ["sampoagent", "learning-summary", "--database", str(database_path)])

    main()

    output = capsys.readouterr().out
    assert json.loads(output)["data_boundary"]["scope"] == "aggregated_confirmed_outcomes_only"
    assert "candidate_profile" not in output
