# Contributing

Use Python 3.11+ and run `py -m pytest -q` before opening a pull request. Add tests before production behavior. Keep job-source adapters compliant with their terms and robots rules, preserve candidate fact provenance, and never add real candidate data or secrets to fixtures. Changes to Codex workflows should update the relevant skill under `.agents/skills/` and keep the top-level `sampoagent` skill aligned with the focused skills. The project and its skills are distributed under Apache-2.0; retain the existing `LICENSE` and `NOTICE` when redistributing.
