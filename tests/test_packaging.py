from pathlib import Path
import tomllib


def test_setuptools_package_discovery_includes_only_sampoagent() -> None:
    config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert config["tool"]["setuptools"]["packages"]["find"]["include"] == ["sampoagent*"]
