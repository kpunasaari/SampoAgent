"""Small SQLite repository. Candidate data never leaves the local device."""

from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, DecimalException, InvalidOperation
from email.utils import parseaddr
from hashlib import sha256
from pathlib import Path
import sqlite3
import json
import hmac
import re
from urllib.parse import urlsplit

from sampoagent.candidate.questions import (
    ANSWER_STATES,
    AnswerConfirmation,
    answer_state_for_value,
    question_id_for_label,
    question_metadata,
)
from sampoagent.careers.taxonomy import TaxonomyOccupation, TaxonomyOccupationSkill


_SAFE_ACTIVITY_ACTIONS = frozenset({
    "application_email_draft_cancelled", "application_email_draft_created", "application_email_interrupted",
    "application_email_send_finished", "application_email_send_reserved", "application_package_approved",
    "application_package_waiting_review", "application_preparation_claimed", "application_preparation_released",
    "application_preparations_recovered", "application_resumed", "application_status_changed",
    "application_submit_cancelled_preclick", "application_submit_reserved", "activity_details_redacted", "autopilot_granted",
    "autopilot_revoked", "candidate_record_added", "candidate_record_deleted", "candidate_record_updated",
    "captcha_task_completed", "captcha_task_created", "captcha_task_started", "career_profile_added",
    "career_profile_deleted", "career_profile_enabled", "career_profile_updated", "confirmed_fact_added",
    "cv_record_needs_review", "cv_record_reviewed", "cv_template_registered", "demo_loaded",
    "email_send_autopilot_granted", "esco_taxonomy_imported", "fact_confirmed", "fact_deleted",
    "fact_edited", "fact_rejected", "job_imported", "job_language_override", "job_override", "job_verified",
    "mail_send_account_disconnected", "mail_send_connected", "mail_send_disconnected", "mailbox_connected",
    "mailbox_disconnected", "onboarding_answers_confirmed", "queue_created", "semantic_cache_cleared",
    "setting_changed", "skill_added", "source_added", "source_deleted", "source_enabled", "source_updated",
    "target_occupation_approved", "target_occupation_deleted", "target_occupation_enabled",
    "target_occupations_replaced",
})
_SAFE_ACTIVITY_DETAIL = "Activity details omitted to protect privacy."
_AUTOPILOT_POLICY_VERSION = "stepwise-form-save-v1"
_APPLICATION_ANSWER_KINDS = frozenset({"text", "textarea", "email", "tel", "number", "date", "select", "radio"})
_APPLICATION_ANSWER_SECRETS = ("password", "passwd", "access token", "api key", "secret=")
_APPLICATION_ANSWER_CONSTRAINTS = ("min", "max", "step", "pattern", "minlength", "maxlength", "step_base")
_APPLICATION_EMAIL = re.compile(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*")
_APPLICATION_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")


def _split_candidate_values(value: str) -> list[str]:
    """Split the explicit multi-skill list without guessing at full sentences."""
    parts = re.split(r"[\r\n,;|·•]+", value)
    unique: dict[str, str] = {}
    for part in parts:
        clean = re.sub(r"^\s*[-*•]\s*", "", part).strip()
        clean = " ".join(clean.split())
        if not clean or len(clean) > 150:
            continue
        unique.setdefault(clean.casefold(), clean)
        if len(unique) >= 40:
            break
    return list(unique.values())


class Repository:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row

    @classmethod
    def open_read_only(cls, path: str | Path) -> "Repository":
        """Open an existing local database without migration or write access."""
        resolved = Path(path).expanduser().resolve(strict=True)
        if not resolved.is_file():
            raise FileNotFoundError("An existing local database file is required")
        repository = cls.__new__(cls)
        repository.path = str(resolved)
        repository.connection = sqlite3.connect(
            f"{resolved.as_uri()}?mode=ro", uri=True, check_same_thread=False
        )
        repository.connection.row_factory = sqlite3.Row
        repository.connection.execute("PRAGMA query_only = ON")
        return repository

    def initialize(self) -> None:
        self.connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS candidate_profile (id INTEGER PRIMARY KEY CHECK(id=1), name TEXT NOT NULL, email TEXT, locale TEXT NOT NULL DEFAULT 'en');
            CREATE TABLE IF NOT EXISTS facts (id INTEGER PRIMARY KEY, type TEXT NOT NULL, value TEXT NOT NULL, provenance TEXT NOT NULL, source_id TEXT, confidence REAL NOT NULL, confirmed INTEGER NOT NULL DEFAULT 0, rejected INTEGER NOT NULL DEFAULT 0, evidence_json TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS candidate_records (id INTEGER PRIMARY KEY, record_type TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS cv_templates (id INTEGER PRIMARY KEY, name TEXT NOT NULL, language TEXT NOT NULL, role_family TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS career_profiles (id INTEGER PRIMARY KEY, name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, notes TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS target_occupations (id INTEGER PRIMARY KEY, title_en TEXT NOT NULL, title_fi TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, UNIQUE(title_en, title_fi));
            CREATE TABLE IF NOT EXISTS esco_taxonomy_metadata (id INTEGER PRIMARY KEY CHECK(id=1), version TEXT NOT NULL, languages TEXT NOT NULL, source_url TEXT NOT NULL, license_statement TEXT NOT NULL, attribution TEXT NOT NULL, modified TEXT NOT NULL, quality_notice TEXT NOT NULL, source_sha256 TEXT NOT NULL, imported_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS esco_occupations (concept_uri TEXT NOT NULL, language TEXT NOT NULL, label TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', isco_code TEXT NOT NULL DEFAULT '', PRIMARY KEY(concept_uri, language));
            CREATE TABLE IF NOT EXISTS esco_skills (concept_uri TEXT NOT NULL, language TEXT NOT NULL, label TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', PRIMARY KEY(concept_uri, language));
            CREATE TABLE IF NOT EXISTS esco_occupation_skills (occupation_uri TEXT NOT NULL, skill_uri TEXT NOT NULL, importance TEXT NOT NULL CHECK(importance IN ('ESSENTIAL','OPTIONAL','UNSPECIFIED')), PRIMARY KEY(occupation_uri, skill_uri));
            CREATE TABLE IF NOT EXISTS job_sources (id INTEGER PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL, country TEXT NOT NULL, source_type TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1, capability TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY, title TEXT NOT NULL, company TEXT NOT NULL, location TEXT, language TEXT NOT NULL, description TEXT NOT NULL, application_url TEXT NOT NULL, fingerprint TEXT UNIQUE NOT NULL, verification_state TEXT NOT NULL, deadline TEXT, source_id INTEGER, source_name TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL DEFAULT '', verified_at TEXT);
            CREATE TABLE IF NOT EXISTS job_verifications (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL, method TEXT NOT NULL, source_id INTEGER, source_url TEXT NOT NULL, evidence_summary TEXT NOT NULL, verified_at TEXT NOT NULL, snapshot_hash TEXT NOT NULL DEFAULT '', FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS job_overrides (job_id INTEGER PRIMARY KEY, decision TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, FOREIGN KEY(job_id) REFERENCES jobs(id));
            CREATE TABLE IF NOT EXISTS applications (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL, status TEXT NOT NULL, queue_state TEXT NOT NULL, language TEXT NOT NULL, cv_path TEXT, notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, FOREIGN KEY(job_id) REFERENCES jobs(id));
            CREATE TABLE IF NOT EXISTS application_reviews (application_id INTEGER PRIMARY KEY, package_hash TEXT NOT NULL, package_json TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('WAITING','APPROVED')), created_at TEXT NOT NULL, approved_at TEXT, FOREIGN KEY(application_id) REFERENCES applications(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS application_form_answers (id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL, field_id TEXT NOT NULL, form_signature TEXT NOT NULL, listing_hash TEXT NOT NULL, label TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL, options_json TEXT NOT NULL DEFAULT '[]', constraints_json TEXT NOT NULL DEFAULT '{}', required INTEGER NOT NULL DEFAULT 1, risk TEXT NOT NULL CHECK(risk IN ('LOW','MEDIUM')), state TEXT NOT NULL DEFAULT 'PENDING' CHECK(state IN ('PENDING','ANSWERED','STALE')), answer_value TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, answered_at TEXT, UNIQUE(application_id, form_signature, listing_hash, field_id), FOREIGN KEY(application_id) REFERENCES applications(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS submission_evidence (id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL, final_url TEXT NOT NULL, confirmation_message TEXT NOT NULL, confirmation_id TEXT, agent_provider TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS answer_bank (id INTEGER PRIMARY KEY, category TEXT NOT NULL, question TEXT NOT NULL, value TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL, question_id TEXT NOT NULL DEFAULT '', answer_state TEXT NOT NULL DEFAULT 'DRAFT', value_type TEXT NOT NULL DEFAULT 'text', sensitivity TEXT NOT NULL DEFAULT 'NORMAL', scope_type TEXT NOT NULL DEFAULT 'GLOBAL', scope_country TEXT NOT NULL DEFAULT '', scope_employer TEXT NOT NULL DEFAULT '', valid_until TEXT, confirmed_at TEXT, source_ref TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS application_timeline (id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL, status TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY(application_id) REFERENCES applications(id));
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS activity_log (id INTEGER PRIMARY KEY, action TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS semantic_cache (cache_key TEXT PRIMARY KEY, value TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ai_usage (id INTEGER PRIMARY KEY, provider TEXT NOT NULL, model TEXT NOT NULL, feature TEXT NOT NULL, input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0, cached_tokens INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS documents (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, path TEXT NOT NULL, checksum TEXT, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS discovery_runs (id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL, query_count INTEGER NOT NULL DEFAULT 0, jobs_found INTEGER NOT NULL DEFAULT 0, imported_count INTEGER NOT NULL DEFAULT 0, duplicates_count INTEGER NOT NULL DEFAULT 0, summary TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS discovery_source_results (id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, source_id INTEGER, source_name TEXT NOT NULL, source_url TEXT NOT NULL, capability TEXT NOT NULL, status TEXT NOT NULL, jobs_found INTEGER NOT NULL DEFAULT 0, imported_count INTEGER NOT NULL DEFAULT 0, duplicates_count INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, FOREIGN KEY(run_id) REFERENCES discovery_runs(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS discovery_source_state (source_id INTEGER PRIMARY KEY, consecutive_failures INTEGER NOT NULL DEFAULT 0, last_status TEXT NOT NULL DEFAULT '', last_message TEXT NOT NULL DEFAULT '', last_checked_at TEXT, last_success_at TEXT, next_attempt_at TEXT, FOREIGN KEY(source_id) REFERENCES job_sources(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS mailbox_connection (id INTEGER PRIMARY KEY CHECK(id=1), provider TEXT NOT NULL, token_ciphertext TEXT NOT NULL, connected_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS mail_send_connection (id INTEGER PRIMARY KEY CHECK(id=1), provider TEXT NOT NULL, token_ciphertext TEXT NOT NULL, granted_scopes TEXT NOT NULL, connected_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS mail_send_accounts (id INTEGER PRIMARY KEY, provider TEXT NOT NULL CHECK(provider IN ('gmail','microsoft')), provider_subject TEXT NOT NULL, sender_email TEXT NOT NULL, address_status TEXT NOT NULL CHECK(address_status IN ('verified','provider_reported')), token_ciphertext TEXT NOT NULL, granted_scopes TEXT NOT NULL, connected_at TEXT NOT NULL, token_expires_at TEXT NOT NULL DEFAULT '', UNIQUE(provider, provider_subject));
            CREATE TABLE IF NOT EXISTS email_outbox (id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL UNIQUE, provider TEXT NOT NULL CHECK(provider IN ('gmail','microsoft')), idempotency_key TEXT NOT NULL UNIQUE, package_hash TEXT NOT NULL, payload_ciphertext TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('READY','SENDING','ACCEPTED','UNKNOWN','FAILED_FINAL','CANCELLED')), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, provider_reference TEXT NOT NULL DEFAULT '', message TEXT NOT NULL DEFAULT '', FOREIGN KEY(application_id) REFERENCES applications(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS mailbox_messages (id INTEGER PRIMARY KEY, provider TEXT NOT NULL, provider_message_id TEXT NOT NULL, sender TEXT NOT NULL, subject TEXT NOT NULL, snippet TEXT NOT NULL, received_at TEXT NOT NULL, link TEXT NOT NULL, reviewed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, UNIQUE(provider, provider_message_id));
            CREATE TABLE IF NOT EXISTS captcha_tasks (id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL UNIQUE, detected_url TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'WAITING_USER', note TEXT NOT NULL DEFAULT '', outcome TEXT, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, FOREIGN KEY(application_id) REFERENCES applications(id));
            CREATE TABLE IF NOT EXISTS application_claims (job_id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL UNIQUE, FOREIGN KEY(job_id) REFERENCES jobs(id), FOREIGN KEY(application_id) REFERENCES applications(id));
            CREATE TABLE IF NOT EXISTS cv_archive (id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, checksum TEXT NOT NULL, language TEXT NOT NULL, role_family TEXT NOT NULL, source_job_id INTEGER, fit_score INTEGER NOT NULL DEFAULT 0, ats_score INTEGER NOT NULL DEFAULT 0, text_check_performed INTEGER NOT NULL DEFAULT 0, text_check_score INTEGER NOT NULL DEFAULT 0, strategy TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY(source_job_id) REFERENCES jobs(id));
            CREATE TABLE IF NOT EXISTS application_learning (application_id INTEGER PRIMARY KEY, role_family TEXT NOT NULL, outcome TEXT NOT NULL, observed_at TEXT NOT NULL, FOREIGN KEY(application_id) REFERENCES applications(id));
            CREATE TABLE IF NOT EXISTS application_attempts (id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL, state TEXT NOT NULL, package_hash TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, message TEXT NOT NULL DEFAULT '', FOREIGN KEY(application_id) REFERENCES applications(id));
            CREATE TABLE IF NOT EXISTS automation_worker_lease (id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT NOT NULL, expires_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS automation_worker_status (id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT, status TEXT NOT NULL, started_at TEXT, last_heartbeat TEXT, next_run_at TEXT, last_result TEXT NOT NULL DEFAULT '');
            """
        )
        application_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(applications)")}
        if "preparation_token" not in application_columns:
            self.connection.execute("ALTER TABLE applications ADD COLUMN preparation_token TEXT")
        application_answer_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(application_form_answers)")}
        if "constraints_json" not in application_answer_columns:
            self.connection.execute("ALTER TABLE application_form_answers ADD COLUMN constraints_json TEXT NOT NULL DEFAULT '{}'")
        outbox_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(email_outbox)")}
        if "sender_account_id" not in outbox_columns:
            self.connection.execute("ALTER TABLE email_outbox ADD COLUMN sender_account_id INTEGER")
        self.connection.execute("INSERT OR IGNORE INTO application_claims(job_id, application_id) SELECT job_id, MIN(id) FROM applications GROUP BY job_id")
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(job_sources)")}
        if "notes" not in columns:
            self.connection.execute("ALTER TABLE job_sources ADD COLUMN notes TEXT NOT NULL DEFAULT ''")
        if "capability" not in columns:
            self.connection.execute("ALTER TABLE job_sources ADD COLUMN capability TEXT NOT NULL DEFAULT 'Browser search only'")
        if "listing_selector" not in columns:
            self.connection.execute("ALTER TABLE job_sources ADD COLUMN listing_selector TEXT NOT NULL DEFAULT ''")
        if "terms_url" not in columns:
            self.connection.execute("ALTER TABLE job_sources ADD COLUMN terms_url TEXT NOT NULL DEFAULT ''")
        if "terms_reviewed" not in columns:
            self.connection.execute("ALTER TABLE job_sources ADD COLUMN terms_reviewed INTEGER NOT NULL DEFAULT 0")
        if "terms_reviewed_at" not in columns:
            self.connection.execute("ALTER TABLE job_sources ADD COLUMN terms_reviewed_at TEXT")
        cv_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(cv_archive)")}
        if "ats_score" not in cv_columns:
            self.connection.execute("ALTER TABLE cv_archive ADD COLUMN ats_score INTEGER NOT NULL DEFAULT 0")
        if "text_check_performed" not in cv_columns:
            self.connection.execute("ALTER TABLE cv_archive ADD COLUMN text_check_performed INTEGER NOT NULL DEFAULT 0")
        if "text_check_score" not in cv_columns:
            self.connection.execute("ALTER TABLE cv_archive ADD COLUMN text_check_score INTEGER NOT NULL DEFAULT 0")
            self.connection.execute(
                "UPDATE cv_archive SET text_check_score=ats_score WHERE text_check_performed=1"
            )
        fact_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(facts)")}
        if "evidence_json" not in fact_columns:
            self.connection.execute("ALTER TABLE facts ADD COLUMN evidence_json TEXT NOT NULL DEFAULT ''")
        job_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(jobs)")}
        for name, declaration in (
            ("source_id", "INTEGER"),
            ("source_name", "TEXT NOT NULL DEFAULT ''"),
            ("source_url", "TEXT NOT NULL DEFAULT ''"),
            ("verified_at", "TEXT"),
        ):
            if name not in job_columns:
                self.connection.execute(f"ALTER TABLE jobs ADD COLUMN {name} {declaration}")
        verification_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(job_verifications)")}
        if "snapshot_hash" not in verification_columns:
            self.connection.execute("ALTER TABLE job_verifications ADD COLUMN snapshot_hash TEXT NOT NULL DEFAULT ''")
        answer_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(answer_bank)")}
        for name, declaration in (
            ("question_id", "TEXT NOT NULL DEFAULT ''"),
            ("answer_state", "TEXT NOT NULL DEFAULT 'DRAFT'"),
            ("value_type", "TEXT NOT NULL DEFAULT 'text'"),
            ("sensitivity", "TEXT NOT NULL DEFAULT 'NORMAL'"),
            ("scope_type", "TEXT NOT NULL DEFAULT 'GLOBAL'"),
            ("scope_country", "TEXT NOT NULL DEFAULT ''"),
            ("scope_employer", "TEXT NOT NULL DEFAULT ''"),
            ("valid_until", "TEXT"),
            ("confirmed_at", "TEXT"),
            ("source_ref", "TEXT NOT NULL DEFAULT ''"),
        ):
            if name not in answer_columns:
                self.connection.execute(f"ALTER TABLE answer_bank ADD COLUMN {name} {declaration}")
        self.migrate_answer_provenance()
        self.migrate_legacy_answer_facts()
        self.connection.execute(
            "UPDATE jobs SET verification_state='PARTIALLY_VERIFIED' WHERE verification_state='VERIFIED' "
            "AND NOT EXISTS (SELECT 1 FROM job_verifications v WHERE v.job_id=jobs.id)"
        )
        for key, value in {
            "application_mode": "review_everything",
            "daily_limit": "0",
            "ai_usage_mode": "minimal",
            "dry_run": "true",
            "autopilot_authorized": "false",
            "autopilot_grant_policy_version": "",
            "autopilot_grant_fingerprint": "",
            "autopilot_grant_expires_at": "",
            "email_send_autopilot_authorized": "false",
            "email_send_autopilot_fingerprint": "",
            "email_send_autopilot_expires_at": "",
            "mail_send_default_account_id": "",
            "automation_paused": "false",
            "ocr_provider": "environment",
        }.items():
            self.connection.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, value)
            )
        self.migrate_unambiguous_candidate_fact_links()
        self.seed_builtin_sources()
        self.connection.commit()

    def migrate_answer_provenance(self) -> None:
        """Backfill answer metadata without treating unscoped sensitive answers as reusable."""
        rows = self.connection.execute(
            "SELECT id, category, question, value, source, created_at, question_id, answer_state "
            "FROM answer_bank WHERE question_id='' OR answer_state='DRAFT'"
        ).fetchall()
        for row in rows:
            question = str(row["question"])
            value = str(row["value"])
            source = str(row["source"])
            question_id = str(row["question_id"] or question_id_for_label(question) or "")
            metadata = question_metadata(question_id) if question_id else None
            if not question_id:
                question_id = "custom:" + sha256(" ".join(question.casefold().split()).encode()).hexdigest()[:20]
            state = answer_state_for_value(value) if source == "USER_CONFIRMED" else "DRAFT"
            scope_type = metadata.scope_type if metadata else "GLOBAL"
            if source == "USER_CONFIRMED" and metadata and not metadata.reusable:
                state = "NON_REUSABLE"
            elif source == "USER_CONFIRMED" and metadata is None:
                state = "NEEDS_RECONFIRMATION"
            elif state == "CONFIRMED" and scope_type == "COUNTRY":
                # Legacy rows have no reliable country evidence; do not guess their jurisdiction.
                state = "NEEDS_RECONFIRMATION"
            confirmed_at = str(row["created_at"] or "") if state in {
                "CONFIRMED", "DECLINED", "UNKNOWN", "NEEDS_RECONFIRMATION", "EXPIRED", "NON_REUSABLE"
            } else None
            self.connection.execute(
                "UPDATE answer_bank SET category=?, question_id=?, answer_state=?, value_type=?, sensitivity=?, "
                "scope_type=?, confirmed_at=COALESCE(confirmed_at, ?), source_ref=CASE WHEN source_ref='' THEN 'legacy' ELSE source_ref END "
                "WHERE id=?",
                (
                    metadata.category if metadata else str(row["category"]),
                    question_id,
                    state,
                    metadata.value_type if metadata else "text",
                    metadata.sensitivity if metadata else "NORMAL",
                    scope_type,
                    confirmed_at,
                    int(row["id"]),
                ),
            )

    def migrate_legacy_answer_facts(self) -> None:
        """Materialize recognized legacy skill answers once, retaining their answer-bank source."""
        rows = self.connection.execute(
            "SELECT id, question_id, value FROM answer_bank WHERE source_ref='legacy' AND answer_state='CONFIRMED'"
        ).fetchall()
        for row in rows:
            answer_id = int(row["id"])
            metadata = question_metadata(str(row["question_id"]))
            if metadata and metadata.candidate_fact_type:
                self._materialize_answer_facts(answer_id, metadata.candidate_fact_type, str(row["value"]))
            self.connection.execute(
                "UPDATE answer_bank SET source_ref='legacy_migrated' WHERE id=?", (answer_id,)
            )

    def _materialize_answer_facts(self, answer_id: int, fact_type: str, value: str) -> None:
        source_id = f"answer_bank:{answer_id}"
        for candidate_value in _split_candidate_values(value):
            existing = self.connection.execute(
                "SELECT 1 FROM facts WHERE type=? AND lower(trim(value))=lower(trim(?)) "
                "AND confirmed=1 AND rejected=0 LIMIT 1",
                (fact_type, candidate_value),
            ).fetchone()
            if existing:
                continue
            self.connection.execute(
                "INSERT INTO facts(type,value,provenance,source_id,confidence,confirmed,rejected) "
                "VALUES (?,?, 'USER_CONFIRMED', ?, 1, 1, 0)",
                (fact_type, candidate_value, source_id),
            )

    def migrate_unambiguous_candidate_fact_links(self) -> None:
        """Associate legacy UI-derived facts only when record/fact matching is one-to-one."""
        records = self.connection.execute("SELECT id, record_type, payload FROM candidate_records ORDER BY id").fetchall()
        for record in records:
            title = str(json.loads(record[2]).get("title", "")).strip()
            if not title:
                continue
            linked_source = f"candidate_record:{int(record[0])}"
            if self.connection.execute("SELECT 1 FROM facts WHERE source_id=? LIMIT 1", (linked_source,)).fetchone():
                continue
            params = (record[1], title)
            record_count = int(self.connection.execute("SELECT COUNT(*) FROM candidate_records WHERE record_type=? AND json_extract(payload, '$.title')=?", params).fetchone()[0])
            facts = self.connection.execute("SELECT id FROM facts WHERE type=? AND value=? AND provenance='USER_CONFIRMED' AND source_id='profile'", params).fetchall()
            if record_count == 1 and len(facts) == 1:
                self.connection.execute("UPDATE facts SET source_id=? WHERE id=?", (linked_source, int(facts[0][0])))

    def seed_builtin_sources(self) -> None:
        """Insert the country-pack catalog without changing existing source choices."""
        if self.setting("builtin_sources_seeded") == "true":
            return
        from sampoagent.country_packs.finland import builtin_sources

        for source in builtin_sources():
            exists = self.connection.execute(
                "SELECT 1 FROM job_sources WHERE url=? LIMIT 1", (source.url,)
            ).fetchone()
            if not exists:
                self.connection.execute(
                    "INSERT INTO job_sources(name, url, country, source_type, notes, enabled, capability) VALUES (?, ?, ?, ?, ?, 1, ?)",
                    (
                        source.name,
                        source.url,
                        "Finland",
                        source.source_type,
                        "Bundled Finland source; capability is shown honestly and can be changed by the user.",
                        source.capability,
                    ),
                )
        self.connection.execute(
            "INSERT INTO settings(key, value) VALUES ('builtin_sources_seeded', 'true') "
            "ON CONFLICT(key) DO UPDATE SET value='true'"
        )

    def load_demo(self) -> None:
        if self.profile():
            return
        self.connection.execute("INSERT INTO candidate_profile(id, name, email, locale) VALUES(1, ?, ?, ?)", ("Aino Example", "aino@example.test", "en"))
        self.connection.executemany("INSERT INTO facts(type, value, provenance, source_id, confidence, confirmed) VALUES (?, ?, 'USER_CONFIRMED', 'demo', 1, 1)", [("skill", "forklift operation"), ("skill", "customer service"), ("language", "Finnish"), ("language", "English")])
        self.connection.executemany("INSERT INTO career_profiles(name, notes) VALUES (?, ?)", [("Logistics", "Synthetic demo career profile"), ("Customer Service", "Synthetic demo career profile")])
        jobs = [("Warehouse Worker", "Northern Logistics Oy", "Vantaa", "en", "Synthetic demo: forklift operation and Finnish required.", "https://example.test/apply/warehouse", "demo-warehouse", "PARTIALLY_VERIFIED"), ("Asiakaspalvelija", "Example Services Oy", "Helsinki", "fi", "Synteettinen demo: asiakaspalvelu ja englanti.", "https://example.test/apply/service", "demo-service", "PARTIALLY_VERIFIED")]
        self.connection.executemany("INSERT INTO jobs(title, company, location, language, description, application_url, fingerprint, verification_state) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", jobs)
        for key, value in {"application_mode": "review_everything", "daily_limit": "5", "ai_usage_mode": "minimal", "dry_run": "true"}.items():
            self.connection.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
        self.log("demo_loaded", "Synthetic demo data loaded")
        self.connection.commit()

    def profile(self) -> dict[str, str] | None:
        row = self.connection.execute("SELECT name, email, locale FROM candidate_profile WHERE id=1").fetchone()
        return dict(row) if row else None

    def save_profile(self, name: str, locale: str, email: str = "") -> None:
        if locale not in {"fi", "en"}:
            raise ValueError("Unsupported locale")
        self.connection.execute("INSERT INTO candidate_profile(id, name, email, locale) VALUES(1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, email=excluded.email, locale=excluded.locale", (name.strip(), email.strip(), locale))
        self.set_setting("dry_run", "true")
        self.revoke_autopilot("Candidate profile changed")
        self.set_setting("onboarding_complete", "true")
        self.connection.commit()

    def confirmed_skills(self) -> list[str]:
        return [row[0] for row in self.connection.execute("SELECT value FROM facts WHERE type='skill' AND confirmed=1 AND rejected=0 ORDER BY value")]

    def recommendation_skills(self) -> list[str]:
        """Include transferable abilities for role suggestions, but not as CV work skills."""
        return [
            str(row[0])
            for row in self.connection.execute(
                "SELECT value FROM facts WHERE type IN ('skill','transferable_skill') "
                "AND confirmed=1 AND rejected=0 ORDER BY value"
            )
        ]

    def confirmed_fact_values(self) -> list[str]:
        """Return only user-confirmed, non-rejected facts for safety decisions."""
        return [
            row[0]
            for row in self.connection.execute(
                "SELECT value FROM facts WHERE confirmed=1 AND rejected=0 ORDER BY value"
            )
        ]

    def add_candidate_record(self, record_type: str, payload: dict[str, str]) -> int:
        if record_type not in {"experience", "education", "certificate", "licence", "language", "availability", "preference", "answer_bank"}:
            raise ValueError("Unsupported candidate record type")
        for key in ("start_date", "end_date"):
            value = str(payload.get(key, "")).strip()
            if value and not re.fullmatch(r"\d{4}(?:-(?:0[1-9]|1[0-2]))?", value):
                raise ValueError("Candidate history dates must use YYYY or YYYY-MM")
        if payload.get("is_current") and payload.get("end_date"):
            raise ValueError("Current roles cannot also have an end date")
        candidate_payload = {"review_state": "CONFIRMED", "provenance": "USER_CONFIRMED", **payload}
        cursor = self.connection.execute("INSERT INTO candidate_records(record_type, payload, created_at) VALUES (?, ?, ?)", (record_type, json.dumps(candidate_payload, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))
        self.log("candidate_record_added", record_type)
        self.revoke_autopilot("Candidate information changed")
        self.connection.commit()
        return int(cursor.lastrowid)

    def add_extracted_candidate_record(self, record_type: str, payload: dict[str, object]) -> int | None:
        """Store a CV-derived structured history row for explicit candidate review."""
        if record_type not in {"experience", "education"}:
            raise ValueError("Only employment and education CV records are supported")
        title = str(payload.get("title", "")).strip()
        if not title or len(title) > 200:
            raise ValueError("CV record needs a title of at most 200 characters")
        for key in ("start_date", "end_date"):
            value = str(payload.get(key, "")).strip()
            if value and not re.fullmatch(r"\d{4}(?:-(?:0[1-9]|1[0-2]))?", value):
                raise ValueError("CV record dates must use YYYY or YYYY-MM")

        def normalized(value: object) -> str:
            return " ".join(re.findall(r"[^\W_]+", str(value or "").casefold(), flags=re.UNICODE))

        identity = (normalized(title), normalized(payload.get("organization", "")))
        existing: list[tuple[int, dict[str, object]]] = []
        for row in self.connection.execute("SELECT id,payload FROM candidate_records WHERE record_type=? ORDER BY id DESC", (record_type,)):
            existing_payload = json.loads(row["payload"])
            if existing_payload.get("review_state", "CONFIRMED") != "CONFIRMED":
                continue
            existing_identity = (
                normalized(existing_payload.get("title", "")),
                normalized(existing_payload.get("organization", "")),
            )
            if identity == existing_identity:
                existing.append((int(row["id"]), existing_payload))

        compared = ("title", "organization", "location", "start_date", "end_date", "is_current", "details")
        for existing_id, existing_payload in existing:
            if all(str(existing_payload.get(key, "")) == str(payload.get(key, "")) for key in compared):
                return None

        candidate_payload = {
            "review_state": "CONFLICT" if existing else "DRAFT",
            "provenance": "CV_EXTRACTED",
            **payload,
        }
        if existing:
            candidate_payload["conflict_with_record_id"] = existing[0][0]
        cursor = self.connection.execute(
            "INSERT INTO candidate_records(record_type,payload,created_at) VALUES (?,?,?)",
            (record_type, json.dumps(candidate_payload, ensure_ascii=False, sort_keys=True), datetime.now(timezone.utc).isoformat()),
        )
        self.log("cv_record_needs_review", f"{record_type}: {title}")
        self.revoke_autopilot("CV history was added or changed")
        self.connection.commit()
        return int(cursor.lastrowid)

    def candidate_records(self, record_type: str) -> list[dict[str, str]]:
        rows = self.connection.execute("SELECT payload FROM candidate_records WHERE record_type=? ORDER BY id DESC", (record_type,))
        records = [json.loads(row[0]) for row in rows]
        return [record for record in records if record.get("review_state", "CONFIRMED") == "CONFIRMED"]

    def candidate_record_rows(self, record_type: str) -> list[dict[str, object]]:
        rows = self.connection.execute("SELECT id, payload FROM candidate_records WHERE record_type=? ORDER BY id DESC", (record_type,))
        return [{"id": int(row[0]), "review_state": "CONFIRMED", **json.loads(row[1])} for row in rows]

    def candidate_record(self, record_id: int) -> dict[str, object] | None:
        row = self.connection.execute("SELECT id,record_type,payload FROM candidate_records WHERE id=?", (record_id,)).fetchone()
        return {"id": int(row["id"]), "record_type": str(row["record_type"]), **json.loads(row["payload"])} if row else None

    def resolve_candidate_record(self, record_id: int, *, decision: str) -> None:
        allowed = {"confirm", "reject", "keep_existing", "use_new", "add_separate"}
        row = self.connection.execute("SELECT record_type,payload FROM candidate_records WHERE id=?", (record_id,)).fetchone()
        if not row or decision not in allowed:
            raise ValueError("Candidate record or review decision is invalid")
        payload = json.loads(row["payload"])
        state = str(payload.get("review_state", "CONFIRMED"))
        conflict_id = payload.get("conflict_with_record_id")
        if state == "CONFLICT" and decision in {"keep_existing", "use_new", "add_separate"}:
            if decision == "keep_existing":
                payload["review_state"] = "REJECTED"
            elif decision == "use_new":
                existing = self.connection.execute("SELECT record_type,payload FROM candidate_records WHERE id=?", (int(conflict_id or 0),)).fetchone()
                if not existing or existing["record_type"] != row["record_type"]:
                    raise ValueError("The prior record for this conflict no longer exists")
                self.connection.execute(
                    "UPDATE candidate_records SET payload=? WHERE id=?",
                    (json.dumps({**json.loads(existing["payload"]), "review_state": "SUPERSEDED"}, ensure_ascii=False, sort_keys=True), int(conflict_id)),
                )
                self.connection.execute("UPDATE facts SET confirmed=0,rejected=1 WHERE source_id=?", (f"candidate_record:{int(conflict_id)}",))
                payload["review_state"] = "CONFIRMED"
                payload["provenance"] = "USER_CONFIRMED"
            else:
                payload["review_state"] = "CONFIRMED"
                payload["provenance"] = "USER_CONFIRMED"
            payload.pop("conflict_with_record_id", None)
        elif state == "DRAFT" and decision in {"confirm", "reject"}:
            payload["review_state"] = "CONFIRMED" if decision == "confirm" else "REJECTED"
            if decision == "confirm":
                payload["provenance"] = "USER_CONFIRMED"
        else:
            raise ValueError("This record is not awaiting the selected review decision")

        with self.connection:
            self.connection.execute("UPDATE candidate_records SET payload=? WHERE id=?", (json.dumps(payload, ensure_ascii=False, sort_keys=True), record_id))
            if payload.get("review_state") == "CONFIRMED":
                self.connection.execute(
                    "INSERT INTO facts(type,value,provenance,source_id,confidence,confirmed,rejected) VALUES (?,?, 'USER_CONFIRMED', ?,1,1,0)",
                    (str(row["record_type"]), str(payload["title"]), f"candidate_record:{record_id}"),
                )
            self.log("cv_record_reviewed", f"{record_id}: {decision}")
            self.revoke_autopilot("Candidate reviewed CV history")

    def update_candidate_record(
        self,
        record_id: int,
        *,
        title: str,
        details: str,
        organization: str | None = None,
        location: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        is_current: bool | None = None,
    ) -> None:
        row = self.connection.execute("SELECT record_type, payload FROM candidate_records WHERE id=?", (record_id,)).fetchone()
        if not row or not title.strip():
            raise ValueError("Candidate record was not found or the title is empty")
        payload = json.loads(row[1])
        old_title = str(payload.get("title", ""))
        payload.update({"title": title.strip(), "details": details.strip()})
        for key, value in (("organization", organization), ("location", location), ("start_date", start_date), ("end_date", end_date), ("is_current", is_current)):
            if value is not None:
                payload[key] = value.strip() if isinstance(value, str) else bool(value)
        for key in ("start_date", "end_date"):
            value = str(payload.get(key, "")).strip()
            if value and not re.fullmatch(r"\d{4}(?:-(?:0[1-9]|1[0-2]))?", value):
                raise ValueError("Candidate history dates must use YYYY or YYYY-MM")
        if payload.get("is_current") and payload.get("end_date"):
            raise ValueError("Current roles cannot also have an end date")
        self.connection.execute("UPDATE candidate_records SET payload=? WHERE id=?", (json.dumps(payload, ensure_ascii=False), record_id))
        source = f"candidate_record:{record_id}"
        fact = self.connection.execute("SELECT id FROM facts WHERE type=? AND value=? AND provenance='USER_CONFIRMED' AND source_id=? ORDER BY id DESC LIMIT 1", (row[0], old_title, source)).fetchone()
        if fact:
            self.connection.execute("UPDATE facts SET value=? WHERE id=?", (title.strip(), int(fact[0])))
        self.log("candidate_record_updated", str(record_id))
        self.revoke_autopilot("Candidate information changed")
        self.connection.commit()

    def delete_candidate_record(self, record_id: int) -> None:
        row = self.connection.execute("SELECT record_type, payload FROM candidate_records WHERE id=?", (record_id,)).fetchone()
        if row:
            value = str(json.loads(row[1]).get("title", ""))
            self.connection.execute("DELETE FROM facts WHERE type=? AND provenance='USER_CONFIRMED' AND source_id=?", (row[0], f"candidate_record:{record_id}"))
            self.connection.execute("DELETE FROM candidate_records WHERE id=?", (record_id,))
        self.log("candidate_record_deleted", str(record_id))
        self.revoke_autopilot("Candidate information changed")
        self.connection.commit()

    def add_cv_template(self, *, name: str, language: str, role_family: str, notes: str = "") -> int:
        if language not in {"fi", "en"}:
            raise ValueError("Unsupported template language")
        cursor = self.connection.execute(
            "INSERT INTO cv_templates(name, language, role_family, notes, created_at) VALUES (?, ?, ?, ?, ?)",
            (name.strip(), language, role_family, notes.strip(), datetime.now(timezone.utc).isoformat()),
        )
        self.log("cv_template_registered", name)
        self.connection.commit()
        return int(cursor.lastrowid)

    def cv_templates(self) -> list[dict[str, object]]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM cv_templates ORDER BY id DESC")]

    def add_career_profile(self, name: str, notes: str = "") -> int:
        cursor = self.connection.execute("INSERT INTO career_profiles(name, notes) VALUES (?, ?)", (name.strip(), notes.strip()))
        self.log("career_profile_added", name)
        self.connection.commit()
        return int(cursor.lastrowid)

    def career_profile(self, profile_id: int) -> dict[str, object] | None:
        row = self.connection.execute("SELECT * FROM career_profiles WHERE id=?", (profile_id,)).fetchone()
        return dict(row) if row else None

    def set_career_profile_enabled(self, profile_id: int, enabled: bool) -> None:
        self.connection.execute("UPDATE career_profiles SET enabled=? WHERE id=?", (int(enabled), profile_id))
        self.log("career_profile_enabled", str(profile_id))
        self.connection.commit()

    def delete_career_profile(self, profile_id: int) -> None:
        self.connection.execute("DELETE FROM career_profiles WHERE id=?", (profile_id,))
        self.log("career_profile_deleted", str(profile_id))
        self.connection.commit()

    def update_career_profile(self, profile_id: int, *, name: str, notes: str) -> None:
        if not name.strip():
            raise ValueError("Career profile name is required")
        self.connection.execute("UPDATE career_profiles SET name=?, notes=? WHERE id=?", (name.strip(), notes.strip(), profile_id))
        self.log("career_profile_updated", str(profile_id))
        self.connection.commit()

    def add_target_occupation(self, title_en: str, title_fi: str) -> int:
        """Save a user-approved occupation target; recommendations never do this implicitly."""
        english = title_en.strip()
        finnish = title_fi.strip()
        if not english or not finnish:
            raise ValueError("Both occupation titles are required")
        existing = self.connection.execute(
            "SELECT id FROM target_occupations WHERE title_en=? AND title_fi=?",
            (english, finnish),
        ).fetchone()
        if existing:
            self.connection.execute("UPDATE target_occupations SET enabled=1 WHERE id=?", (existing[0],))
            target_id = int(existing[0])
        else:
            cursor = self.connection.execute(
                "INSERT INTO target_occupations(title_en, title_fi, created_at) VALUES (?, ?, ?)",
                (english, finnish, datetime.now(timezone.utc).isoformat()),
            )
            target_id = int(cursor.lastrowid)
        self.log("target_occupation_approved", english)
        self.connection.commit()
        return target_id

    def target_occupations(self) -> list[dict[str, object]]:
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM target_occupations ORDER BY enabled DESC, title_en"
            )
        ]

    def replace_enabled_target_occupations(self, choices: list[tuple[str, str]]) -> None:
        """Atomically replace the user's active occupation choices."""
        if len(choices) > 50 or any(
            not isinstance(title_en, str)
            or not isinstance(title_fi, str)
            or not title_en.strip()
            or not title_fi.strip()
            or len(title_en) > 300
            or len(title_fi) > 300
            for title_en, title_fi in choices
        ):
            raise ValueError("Invalid occupation selection")
        normalized = [(title_en.strip(), title_fi.strip()) for title_en, title_fi in choices]
        if len(normalized) != len(set(normalized)):
            raise ValueError("Duplicate occupation selection")
        now = datetime.now(timezone.utc).isoformat()
        with self.connection:
            self.connection.execute("UPDATE target_occupations SET enabled=0 WHERE enabled<>0")
            for title_en, title_fi in normalized:
                self.connection.execute(
                    "INSERT INTO target_occupations(title_en,title_fi,enabled,created_at) VALUES (?,?,1,?) "
                    "ON CONFLICT(title_en,title_fi) DO UPDATE SET enabled=1",
                    (title_en, title_fi, now),
                )
            self.log("target_occupations_replaced", f"{len(normalized)} active roles")

    def replace_esco_taxonomy(
        self,
        *,
        occupations: list[dict[str, str]],
        skills: list[dict[str, str]],
        relationships: list[tuple[str, str, str]],
        metadata: dict[str, str],
    ) -> None:
        """Replace the derived public taxonomy index in one transaction."""
        required_metadata = {
            "version", "languages", "source_url", "license_statement", "attribution",
            "modified", "quality_notice", "source_sha256", "imported_at",
        }
        if set(metadata) != required_metadata or len(metadata["source_sha256"]) != 64:
            raise ValueError("ESCO import metadata is incomplete")
        occupation_uris = {item["uri"] for item in occupations}
        skill_uris = {item["uri"] for item in skills}
        if any(
            occupation_uri not in occupation_uris or skill_uri not in skill_uris or importance not in {"ESSENTIAL", "OPTIONAL", "UNSPECIFIED"}
            for occupation_uri, skill_uri, importance in relationships
        ):
            raise ValueError("ESCO relationship refers to an unknown concept")
        with self.connection:
            self.connection.execute("DELETE FROM esco_occupation_skills")
            self.connection.execute("DELETE FROM esco_skills")
            self.connection.execute("DELETE FROM esco_occupations")
            self.connection.execute("DELETE FROM esco_taxonomy_metadata")
            self.connection.executemany(
                "INSERT INTO esco_occupations(concept_uri, language, label, description, isco_code) VALUES (?, ?, ?, ?, ?)",
                [(item["uri"], item["language"], item["label"], item["description"], item["isco_code"]) for item in occupations],
            )
            self.connection.executemany(
                "INSERT INTO esco_skills(concept_uri, language, label, description) VALUES (?, ?, ?, ?)",
                [(item["uri"], item["language"], item["label"], item["description"]) for item in skills],
            )
            self.connection.executemany(
                "INSERT INTO esco_occupation_skills(occupation_uri, skill_uri, importance) VALUES (?, ?, ?)",
                relationships,
            )
            self.connection.execute(
                "INSERT INTO esco_taxonomy_metadata(id, version, languages, source_url, license_statement, attribution, modified, quality_notice, source_sha256, imported_at) "
                "VALUES (1, :version, :languages, :source_url, :license_statement, :attribution, :modified, :quality_notice, :source_sha256, :imported_at)",
                metadata,
            )
            self.log("esco_taxonomy_imported", f"ESCO {metadata['version']} ({metadata['languages']})")

    def esco_taxonomy_metadata(self) -> dict[str, str] | None:
        row = self.connection.execute(
            "SELECT version, languages, source_url, license_statement, attribution, modified, quality_notice, source_sha256, imported_at "
            "FROM esco_taxonomy_metadata WHERE id=1"
        ).fetchone()
        return dict(row) if row else None

    def esco_occupations(self) -> list[TaxonomyOccupation]:
        rows = self.connection.execute(
            "SELECT o.concept_uri, o.language, o.label, o.description, o.isco_code, "
            "r.skill_uri, r.importance, s.language AS skill_language, s.label AS skill_label "
            "FROM esco_occupations o "
            "LEFT JOIN esco_occupation_skills r ON r.occupation_uri=o.concept_uri "
            "LEFT JOIN esco_skills s ON s.concept_uri=r.skill_uri "
            "ORDER BY o.concept_uri, o.language, r.skill_uri, s.language"
        ).fetchall()
        grouped: dict[str, dict[str, object]] = {}
        for row in rows:
            uri = str(row["concept_uri"])
            item = grouped.setdefault(uri, {"labels": {}, "descriptions": {}, "isco_code": "", "skills": {}})
            labels = item["labels"]
            descriptions = item["descriptions"]
            skill_items = item["skills"]
            if not isinstance(labels, dict) or not isinstance(descriptions, dict) or not isinstance(skill_items, dict):
                continue
            labels[str(row["language"])] = str(row["label"])
            descriptions[str(row["language"])] = str(row["description"])
            if row["isco_code"]:
                item["isco_code"] = str(row["isco_code"])
            if row["skill_uri"]:
                skill_uri = str(row["skill_uri"])
                skill = skill_items.setdefault(skill_uri, {"importance": str(row["importance"]), "labels": {}})
                skill_labels = skill["labels"]
                if row["skill_language"] and isinstance(skill_labels, dict):
                    skill_labels[str(row["skill_language"])] = str(row["skill_label"])
        return [
            TaxonomyOccupation(
                uri=uri,
                labels=dict(item["labels"]),
                descriptions=dict(item["descriptions"]),
                isco_code=str(item["isco_code"]),
                skills=tuple(
                    TaxonomyOccupationSkill(uri=skill_uri, importance=str(skill["importance"]), labels=dict(skill["labels"]))
                    for skill_uri, skill in sorted(item["skills"].items())
                ),
            )
            for uri, item in sorted(grouped.items())
        ]

    def target_occupation(self, target_id: int) -> dict[str, object] | None:
        row = self.connection.execute(
            "SELECT * FROM target_occupations WHERE id=?", (target_id,)
        ).fetchone()
        return dict(row) if row else None

    def set_target_occupation_enabled(self, target_id: int, enabled: bool) -> None:
        self.connection.execute(
            "UPDATE target_occupations SET enabled=? WHERE id=?", (int(enabled), target_id)
        )
        self.log("target_occupation_enabled", str(target_id))
        self.connection.commit()

    def delete_target_occupation(self, target_id: int) -> None:
        self.connection.execute("DELETE FROM target_occupations WHERE id=?", (target_id,))
        self.log("target_occupation_deleted", str(target_id))
        self.connection.commit()

    def add_skill(self, value: str) -> None:
        clean = value.strip()
        if not clean:
            raise ValueError("A skill is required")
        self.connection.execute("INSERT INTO facts(type, value, provenance, source_id, confidence, confirmed) VALUES ('skill', ?, 'USER_CONFIRMED', 'profile', 1, 1)", (clean,))
        self.log("skill_added", clean)
        self.connection.commit()

    def add_confirmed_fact(self, *, fact_type: str, value: str, source_id: str = "profile") -> int:
        """Store a directly entered candidate fact as user-confirmed evidence."""
        clean = value.strip()
        if not clean:
            raise ValueError("A fact value is required")
        cursor = self.connection.execute(
            "INSERT INTO facts(type, value, provenance, source_id, confidence, confirmed) VALUES (?, ?, 'USER_CONFIRMED', ?, 1, 1)",
            (fact_type, clean, source_id),
        )
        self.log("confirmed_fact_added", f"{fact_type}: {clean}")
        self.connection.commit()
        return int(cursor.lastrowid)

    def add_extracted_fact(self, *, fact_type: str, value: str, source_id: str, confidence: float, evidence: dict[str, object] | None = None) -> int:
        cursor = self.connection.execute(
            "INSERT INTO facts(type, value, provenance, source_id, confidence, confirmed, evidence_json) "
            "VALUES (?, ?, 'CV_EXTRACTED', ?, ?, 0, ?)",
            (fact_type, value, source_id, confidence, json.dumps(evidence or {}, ensure_ascii=False, sort_keys=True)),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def confirm_fact(self, fact_id: int) -> None:
        self.connection.execute("UPDATE facts SET confirmed=1, rejected=0 WHERE id=?", (fact_id,))
        self.log("fact_confirmed", str(fact_id))
        self.connection.commit()

    def edit_fact(self, fact_id: int, value: str) -> None:
        self.connection.execute("UPDATE facts SET value=?, provenance='USER_CONFIRMED', confirmed=1 WHERE id=?", (value.strip(), fact_id))
        self.log("fact_edited", str(fact_id))
        self.connection.commit()

    def reject_fact(self, fact_id: int) -> None:
        self.connection.execute("UPDATE facts SET rejected=1, confirmed=0 WHERE id=?", (fact_id,))
        self.log("fact_rejected", str(fact_id))
        self.connection.commit()

    def delete_fact(self, fact_id: int) -> None:
        self.connection.execute("DELETE FROM facts WHERE id=?", (fact_id,))
        self.log("fact_deleted", str(fact_id))
        self.connection.commit()

    def add_job(self, job: object, verification: str) -> int | None:
        """Persist a normalized job once; return None when its fingerprint already exists."""
        if verification not in {"UNVERIFIED", "PARTIALLY_VERIFIED", "EXPIRED"}:
            # Only an audit-backed verification method may elevate a job.
            verification = "PARTIALLY_VERIFIED"
        try:
            cursor = self.connection.execute(
                "INSERT INTO jobs(title, company, location, language, description, application_url, fingerprint, verification_state, deadline, source_id, source_name, source_url) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job.title,
                    job.company,
                    job.location,
                    job.language,
                    job.description,
                    job.application_url,
                    job.fingerprint,
                    verification,
                    getattr(job, "deadline", None).isoformat() if getattr(job, "deadline", None) else None,
                    getattr(job, "source_id", None),
                    getattr(job, "source_name", ""),
                    getattr(job, "source_url", ""),
                ),
            )
        except sqlite3.IntegrityError:
            return None
        self.log("job_imported", job.title)
        self.connection.commit()
        return int(cursor.lastrowid)

    def mark_job_user_reviewed(self, job_id: int, *, reviewed_current: bool) -> None:
        """Record the candidate's explicit check of the live employer posting."""
        from sampoagent.applications.urls import is_safe_public_https_url
        from sampoagent.jobs.service import job_snapshot_hash

        job = self.job(job_id)
        if (
            not reviewed_current or not job or job.get("verification_state") == "EXPIRED"
            or not str(job.get("title", "")).strip() or not str(job.get("company", "")).strip()
            or not is_safe_public_https_url(str(job.get("application_url", "")))
        ):
            raise ValueError("Review the active employer listing and confirm its public HTTPS application destination")
        deadline = str(job.get("deadline") or "").strip()
        if deadline:
            try:
                if datetime.fromisoformat(deadline.replace("Z", "+00:00")).date() < datetime.now(timezone.utc).date():
                    raise ValueError("This job listing has expired")
            except ValueError as exc:
                raise ValueError("This job listing has an invalid or expired deadline") from exc
        verified_at = datetime.now(timezone.utc).isoformat()
        evidence_url = str(job.get("source_url") or job.get("application_url") or "")
        with self.connection:
            self.connection.execute("UPDATE jobs SET verification_state='VERIFIED',verified_at=? WHERE id=?", (verified_at, job_id))
            self.connection.execute(
                "INSERT INTO job_verifications(job_id,method,source_id,source_url,evidence_summary,verified_at,snapshot_hash) VALUES (?,?,?,?,?,?,?)",
                (job_id, "user_reviewed_listing", job.get("source_id"), evidence_url, "Candidate confirmed the live employer, role, deadline and application destination", verified_at, job_snapshot_hash(job)),
            )
            self.log("job_verified", f"{job_id}: user-reviewed listing")

    def mark_official_api_job_verified(self, job_id: int, *, source_id: int, source_url: str) -> None:
        """Mark a listing verified only from the configured official Job Market Finland API."""
        from sampoagent.applications.urls import is_safe_public_https_url
        from sampoagent.jobs.service import job_snapshot_hash

        source = self.source(source_id)
        job = self.job(job_id)
        parsed_source = urlsplit(source_url)
        if (
            not source or str(source.get("capability", "")).casefold() != "job market finland api"
            or parsed_source.scheme != "https" or parsed_source.hostname not in {"tyomarkkinatori.fi", "www.tyomarkkinatori.fi"}
            or not job or int(job.get("source_id") or 0) != source_id
            or str(job.get("source_url", "")) != source_url
            or not is_safe_public_https_url(str(job.get("application_url", "")))
            or job.get("verification_state") == "EXPIRED"
        ):
            raise ValueError("Official listing verification requirements were not met")
        verified_at = datetime.now(timezone.utc).isoformat()
        with self.connection:
            self.connection.execute("UPDATE jobs SET verification_state='VERIFIED',verified_at=? WHERE id=?", (verified_at, job_id))
            self.connection.execute(
                "INSERT INTO job_verifications(job_id,method,source_id,source_url,evidence_summary,verified_at,snapshot_hash) VALUES (?,?,?,?,?,?,?)",
                (job_id, "official_job_market_finland_api", source_id, source_url, "Returned by the official API with onlyStatus=PUBLISHED", verified_at, job_snapshot_hash(job)),
            )
            self.log("job_verified", f"{job_id}: official Job Market Finland API")

    def job_verification(self, job_id: int) -> dict[str, object] | None:
        row = self.connection.execute(
            "SELECT method,source_id,source_url,evidence_summary,verified_at,snapshot_hash FROM job_verifications WHERE job_id=? ORDER BY id DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        return dict(row) if row else None

    def create_discovery_run(self, *, query_count: int) -> int:
        if query_count < 0:
            raise ValueError("Query count cannot be negative")
        cursor = self.connection.execute(
            "INSERT INTO discovery_runs(started_at, status, query_count) VALUES (?, 'running', ?)",
            (datetime.now(timezone.utc).isoformat(), query_count),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def record_discovery_source_result(
        self,
        run_id: int,
        *,
        source_id: int | None,
        source_name: str,
        source_url: str,
        capability: str,
        status: str,
        jobs_found: int = 0,
        imported_count: int = 0,
        duplicates_count: int = 0,
        message: str = "",
    ) -> int:
        counts = (jobs_found, imported_count, duplicates_count)
        if any(count < 0 for count in counts):
            raise ValueError("Discovery result counts cannot be negative")
        now = datetime.now(timezone.utc)
        cursor = self.connection.execute(
            "INSERT INTO discovery_source_results(run_id, source_id, source_name, source_url, capability, status, jobs_found, imported_count, duplicates_count, message, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                source_id,
                source_name,
                source_url,
                capability,
                status,
                jobs_found,
                imported_count,
                duplicates_count,
                message,
                now.isoformat(),
            ),
        )
        if source_id is not None and status not in {"cooldown", "terms_review_required", "browser_only", "skipped_limit", "not_configured"}:
            old = self.discovery_source_state(source_id)
            failures = int(old.get("consecutive_failures", 0)) if old else 0
            next_attempt_at = old.get("next_attempt_at") if old else None
            last_success_at = old.get("last_success_at") if old else None
            if status == "failed":
                failures += 1
                delay_seconds = min(6 * 60 * 60, 60 * (2 ** min(failures - 1, 10)))
                next_attempt_at = (now + timedelta(seconds=delay_seconds)).isoformat()
            elif status in {"imported", "duplicates", "no_results"}:
                failures = 0
                # Avoid hammering a source when discovery is manually repeated.
                next_attempt_at = (now + timedelta(seconds=60)).isoformat()
                last_success_at = now.isoformat()
            self.connection.execute(
                "INSERT INTO discovery_source_state(source_id,consecutive_failures,last_status,last_message,last_checked_at,last_success_at,next_attempt_at) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET consecutive_failures=excluded.consecutive_failures, "
                "last_status=excluded.last_status,last_message=excluded.last_message,last_checked_at=excluded.last_checked_at, "
                "last_success_at=excluded.last_success_at,next_attempt_at=excluded.next_attempt_at",
                (source_id, failures, status, message[:300], now.isoformat(), last_success_at, next_attempt_at),
            )
        self.connection.commit()
        return int(cursor.lastrowid)

    def complete_discovery_run(
        self,
        run_id: int,
        *,
        status: str,
        jobs_found: int,
        imported_count: int,
        duplicates_count: int,
        summary: str,
    ) -> None:
        if status not in {"completed", "partial", "failed", "no_search_terms"}:
            raise ValueError("Unsupported discovery run status")
        if min(jobs_found, imported_count, duplicates_count) < 0:
            raise ValueError("Discovery run counts cannot be negative")
        self.connection.execute(
            "UPDATE discovery_runs SET finished_at=?, status=?, jobs_found=?, imported_count=?, duplicates_count=?, summary=? WHERE id=?",
            (
                datetime.now(timezone.utc).isoformat(),
                status,
                jobs_found,
                imported_count,
                duplicates_count,
                summary,
                run_id,
            ),
        )
        self.connection.commit()

    def latest_discovery_run(self) -> dict[str, object] | None:
        row = self.connection.execute("SELECT * FROM discovery_runs ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def discovery_source_results(self, run_id: int) -> list[dict[str, object]]:
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM discovery_source_results WHERE run_id=? ORDER BY id",
                (run_id,),
            )
        ]

    def discovery_source_state(self, source_id: int) -> dict[str, object] | None:
        row = self.connection.execute("SELECT * FROM discovery_source_state WHERE source_id=?", (source_id,)).fetchone()
        return dict(row) if row else None

    def discovery_source_states(self) -> dict[int, dict[str, object]]:
        return {int(row["source_id"]): dict(row) for row in self.connection.execute("SELECT * FROM discovery_source_state")}

    def source_is_in_backoff(self, source_id: int) -> bool:
        state = self.discovery_source_state(source_id)
        if not state or not state.get("next_attempt_at"):
            return False
        try:
            retry_at = datetime.fromisoformat(str(state["next_attempt_at"]))
        except ValueError:
            return False
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return retry_at > datetime.now(timezone.utc)

    def mailbox_connection(self) -> dict[str, object] | None:
        row = self.connection.execute("SELECT id, provider, connected_at FROM mailbox_connection WHERE id=1").fetchone()
        return dict(row) if row else None

    def mailbox_ciphertext(self) -> str | None:
        row = self.connection.execute("SELECT token_ciphertext FROM mailbox_connection WHERE id=1").fetchone()
        return str(row[0]) if row else None

    def mail_send_accounts(self) -> list[dict[str, object]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT id,provider,sender_email,address_status,granted_scopes,connected_at,token_expires_at "
            "FROM mail_send_accounts ORDER BY id DESC"
        )]

    def mail_send_connection(self, account_id: int | None = None) -> dict[str, object] | None:
        if account_id is None:
            try:
                account_id = int(self.setting("mail_send_default_account_id") or "")
            except (ValueError, TypeError):
                return None
        row = self.connection.execute(
            "SELECT id,provider,provider_subject,sender_email,address_status,granted_scopes,connected_at,token_expires_at "
            "FROM mail_send_accounts WHERE id=?", (account_id,),
        ).fetchone()
        return dict(row) if row else None

    def mail_send_ciphertext(self, account_id: int | None = None) -> str | None:
        connection = self.mail_send_connection(account_id)
        if not connection:
            return None
        row = self.connection.execute("SELECT token_ciphertext FROM mail_send_accounts WHERE id=?", (connection["id"],)).fetchone()
        return str(row[0]) if row else None

    def legacy_mail_send_connection_needs_reconnect(self) -> bool:
        return bool(self.connection.execute("SELECT 1 FROM mail_send_connection WHERE id=1").fetchone())

    def save_mail_send_connection(
        self,
        provider: str,
        token_ciphertext: str,
        granted_scopes: str,
        *,
        subject: str,
        sender_email: str,
        address_status: str,
        token_expires_at: str = "",
    ) -> int:
        if provider not in {"gmail", "microsoft"} or not token_ciphertext.strip():
            raise ValueError("A supported provider and encrypted send token are required")
        if not subject.strip() or len(subject) > 512 or not sender_email.strip() or len(sender_email) > 320:
            raise ValueError("A stable provider subject and address are required")
        parsed_email = parseaddr(sender_email)
        if (
            parsed_email[1] != sender_email or sender_email.count("@") != 1
            or any(char.isspace() or char in "\r\n\x00<>;,\"'" for char in sender_email)
            or "." not in sender_email.rsplit("@", 1)[-1]
        ):
            raise ValueError("A valid provider-reported account address is required")
        if address_status != ("verified" if provider == "gmail" else "provider_reported"):
            raise ValueError("The provider identity status does not match the selected provider")
        scopes = set(granted_scopes.split())
        required_scope = "https://www.googleapis.com/auth/gmail.send" if provider == "gmail" else "Mail.Send"
        if required_scope not in scopes or not {"openid", "email"}.issubset(scopes):
            raise ValueError("The separate send permission and OIDC identity scopes are required")
        now = datetime.now(timezone.utc).isoformat()
        existing = self.connection.execute(
            "SELECT id FROM mail_send_accounts WHERE provider=? AND provider_subject=?", (provider, subject.strip())
        ).fetchone()
        if existing:
            account_id = int(existing["id"])
            self.connection.execute(
                "UPDATE mail_send_accounts SET sender_email=?,address_status=?,token_ciphertext=?,granted_scopes=?,connected_at=?,token_expires_at=? WHERE id=?",
                (sender_email.strip(), address_status, token_ciphertext, " ".join(sorted(scopes)), now, token_expires_at, account_id),
            )
        else:
            cursor = self.connection.execute(
                "INSERT INTO mail_send_accounts(provider,provider_subject,sender_email,address_status,token_ciphertext,granted_scopes,connected_at,token_expires_at) VALUES (?,?,?,?,?,?,?,?)",
                (provider, subject.strip(), sender_email.strip(), address_status, token_ciphertext, " ".join(sorted(scopes)), now, token_expires_at),
            )
            account_id = int(cursor.lastrowid)
        if not self.setting("mail_send_default_account_id"):
            self.connection.execute("UPDATE settings SET value=? WHERE key='mail_send_default_account_id'", (str(account_id),))
        else:
            try:
                selected_id = int(self.setting("mail_send_default_account_id") or "")
            except (TypeError, ValueError):
                selected_id = -1
            if selected_id == account_id:
                self._revoke_email_send_autopilot()
        self.log("mail_send_connected", f"{provider}:{account_id}")
        self.connection.commit()
        return account_id

    def set_default_mail_send_account(self, account_id: int) -> None:
        if not self.connection.execute("SELECT 1 FROM mail_send_accounts WHERE id=?", (account_id,)).fetchone():
            raise ValueError("Choose a connected send account")
        with self.connection:
            self.connection.execute("UPDATE settings SET value=? WHERE key='mail_send_default_account_id'", (str(account_id),))
            self._revoke_email_send_autopilot()

    def remove_mail_send_account(self, account_id: int) -> bool:
        connection = self.mail_send_connection(account_id)
        if not connection:
            return False
        with self.connection:
            self.connection.execute("DELETE FROM mail_send_accounts WHERE id=?", (account_id,))
            if str(self.setting("mail_send_default_account_id") or "") == str(account_id):
                self.connection.execute("UPDATE settings SET value='' WHERE key='mail_send_default_account_id'")
                self._revoke_email_send_autopilot()
            self.log("mail_send_account_disconnected", f"{connection['provider']}:{account_id}")
        return True

    def refresh_mail_send_connection(self, token_ciphertext: str, account_id: int | None = None, token_expires_at: str = "") -> None:
        """Replace an encrypted access token without changing account identity or consent."""
        connection = self.mail_send_connection(account_id)
        if not token_ciphertext.strip() or not connection:
            raise ValueError("An encrypted token and connected send account are required")
        with self.connection:
            self.connection.execute(
                "UPDATE mail_send_accounts SET token_ciphertext=?,token_expires_at=CASE WHEN ?='' THEN token_expires_at ELSE ? END WHERE id=?",
                (token_ciphertext, token_expires_at, token_expires_at, connection["id"]),
            )

    def create_email_outbox(
        self,
        *,
        application_id: int,
        provider: str,
        sender_account_id: int,
        package_hash: str,
        idempotency_key: str,
        payload_ciphertext: str,
    ) -> dict[str, object]:
        account = self.mail_send_connection(sender_account_id)
        if provider not in {"gmail", "microsoft"} or not account or account["provider"] != provider or not all((package_hash, idempotency_key, payload_ciphertext)):
            raise ValueError("A valid identified sender account and encrypted package are required")
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            application = self.connection.execute(
                "SELECT status,queue_state FROM applications WHERE id=?", (application_id,)
            ).fetchone()
            if not application or application["status"] != "QUEUED" or application["queue_state"] not in {"READY", "EMAIL_READY"}:
                self.connection.rollback()
                raise ValueError("Only a ready queued application may have an email draft")
            existing = self.connection.execute(
                "SELECT id,state FROM email_outbox WHERE application_id=?", (application_id,)
            ).fetchone()
            if existing:
                if existing["state"] != "CANCELLED":
                    self.connection.rollback()
                    raise ValueError("An email draft already exists for this application")
                self.connection.execute(
                    "UPDATE email_outbox SET provider=?,sender_account_id=?,idempotency_key=?,package_hash=?,payload_ciphertext=?,state='READY',created_at=?,updated_at=?,started_at=NULL,finished_at=NULL,provider_reference='',message='' WHERE id=?",
                    (provider, sender_account_id, idempotency_key, package_hash, payload_ciphertext, now, now, int(existing["id"])),
                )
            else:
                self.connection.execute(
                    "INSERT INTO email_outbox(application_id,provider,sender_account_id,idempotency_key,package_hash,payload_ciphertext,state,created_at,updated_at) VALUES (?,?,?,?,?,?,'READY',?,?)",
                    (application_id, provider, sender_account_id, idempotency_key, package_hash, payload_ciphertext, now, now),
                )
            self.connection.execute(
                "UPDATE applications SET queue_state='EMAIL_READY',updated_at=? WHERE id=? AND status='QUEUED' AND queue_state IN ('READY','EMAIL_READY')",
                (now, application_id),
            )
            self.log("application_email_draft_created", f"{application_id}:{package_hash[:12]}")
            self.connection.commit()
        except Exception:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise
        result = self.email_outbox_for_application(application_id)
        if not result:
            raise RuntimeError("Email draft could not be read after creation")
        return result

    def email_outbox_for_application(self, application_id: int) -> dict[str, object] | None:
        row = self.connection.execute(
            "SELECT id,application_id,provider,sender_account_id,idempotency_key,package_hash,state,created_at,updated_at,started_at,finished_at,provider_reference,message FROM email_outbox WHERE application_id=?",
            (application_id,),
        ).fetchone()
        return dict(row) if row else None

    def email_outbox_payload_ciphertext(self, application_id: int) -> str | None:
        row = self.connection.execute(
            "SELECT payload_ciphertext FROM email_outbox WHERE application_id=?", (application_id,)
        ).fetchone()
        return str(row[0]) if row else None

    def email_outbox_items(self) -> list[dict[str, object]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT id,application_id,provider,sender_account_id,idempotency_key,package_hash,state,created_at,updated_at,started_at,finished_at,provider_reference,message FROM email_outbox ORDER BY id DESC"
        )]

    def ready_email_outbox_items(self) -> list[dict[str, object]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT e.id,e.application_id,e.provider,e.sender_account_id,e.package_hash,e.state,e.created_at FROM email_outbox e "
            "JOIN applications a ON a.id=e.application_id WHERE e.state='READY' AND a.status='QUEUED' "
            "AND a.queue_state='EMAIL_READY' ORDER BY e.created_at,e.id"
        )]

    def cancel_email_outbox(self, application_id: int) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE email_outbox SET state='CANCELLED',updated_at=?,finished_at=?,payload_ciphertext='',message='Draft cancelled before any send attempt' WHERE application_id=? AND state='READY'",
                (now, now, application_id),
            )
            if cursor.rowcount:
                self.connection.execute(
                    "UPDATE applications SET queue_state='READY',updated_at=? WHERE id=? AND status='QUEUED' AND queue_state='EMAIL_READY'",
                    (now, application_id),
                )
                self.log("application_email_draft_cancelled", str(application_id))
            return cursor.rowcount == 1

    def claim_email_outbox(
        self,
        application_id: int,
        *,
        package_hash: str,
        daily_limit: int,
        expected_scope_fingerprint: str,
        require_email_autopilot: bool = False,
    ) -> dict[str, object] | None:
        """Atomically reserve a daily slot and permit a single provider call."""
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            outbox = self.connection.execute(
                "SELECT id,provider,idempotency_key,package_hash,payload_ciphertext,state FROM email_outbox WHERE application_id=?",
                (application_id,),
            ).fetchone()
            application = self.connection.execute(
                "SELECT status,queue_state FROM applications WHERE id=?", (application_id,)
            ).fetchone()
            settings = {row[0]: row[1] for row in self.connection.execute(
                "SELECT key,value FROM settings WHERE key IN ('application_mode','dry_run','automation_paused','daily_limit')"
            )}
            try:
                configured_daily_limit = int(settings.get("daily_limit", "0") or "0")
            except (TypeError, ValueError):
                configured_daily_limit = 0
            prior_attempt = self.connection.execute(
                "SELECT 1 FROM application_attempts WHERE application_id=? AND state<>'CANCELLED' LIMIT 1",
                (application_id,),
            ).fetchone()
            if (
                not outbox or outbox["state"] != "READY" or outbox["package_hash"] != package_hash
                or not application or application["status"] != "QUEUED" or application["queue_state"] != "EMAIL_READY"
                or prior_attempt or daily_limit <= 0 or settings.get("dry_run", "true") != "false"
                or settings.get("automation_paused", "false") == "true"
                or configured_daily_limit != daily_limit
                or not hmac.compare_digest(expected_scope_fingerprint, self.automation_scope_fingerprint())
                or (settings.get("application_mode") == "autopilot" and not self.autopilot_authorized())
                or (require_email_autopilot and not self.email_send_autopilot_authorized())
            ):
                self.connection.rollback()
                return None
            day = now[:10]
            reserved = int(self.connection.execute(
                "SELECT COUNT(DISTINCT a.id) FROM applications a LEFT JOIN application_attempts t ON t.application_id=a.id "
                "LEFT JOIN email_outbox e ON e.application_id=a.id "
                "WHERE (a.status IN ('APPLIED','APPLIED_MANUAL','EMAIL_ACCEPTED','EMAIL_SUBMITTED_UNVERIFIED') AND substr(a.updated_at,1,10)=?) "
                "OR (substr(t.started_at,1,10)=? AND t.state IN ('SUBMITTING','SUBMITTED','UNKNOWN','CAPTCHA_HOLD')) "
                "OR (substr(e.started_at,1,10)=? AND e.state IN ('SENDING','ACCEPTED','UNKNOWN','FAILED_FINAL'))",
                (day, day, day),
            ).fetchone()[0])
            if reserved >= daily_limit:
                self.connection.rollback()
                return None
            cursor = self.connection.execute(
                "UPDATE email_outbox SET state='SENDING',started_at=?,updated_at=?,message='Single email provider attempt reserved; automatic retry disabled' WHERE id=? AND state='READY'",
                (now, now, int(outbox["id"])),
            )
            if cursor.rowcount != 1:
                self.connection.rollback()
                return None
            self.connection.execute(
                "UPDATE applications SET status='EMAIL_SUBMITTING',queue_state='SUBMITTING',updated_at=? WHERE id=?",
                (now, application_id),
            )
            self.connection.execute(
                "INSERT INTO application_timeline(application_id,status,note,created_at) VALUES (?,'EMAIL_SUBMITTING','Single provider send reserved; automatic retry is disabled',?)",
                (application_id, now),
            )
            self.log("application_email_send_reserved", f"{application_id}:{package_hash[:12]}")
            self.connection.commit()
            return dict(outbox)
        except Exception:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise

    def finish_email_outbox(
        self,
        application_id: int,
        *,
        state: str,
        message: str,
        provider_reference: str = "",
    ) -> bool:
        if state not in {"ACCEPTED", "UNKNOWN", "FAILED_FINAL"}:
            raise ValueError("Unsupported final email outbox state")
        now = datetime.now(timezone.utc).isoformat()
        application_status = {
            "ACCEPTED": "EMAIL_ACCEPTED",
            "UNKNOWN": "EMAIL_SUBMITTED_UNVERIFIED",
            "FAILED_FINAL": "EMAIL_FAILED",
        }[state]
        safe_message = re.sub(r"[\r\n\x00-\x1f]+", " ", message).strip()[:240]
        safe_reference = re.sub(r"[^A-Za-z0-9._:-]", "", provider_reference)[:160]
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = self.connection.execute(
                "UPDATE email_outbox SET state=?,updated_at=?,finished_at=?,provider_reference=?,message=? WHERE application_id=? AND state='SENDING'",
                (state, now, now, safe_reference, safe_message, application_id),
            )
            if cursor.rowcount != 1:
                self.connection.rollback()
                return False
            self.connection.execute(
                "UPDATE applications SET status=?,queue_state='DO_NOT_RETRY',updated_at=?,notes=? WHERE id=?",
                (application_status, now, safe_message, application_id),
            )
            self.connection.execute(
                "INSERT INTO application_timeline(application_id,status,note,created_at) VALUES (?,?,?,?)",
                (application_id, application_status, safe_message, now),
            )
            self.log("application_email_send_finished", f"{application_id}:{state}")
            self.connection.commit()
            return True
        except Exception:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise

    def remove_mail_send_connection(self) -> None:
        self.connection.execute("DELETE FROM mail_send_accounts")
        self.connection.execute("DELETE FROM mail_send_connection WHERE id=1")
        self.connection.execute("UPDATE settings SET value='' WHERE key='mail_send_default_account_id'")
        self._revoke_email_send_autopilot()
        self.log("mail_send_disconnected", "Separate email-send authorization removed")
        self.connection.commit()

    def save_mailbox_connection(self, provider: str, token_ciphertext: str) -> None:
        if provider not in {"gmail", "microsoft"} or not token_ciphertext.strip():
            raise ValueError("A supported provider and encrypted token payload are required")
        self.connection.execute(
            "INSERT INTO mailbox_connection(id, provider, token_ciphertext, connected_at) VALUES(1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET provider=excluded.provider, token_ciphertext=excluded.token_ciphertext, connected_at=excluded.connected_at",
            (provider, token_ciphertext, datetime.now(timezone.utc).isoformat()),
        )
        self.log("mailbox_connected", provider)
        self.connection.commit()

    def remove_mailbox_connection(self) -> None:
        self.connection.execute("DELETE FROM mailbox_connection WHERE id=1")
        self.connection.execute("DELETE FROM mailbox_messages")
        self.log("mailbox_disconnected", "Mailbox tokens and synced message metadata removed")
        self.connection.commit()

    def store_mailbox_messages(self, provider: str, messages: list[object]) -> int:
        stored = 0
        for message in messages[:50]:
            cursor = self.connection.execute(
                "INSERT INTO mailbox_messages(provider, provider_message_id, sender, subject, snippet, received_at, link, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(provider, provider_message_id) DO UPDATE SET sender=excluded.sender, subject=excluded.subject, snippet=excluded.snippet, received_at=excluded.received_at, link=excluded.link",
                (provider, str(message.provider_message_id)[:300], str(message.sender)[:300], str(message.subject)[:500], str(message.snippet)[:300], str(message.received_at)[:200], str(message.link)[:1000], datetime.now(timezone.utc).isoformat()),
            )
            stored += int(cursor.rowcount > 0)
        self.connection.commit()
        return stored

    def mailbox_messages(self, *, include_reviewed: bool = True) -> list[dict[str, object]]:
        sql = "SELECT * FROM mailbox_messages" + ("" if include_reviewed else " WHERE reviewed=0") + " ORDER BY received_at DESC, id DESC LIMIT 100"
        return [dict(row) for row in self.connection.execute(sql)]

    def confirm_mailbox_response(self, message_id: int, application_id: int, status: str) -> None:
        allowed = {"APPLICATION_RECEIVED", "INTERVIEW", "ASSESSMENT", "OFFER", "REJECTED"}
        message = self.connection.execute("SELECT * FROM mailbox_messages WHERE id=?", (message_id,)).fetchone()
        application = self.application(application_id)
        if status not in allowed or not message or not application:
            raise ValueError("Select a saved message, application, and supported outcome")
        note = f"Confirmed from email review: {message['subject']} — {message['sender']} ({message['received_at']})"
        self.update_application_status(application_id, status, note)
        self.connection.execute("UPDATE mailbox_messages SET reviewed=1 WHERE id=?", (message_id,))
        self.connection.commit()

    def queue_application(self, job_id: int, *, language: str, cv_path: str | None, notes: str = "") -> int:
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            if self.connection.execute("SELECT 1 FROM application_claims WHERE job_id=?", (job_id,)).fetchone():
                raise ValueError("An application already exists for this job")
            cursor = self.connection.execute("INSERT INTO applications(job_id, status, queue_state, language, cv_path, notes, created_at, updated_at) VALUES (?, 'QUEUED', 'READY', ?, ?, ?, ?, ?)", (job_id, language, cv_path, notes[:500], now, now))
            application_id = int(cursor.lastrowid)
            self.connection.execute("INSERT INTO application_claims(job_id, application_id) VALUES (?, ?)", (job_id, application_id))
            self.connection.execute("INSERT INTO application_timeline(application_id, status, note, created_at) VALUES (?, 'QUEUED', 'Application added to queue', ?)", (application_id, now))
            self.log("queue_created", str(application_id))
            self.connection.commit()
            return application_id
        except Exception:
            self.connection.rollback()
            raise

    def claim_application_preparation(self, application_id: int, *, owner_token: str) -> bool:
        """Fence one worker while it prepares a form, before any submit attempt exists."""
        token = owner_token.strip()
        if not token:
            raise ValueError("A preparation owner token is required")
        if self.connection.in_transaction:
            self.connection.commit()
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = self.connection.execute(
                "UPDATE applications SET queue_state='PREPARING',preparation_token=?,updated_at=? "
                "WHERE id=? AND status='QUEUED' AND queue_state='READY'",
                (token, now, application_id),
            )
            if cursor.rowcount != 1:
                self.connection.rollback()
                return False
            self.connection.execute(
                "INSERT INTO application_timeline(application_id,status,note,created_at) "
                "VALUES (?,'PREPARING','Worker claimed this application for form preparation',?)",
                (application_id, now),
            )
            self.log("application_preparation_claimed", str(application_id))
            self.connection.commit()
            return True
        except Exception:
            self.connection.rollback()
            raise

    def owns_application_preparation(self, application_id: int, *, owner_token: str) -> bool:
        row = self.connection.execute(
            "SELECT status,queue_state,preparation_token FROM applications WHERE id=?",
            (application_id,),
        ).fetchone()
        return bool(
            row and row["status"] == "QUEUED" and row["queue_state"] == "PREPARING"
            and row["preparation_token"] == owner_token
        )

    def release_application_preparation(self, application_id: int, *, owner_token: str) -> bool:
        """Release only this worker's pre-submit claim; never change a later state."""
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = self.connection.execute(
                "UPDATE applications SET queue_state='READY',preparation_token=NULL,updated_at=? "
                "WHERE id=? AND status='QUEUED' AND queue_state='PREPARING' AND preparation_token=?",
                (now, application_id, owner_token),
            )
            if cursor.rowcount:
                self.connection.execute(
                    "INSERT INTO application_timeline(application_id,status,note,created_at) "
                    "VALUES (?,'QUEUED','Pre-submit preparation ended without an external submission',?)",
                    (application_id, now),
                )
                self.log("application_preparation_released", str(application_id))
            self.connection.commit()
            return cursor.rowcount == 1
        except Exception:
            self.connection.rollback()
            raise

    def recover_preparing_applications(self) -> int:
        """Requeue only abandoned preparation; submission attempts remain non-retryable."""
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            rows = self.connection.execute(
                "SELECT id FROM applications WHERE status='QUEUED' AND queue_state='PREPARING'"
            ).fetchall()
            for row in rows:
                application_id = int(row["id"])
                self.connection.execute(
                    "UPDATE applications SET queue_state='READY',preparation_token=NULL,updated_at=? "
                    "WHERE id=? AND status='QUEUED' AND queue_state='PREPARING'",
                    (now, application_id),
                )
                self.connection.execute(
                    "INSERT INTO application_timeline(application_id,status,note,created_at) "
                    "VALUES (?,'QUEUED','Recovered after interruption before any submit attempt',?)",
                    (application_id, now),
                )
            if rows:
                self.log("application_preparations_recovered", str(len(rows)))
            self.connection.commit()
            return len(rows)
        except Exception:
            self.connection.rollback()
            raise

    def hold_for_captcha(self, application_id: int, *, note: str = "") -> int:
        """Stop at an access challenge; store a handoff URL only when it is safe."""
        from sampoagent.applications.urls import is_safe_public_https_url

        application = self.application(application_id)
        if not application:
            raise ValueError("Application does not exist")
        job = self.job(int(application["job_id"]))
        official_url = str((job or {}).get("application_url", ""))
        handoff_url = official_url if is_safe_public_https_url(official_url) else ""
        now = datetime.now(timezone.utc).isoformat()
        with self.connection:
            self.connection.execute(
                "INSERT INTO captcha_tasks(application_id, detected_url, state, note, created_at) VALUES (?, ?, 'WAITING_USER', ?, ?) ON CONFLICT(application_id) DO UPDATE SET detected_url=excluded.detected_url, state=CASE WHEN captcha_tasks.state='IN_PROGRESS' THEN 'IN_PROGRESS' ELSE 'WAITING_USER' END, note=excluded.note, finished_at=NULL",
                (application_id, handoff_url, note[:500], now),
            )
            task_id = int(self.connection.execute("SELECT id FROM captcha_tasks WHERE application_id=?", (application_id,)).fetchone()[0])
            self.connection.execute("UPDATE applications SET status='CAPTCHA_HOLD', queue_state='WAITING_USER', updated_at=? WHERE id=?", (now, application_id))
            self.connection.execute("INSERT INTO application_timeline(application_id, status, note, created_at) VALUES (?, 'CAPTCHA_HOLD', 'Access challenge requires candidate action; automatic submission paused', ?)", (application_id, now))
            self.log("captcha_task_created", str(application_id))
        return task_id

    def captcha_tasks(self) -> list[dict[str, object]]:
        from sampoagent.applications.urls import is_safe_public_https_url

        rows = self.connection.execute(
            "SELECT t.id, t.application_id, t.state, t.note, t.outcome, t.created_at, t.started_at, t.finished_at, "
            "a.job_id, j.title, j.company, j.application_url AS verified_application_url "
            "FROM captcha_tasks t JOIN applications a ON a.id=t.application_id "
            "JOIN jobs j ON j.id=a.job_id WHERE t.state IN ('WAITING_USER','IN_PROGRESS') "
            "ORDER BY CASE t.state WHEN 'IN_PROGRESS' THEN 0 ELSE 1 END, t.created_at, t.id"
        )
        tasks = []
        for row in rows:
            task = dict(row)
            official_url = str(task.pop("verified_application_url", "") or "")
            # Also protect tasks created by older releases that persisted redirects.
            task["official_url"] = official_url if is_safe_public_https_url(official_url) else ""
            tasks.append(task)
        return tasks

    def begin_captcha_task(self, task_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            active = self.connection.execute("SELECT id FROM captcha_tasks WHERE state='IN_PROGRESS' AND id<>? LIMIT 1", (task_id,)).fetchone()
            task = self.connection.execute("SELECT application_id, state FROM captcha_tasks WHERE id=?", (task_id,)).fetchone()
            if not task or task["state"] != "WAITING_USER":
                raise ValueError("CAPTCHA task is not waiting")
            if active:
                raise ValueError("Handle CAPTCHA tasks one at a time")
            self.connection.execute("UPDATE captcha_tasks SET state='IN_PROGRESS', started_at=? WHERE id=?", (now, task_id))
            self.connection.execute("UPDATE applications SET queue_state='IN_PROGRESS', updated_at=? WHERE id=?", (now, int(task["application_id"])))
            self.log("captcha_task_started", str(int(task["application_id"])))
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def finish_captcha_task(self, task_id: int, *, outcome: str, confirmation_message: str = "") -> None:
        if outcome not in {"submitted", "not_submitted", "skip"}:
            raise ValueError("Unsupported CAPTCHA task outcome")
        if outcome == "submitted" and not confirmation_message.strip():
            raise ValueError("Record the employer confirmation before marking submitted")
        if outcome == "submitted" and any(secret in confirmation_message.casefold() for secret in ("password", "access token", "secret=")):
            raise ValueError("Confirmation must not contain credentials")
        now = datetime.now(timezone.utc).isoformat()
        final_status = {"submitted": "APPLIED_MANUAL", "not_submitted": "NOT_SUBMITTED", "skip": "WITHDRAWN"}[outcome]
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            task = self.connection.execute(
                "SELECT application_id, state FROM captcha_tasks WHERE id=?",
                (task_id,),
            ).fetchone()
            if not task or task["state"] != "IN_PROGRESS":
                raise ValueError("CAPTCHA task is not active")
            self.connection.execute("UPDATE captcha_tasks SET state='COMPLETED', outcome=?, finished_at=? WHERE id=?", (outcome, now, task_id))
            self.connection.execute("UPDATE applications SET status=?, queue_state='COMPLETED', updated_at=? WHERE id=?", (final_status, now, int(task["application_id"])))
            self.connection.execute("INSERT INTO application_timeline(application_id, status, note, created_at) VALUES (?, ?, ?, ?)", (int(task["application_id"]), final_status, "Candidate completed the CAPTCHA step manually" + (": " + confirmation_message.strip()[:300] if outcome == "submitted" else ""), now))
            if outcome == "submitted":
                self.connection.execute("INSERT INTO submission_evidence(application_id, final_url, confirmation_message, confirmation_id, agent_provider, created_at) VALUES (?, '', ?, NULL, 'manual_captcha', ?)", (int(task["application_id"]), confirmation_message.strip()[:300], now))
            self.log("captcha_task_completed", f"{int(task['application_id'])}: {outcome}")
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def archive_cv(
        self, *, path: str, checksum: str, language: str, role_family: str,
        source_job_id: int | None, fit_score: int, strategy: str,
        text_check_score: int | None = None, ats_score: int | None = None,
    ) -> None:
        if text_check_score is not None and ats_score is not None and text_check_score != ats_score:
            raise ValueError("Conflicting legacy and current CV text-check scores")
        provided_score = text_check_score if text_check_score is not None else ats_score
        if language not in {"fi", "en", "unknown"} or not 0 <= fit_score <= 100 or (provided_score is not None and not 0 <= provided_score <= 100) or strategy not in {"uploaded", "generated", "reused"}:
            raise ValueError("Invalid CV archive metadata")
        score = 0 if provided_score is None else provided_score
        checked = int(provided_score is not None)
        self.connection.execute(
            "INSERT OR IGNORE INTO cv_archive(path, checksum, language, role_family, source_job_id, fit_score, ats_score, text_check_performed, text_check_score, strategy, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (path, checksum, language, role_family, source_job_id, fit_score, score, checked, score, strategy, datetime.now(timezone.utc).isoformat()),
        )
        self.connection.commit()

    def cv_archives(self) -> list[dict[str, object]]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM cv_archive ORDER BY created_at DESC, id DESC")]

    def has_application_for_job(self, job_id: int) -> bool:
        """Prevent a job from being prepared or submitted more than once."""
        return self.connection.execute(
            "SELECT 1 FROM applications WHERE job_id=? LIMIT 1", (job_id,)
        ).fetchone() is not None

    def has_other_application_for_job(self, job_id: int, application_id: int) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM applications WHERE job_id=? AND id<>? LIMIT 1", (job_id, application_id)
        ).fetchone() is not None

    def update_application_cv_path(self, application_id: int, path: str) -> None:
        if not Path(path).is_file():
            raise ValueError("Application CV must be a readable local file")
        self.connection.execute("UPDATE applications SET cv_path=?,updated_at=? WHERE id=?", (path, datetime.now(timezone.utc).isoformat(), application_id))
        self.connection.commit()

    def resume_application(self, application_id: int) -> bool:
        application = self.application(application_id)
        if not application or application.get("status") not in {"NEEDS_USER", "NEEDS_AUTH", "NOT_SUBMITTED"}:
            return False
        if self.connection.execute("SELECT 1 FROM application_attempts WHERE application_id=? AND state<>'CANCELLED' LIMIT 1", (application_id,)).fetchone():
            return False
        if self.connection.execute("SELECT 1 FROM captcha_tasks WHERE application_id=? AND state<>'COMPLETED' LIMIT 1", (application_id,)).fetchone():
            return False
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute("UPDATE applications SET status='QUEUED',queue_state='READY',updated_at=? WHERE id=?", (now, application_id))
        self.connection.execute("INSERT INTO application_timeline(application_id,status,note,created_at) VALUES (?,'QUEUED','Candidate resumed the application after resolving a required action',?)", (application_id, now))
        self.log("application_resumed", str(application_id))
        self.connection.commit()
        return True

    def set_job_override(self, job_id: int, *, decision: str, note: str = "") -> None:
        if decision not in {"review"}:
            raise ValueError("Unsupported job override")
        self.connection.execute(
            "INSERT INTO job_overrides(job_id, decision, note, created_at) VALUES (?, ?, ?, ?) ON CONFLICT(job_id) DO UPDATE SET decision=excluded.decision, note=excluded.note, created_at=excluded.created_at",
            (job_id, decision, note.strip(), datetime.now(timezone.utc).isoformat()),
        )
        self.log("job_override", f"{job_id}: {decision}")
        self.connection.commit()

    def job_override(self, job_id: int) -> dict[str, object] | None:
        row = self.connection.execute("SELECT * FROM job_overrides WHERE job_id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def update_application_status(self, application_id: int, status: str, note: str = "", *, queue_state: str | None = None) -> None:
        if queue_state is not None and queue_state not in {"READY", "PREPARING", "SUBMITTING", "IN_PROGRESS", "WAITING_USER", "DO_NOT_RETRY", "COMPLETED"}:
            raise ValueError("Unsupported queue state")
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            "UPDATE applications SET status=?, queue_state=COALESCE(?, queue_state), "
            "preparation_token=CASE WHEN COALESCE(?, '') IN ('', 'PREPARING') THEN preparation_token ELSE NULL END, "
            "notes=CASE WHEN ? <> '' THEN ? ELSE notes END, updated_at=? WHERE id=?",
            (status, queue_state, queue_state, note, note, now, application_id),
        )
        self.connection.execute("INSERT INTO application_timeline(application_id, status, note, created_at) VALUES (?, ?, ?, ?)", (application_id, status, note, now))
        self.log("application_status_changed", f"{application_id}: {status}")
        self.connection.commit()
        self.record_application_learning(application_id, status)

    def record_application_learning(self, application_id: int, outcome: str) -> None:
        if outcome not in {"APPLICATION_RECEIVED", "INTERVIEW", "ASSESSMENT", "OFFER", "REJECTED", "NO_RESPONSE"}:
            return
        app = self.application(application_id)
        job = self.job(int(app["job_id"])) if app else None
        if not job:
            return
        from sampoagent.cv.archive import role_family_for_job

        family = role_family_for_job(job)
        self.connection.execute("INSERT INTO application_learning(application_id, role_family, outcome, observed_at) VALUES (?, ?, ?, ?) ON CONFLICT(application_id) DO UPDATE SET role_family=excluded.role_family, outcome=excluded.outcome, observed_at=excluded.observed_at", (application_id, family, outcome, datetime.now(timezone.utc).isoformat()))
        self.connection.commit()

    def learning_adjustment(self, role_family: str) -> int:
        rows = self.connection.execute(
            "SELECT outcome FROM application_learning WHERE role_family=? "
            "AND outcome IN ('INTERVIEW','ASSESSMENT','OFFER','REJECTED','NO_RESPONSE')",
            (role_family,),
        ).fetchall()
        count = len(rows)
        positive = sum(row[0] in {"INTERVIEW", "ASSESSMENT", "OFFER"} for row in rows)
        # Beta prior (2 successes / 8 failures) shrinks sparse histories to 20%.
        posterior = (positive + 2) / (count + 10)
        return max(-8, min(8, round((posterior - 0.2) * 40)))

    def cv_learning_adjustments(self) -> dict[str, int]:
        """Return small, explainable CV-selection adjustments from confirmed outcomes.

        Only interview/assessment/offer/rejection/no-response events count. A
        sent application, CAPTCHA hold, or unknown result is not a hiring signal.
        """
        rows = self.connection.execute(
            "SELECT DISTINCT ca.checksum, learning.application_id, learning.outcome "
            "FROM application_learning AS learning "
            "JOIN applications AS app ON app.id=learning.application_id "
            "JOIN documents AS doc ON doc.path=app.cv_path AND doc.kind='application_cv' "
            "JOIN cv_archive AS ca ON ca.checksum=doc.checksum "
            "WHERE learning.outcome IN ('INTERVIEW','ASSESSMENT','OFFER','REJECTED','NO_RESPONSE') "
            "ORDER BY ca.checksum, learning.application_id"
        ).fetchall()
        outcomes_by_checksum: dict[str, list[str]] = {}
        for row in rows:
            outcomes_by_checksum.setdefault(str(row["checksum"]), []).append(str(row["outcome"]))
        adjustments: dict[str, int] = {}
        for checksum, outcomes in outcomes_by_checksum.items():
            positive = sum(outcome in {"INTERVIEW", "ASSESSMENT", "OFFER"} for outcome in outcomes)
            posterior = (positive + 2) / (len(outcomes) + 10)
            adjustments[checksum] = max(-8, min(8, round((posterior - 0.2) * 40)))
        return adjustments

    def learning_summary(self) -> list[dict[str, object]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT role_family, COUNT(*) AS outcomes, "
            "SUM(CASE WHEN outcome IN ('INTERVIEW','ASSESSMENT','OFFER') THEN 1 ELSE 0 END) AS positive_outcomes "
            "FROM application_learning "
            "WHERE outcome IN ('INTERVIEW','ASSESSMENT','OFFER','REJECTED','NO_RESPONSE') "
            "GROUP BY role_family ORDER BY outcomes DESC, role_family"
        )]

    def learning_digest(self) -> dict[str, object]:
        """Return a small-cohort-suppressed, de-identified outcome learning digest."""
        confirmed = "'INTERVIEW','ASSESSMENT','OFFER','REJECTED','NO_RESPONSE'"
        role_rows = self.connection.execute(
            "SELECT role_family, COUNT(*) AS confirmed_outcomes, "
            "SUM(CASE WHEN outcome IN ('INTERVIEW','ASSESSMENT','OFFER') THEN 1 ELSE 0 END) AS positive_outcomes "
            f"FROM application_learning WHERE outcome IN ({confirmed}) "
            "GROUP BY role_family HAVING COUNT(*) >= 5 ORDER BY confirmed_outcomes DESC, role_family"
        ).fetchall()
        role_families = [
            {
                "role_family": str(row["role_family"]),
                "confirmed_outcomes": int(row["confirmed_outcomes"]),
                "positive_outcomes": int(row["positive_outcomes"]),
                "ranking_adjustment": self.learning_adjustment(str(row["role_family"])),
            }
            for row in role_rows
        ]

        cv_rows = self.connection.execute(
            "SELECT DISTINCT ca.checksum, ca.role_family, lower(ca.language) AS language, "
            "learning.application_id, learning.outcome "
            "FROM application_learning AS learning "
            "JOIN applications AS app ON app.id=learning.application_id "
            "JOIN documents AS doc ON doc.path=app.cv_path AND doc.kind='application_cv' "
            "JOIN cv_archive AS ca ON ca.checksum=doc.checksum "
            f"WHERE learning.outcome IN ({confirmed}) "
            "ORDER BY ca.checksum, learning.application_id"
        ).fetchall()
        outcomes_by_checksum: dict[tuple[str, str, str], list[str]] = {}
        for row in cv_rows:
            language = str(row["language"])
            if language not in {"fi", "sv", "en"}:
                language = "other"
            key = (str(row["checksum"]), str(row["role_family"]), language)
            outcomes_by_checksum.setdefault(key, []).append(str(row["outcome"]))

        grouped: dict[tuple[str, str], dict[str, object]] = {}
        for (_, role_family, language), outcomes in outcomes_by_checksum.items():
            if len(outcomes) < 5:
                continue
            positive = sum(outcome in {"INTERVIEW", "ASSESSMENT", "OFFER"} for outcome in outcomes)
            posterior = (positive + 2) / (len(outcomes) + 10)
            adjustment = max(-8, min(8, round((posterior - 0.2) * 40)))
            group = grouped.setdefault(
                (role_family, language),
                {
                    "role_family": role_family,
                    "language": language,
                    "cv_variants": 0,
                    "confirmed_outcomes": 0,
                    "positive_outcomes": 0,
                    "mature_variants": 0,
                    "_adjustments": [],
                },
            )
            group["cv_variants"] = int(group["cv_variants"]) + 1
            group["confirmed_outcomes"] = int(group["confirmed_outcomes"]) + len(outcomes)
            group["positive_outcomes"] = int(group["positive_outcomes"]) + positive
            group["mature_variants"] = int(group["mature_variants"]) + 1
            adjustments = group["_adjustments"]
            assert isinstance(adjustments, list)
            adjustments.append(adjustment)

        cv_groups: list[dict[str, object]] = []
        for key in sorted(grouped):
            group = grouped[key]
            adjustments = group.pop("_adjustments")
            assert isinstance(adjustments, list)
            group["mean_selection_adjustment"] = round(sum(adjustments) / len(adjustments))
            cv_groups.append(group)

        return {
            "data_boundary": {
                "scope": "aggregated_confirmed_outcomes_only",
                "candidate_facts_included": False,
                "cv_content_included": False,
                "direct_identifiers_included": False,
            },
            "role_families": role_families,
            "cv_groups": cv_groups,
        }

    def application_timeline(self, application_id: int) -> list[dict[str, object]]:
        return [dict(row) for row in self.connection.execute("SELECT status, note, created_at FROM application_timeline WHERE application_id=? ORDER BY id", (application_id,))]

    def applications_today(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM applications WHERE status IN ('APPLIED','APPLIED_MANUAL','EMAIL_ACCEPTED','EMAIL_SUBMITTED_UNVERIFIED') AND substr(updated_at, 1, 10)=?", (datetime.now(timezone.utc).date().isoformat(),)).fetchone()[0])

    def submissions_reserved_today(self) -> int:
        today = datetime.now(timezone.utc).date().isoformat()
        row = self.connection.execute(
            "SELECT COUNT(DISTINCT a.id) FROM applications a LEFT JOIN application_attempts t ON t.application_id=a.id "
            "LEFT JOIN email_outbox e ON e.application_id=a.id "
            "WHERE (a.status IN ('APPLIED','APPLIED_MANUAL','EMAIL_ACCEPTED','EMAIL_SUBMITTED_UNVERIFIED') AND substr(a.updated_at,1,10)=?) "
            "OR (substr(t.started_at,1,10)=? AND t.state IN ('SUBMITTING','SUBMITTED','UNKNOWN','CAPTCHA_HOLD')) "
            "OR (substr(e.started_at,1,10)=? AND e.state IN ('SENDING','ACCEPTED','UNKNOWN','FAILED_FINAL'))",
            (today, today, today),
        ).fetchone()
        return int(row[0])

    def recover_interrupted_email_sends(self, *, stale_seconds: int = 120) -> int:
        """Mark stale in-flight sends uncertain so they can never be replayed."""
        if not 30 <= stale_seconds <= 3600:
            raise ValueError("Email send recovery threshold must be from 30 seconds to one hour")
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(seconds=stale_seconds)).isoformat()
        rows = self.connection.execute(
            "SELECT id,application_id FROM email_outbox WHERE state='SENDING' AND started_at < ?",
            (cutoff,),
        ).fetchall()
        if not rows:
            return 0
        finished = now.isoformat()
        with self.connection:
            for row in rows:
                application_id = int(row["application_id"])
                cursor = self.connection.execute(
                    "UPDATE email_outbox SET state='UNKNOWN',updated_at=?,finished_at=?,message='The local process stopped during send; provider outcome is uncertain and automatic retry is disabled' WHERE id=? AND state='SENDING'",
                    (finished, finished, int(row["id"])),
                )
                if not cursor.rowcount:
                    continue
                self.connection.execute(
                    "UPDATE applications SET status='EMAIL_SUBMITTED_UNVERIFIED',queue_state='DO_NOT_RETRY',updated_at=?,notes='Email send interrupted; check Sent folder or contact the employer before any manual retry' WHERE id=? AND status='EMAIL_SUBMITTING'",
                    (finished, application_id),
                )
                self.connection.execute(
                    "INSERT INTO application_timeline(application_id,status,note,created_at) VALUES (?,'EMAIL_SUBMITTED_UNVERIFIED','Local process stopped during send; reconcile provider state before any retry',?)",
                    (application_id, finished),
                )
                self.log("application_email_interrupted", str(application_id))
        return len(rows)

    def _autopilot_fingerprint(self) -> str:
        profile = self.profile() or {}
        facts = [dict(row) for row in self.connection.execute("SELECT type,value,provenance,source_id,confidence,confirmed,rejected FROM facts WHERE confirmed=1 AND rejected=0 ORDER BY id")]
        records = [dict(row) for row in self.connection.execute("SELECT record_type,payload FROM candidate_records ORDER BY id")]
        answers = [dict(row) for row in self.connection.execute("SELECT category,question,value,source FROM answer_bank ORDER BY id")]
        targets = [dict(row) for row in self.connection.execute("SELECT title_en,title_fi,enabled FROM target_occupations ORDER BY id")]
        career_profiles = [dict(row) for row in self.connection.execute("SELECT name,enabled,notes FROM career_profiles ORDER BY id")]
        sources = [dict(row) for row in self.connection.execute("SELECT id,url,enabled,capability,terms_url,terms_reviewed,listing_selector FROM job_sources ORDER BY id")]
        snapshot = {
            "policy_version": _AUTOPILOT_POLICY_VERSION,
            "profile": profile,
            "facts": facts,
            "records": records,
            "answers": answers,
            "targets": targets,
            "career_profiles": career_profiles,
            "sources": sources,
            "preferences": self.preferences(),
            "application_mode": self.setting("application_mode"),
            "daily_limit": self.setting("daily_limit"),
        }
        payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return sha256(payload.encode("utf-8")).hexdigest()

    def automation_scope_fingerprint(self) -> str:
        """Hash candidate, answer, target, source, preference and quota inputs."""
        return self._autopilot_fingerprint()

    def has_explicit_search_scope(self) -> bool:
        if self.connection.execute("SELECT 1 FROM target_occupations WHERE enabled=1 LIMIT 1").fetchone():
            return True
        if self.connection.execute("SELECT 1 FROM career_profiles WHERE enabled=1 LIMIT 1").fetchone():
            return True
        preferences = self.preferences()
        return any(
            str(preferences.get(key, "")).strip()
            for key in ("keywords", "search_terms_include", "title_include", "industries", "employer_include")
        )

    def grant_autopilot(self, *, days: int = 30) -> None:
        if not self.profile() or self.setting("application_mode") != "autopilot" or self.setting("dry_run") != "false":
            raise ValueError("Select Autopilot, create a candidate profile, and turn Dry Run off before granting automation")
        try:
            daily_limit = int(self.setting("daily_limit") or "0")
        except ValueError:
            daily_limit = 0
        if daily_limit <= 0 or not 1 <= days <= 30:
            raise ValueError("Autopilot requires a positive daily limit and a grant of at most 30 days")
        if not self.has_explicit_search_scope():
            raise ValueError("Select at least one target occupation or explicit search scope before authorizing Autopilot")
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(days=days)).isoformat()
        fingerprint = self._autopilot_fingerprint()
        with self.connection:
            self.connection.execute("UPDATE settings SET value='true' WHERE key='autopilot_authorized'")
            self.connection.execute("UPDATE settings SET value=? WHERE key='autopilot_grant_policy_version'", (_AUTOPILOT_POLICY_VERSION,))
            self.connection.execute("UPDATE settings SET value=? WHERE key='autopilot_grant_fingerprint'", (fingerprint,))
            self.connection.execute("UPDATE settings SET value=? WHERE key='autopilot_grant_expires_at'", (expires,))
            self.log("autopilot_granted", f"Expires {expires[:10]}; scoped to current profile and preferences")

    def autopilot_authorized(self) -> bool:
        if (
            self.setting("autopilot_authorized") != "true"
            or self.setting("autopilot_grant_policy_version") != _AUTOPILOT_POLICY_VERSION
        ):
            return False
        expires = self.setting("autopilot_grant_expires_at") or ""
        try:
            if datetime.fromisoformat(expires) <= datetime.now(timezone.utc):
                return False
        except ValueError:
            return False
        saved = self.setting("autopilot_grant_fingerprint") or ""
        return bool(saved and hmac.compare_digest(saved, self._autopilot_fingerprint()))

    def revoke_autopilot(self, reason: str = "") -> None:
        active = self.setting("autopilot_authorized") == "true"
        self.connection.execute("UPDATE settings SET value='false' WHERE key='autopilot_authorized'")
        self.connection.execute("UPDATE settings SET value='' WHERE key='autopilot_grant_policy_version'")
        self.connection.execute("UPDATE settings SET value='' WHERE key='autopilot_grant_fingerprint'")
        self.connection.execute("UPDATE settings SET value='' WHERE key='autopilot_grant_expires_at'")
        self.connection.execute("UPDATE settings SET value='false' WHERE key='email_send_autopilot_authorized'")
        self.connection.execute("UPDATE settings SET value='' WHERE key='email_send_autopilot_fingerprint'")
        self.connection.execute("UPDATE settings SET value='' WHERE key='email_send_autopilot_expires_at'")
        if active and reason:
            self.log("autopilot_revoked", reason[:160])

    def _email_send_autopilot_fingerprint(self) -> str:
        connection = self.mail_send_connection() or {}
        payload = {
            "automation_scope": self.automation_scope_fingerprint(),
            "account_id": connection.get("id", ""),
            "provider": connection.get("provider", ""),
            "provider_subject": connection.get("provider_subject", ""),
            "sender_email": connection.get("sender_email", ""),
            "address_status": connection.get("address_status", ""),
            "connected_at": connection.get("connected_at", ""),
            "granted_scopes": connection.get("granted_scopes", ""),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return sha256(canonical.encode("utf-8")).hexdigest()

    def _revoke_email_send_autopilot(self) -> None:
        self.connection.execute("UPDATE settings SET value='false' WHERE key='email_send_autopilot_authorized'")
        self.connection.execute("UPDATE settings SET value='' WHERE key='email_send_autopilot_fingerprint'")
        self.connection.execute("UPDATE settings SET value='' WHERE key='email_send_autopilot_expires_at'")

    def grant_email_send_autopilot(self, *, days: int = 30) -> None:
        connection = self.mail_send_connection()
        if (
            not self.autopilot_authorized()
            or self.setting("application_mode") != "autopilot"
            or self.setting("dry_run") != "false"
            or self.setting("automation_paused") == "true"
            or not connection
            or not 1 <= days <= 30
        ):
            raise ValueError("Email Autopilot needs an active Full Autopilot grant and a separate send-only account")
        granted_scopes = set(str(connection.get("granted_scopes", "")).split())
        required_scope = "https://www.googleapis.com/auth/gmail.send" if connection["provider"] == "gmail" else "Mail.Send"
        if required_scope not in granted_scopes or not {"openid", "email"}.issubset(granted_scopes):
            raise ValueError("The connected email account has not granted send-only permission")
        now = datetime.now(timezone.utc)
        try:
            autopilot_expiry = datetime.fromisoformat(str(self.setting("autopilot_grant_expires_at")))
            if autopilot_expiry.tzinfo is None:
                autopilot_expiry = autopilot_expiry.replace(tzinfo=timezone.utc)
        except ValueError:
            raise ValueError("The Full Autopilot grant has expired") from None
        expires = min(now + timedelta(days=days), autopilot_expiry)
        if expires <= now:
            raise ValueError("The Full Autopilot grant has expired")
        fingerprint = self._email_send_autopilot_fingerprint()
        with self.connection:
            self.connection.execute("UPDATE settings SET value='true' WHERE key='email_send_autopilot_authorized'")
            self.connection.execute("UPDATE settings SET value=? WHERE key='email_send_autopilot_fingerprint'", (fingerprint,))
            self.connection.execute("UPDATE settings SET value=? WHERE key='email_send_autopilot_expires_at'", (expires.isoformat(),))
            self.log("email_send_autopilot_granted", f"Expires {expires.date().isoformat()}; bound to current job scope and send account")

    def email_send_autopilot_authorized(self) -> bool:
        if (
            self.setting("email_send_autopilot_authorized") != "true"
            or not self.autopilot_authorized()
            or self.setting("application_mode") != "autopilot"
            or self.setting("dry_run") != "false"
            or self.setting("automation_paused") == "true"
            or not self.mail_send_connection()
        ):
            return False
        try:
            expires = datetime.fromisoformat(str(self.setting("email_send_autopilot_expires_at")))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires <= datetime.now(timezone.utc):
                return False
        except ValueError:
            return False
        saved = self.setting("email_send_autopilot_fingerprint") or ""
        return bool(saved and hmac.compare_digest(saved, self._email_send_autopilot_fingerprint()))

    def reserve_submission_attempt(self, application_id: int, *, daily_limit: int, package_hash: str, preparation_token: str) -> int | None:
        """Atomically reserve a daily slot and make at most one external submit attempt."""
        if self.connection.in_transaction:
            self.connection.commit()
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            application = self.connection.execute("SELECT status,queue_state,preparation_token FROM applications WHERE id=?", (application_id,)).fetchone()
            prior = self.connection.execute("SELECT 1 FROM application_attempts WHERE application_id=? AND state<>'CANCELLED' LIMIT 1", (application_id,)).fetchone()
            if not application or application["status"] != "QUEUED" or application["queue_state"] != "PREPARING" or application["preparation_token"] != preparation_token or prior or daily_limit <= 0:
                self.connection.rollback()
                return None
            day = now[:10]
            allocated = int(self.connection.execute(
                "SELECT COUNT(DISTINCT a.id) FROM applications a LEFT JOIN application_attempts t ON t.application_id=a.id "
                "LEFT JOIN email_outbox e ON e.application_id=a.id "
                "WHERE (a.status IN ('APPLIED','APPLIED_MANUAL','EMAIL_ACCEPTED','EMAIL_SUBMITTED_UNVERIFIED') AND substr(a.updated_at,1,10)=?) "
                "OR (substr(t.started_at,1,10)=? AND t.state IN ('SUBMITTING','SUBMITTED','UNKNOWN','CAPTCHA_HOLD')) "
                "OR (substr(e.started_at,1,10)=? AND e.state IN ('SENDING','ACCEPTED','UNKNOWN','FAILED_FINAL'))",
                (day, day, day),
            ).fetchone()[0])
            if allocated >= daily_limit:
                self.connection.rollback()
                return None
            cursor = self.connection.execute("INSERT INTO application_attempts(application_id,state,package_hash,started_at) VALUES (?,'SUBMITTING',?,?)", (application_id, package_hash, now))
            self.connection.execute("UPDATE applications SET status='SUBMITTING',queue_state='SUBMITTING',preparation_token=NULL,updated_at=? WHERE id=?", (now, application_id))
            self.connection.execute("INSERT INTO application_timeline(application_id,status,note,created_at) VALUES (?,'SUBMITTING','Single submission attempt reserved; automatic retry is disabled',?)", (application_id, now))
            self.log("application_submit_reserved", str(application_id))
            self.connection.commit()
            return int(cursor.lastrowid)
        except Exception:
            self.connection.rollback()
            raise

    def acquire_worker_lease(self, owner: str, *, lease_seconds: int = 300) -> bool:
        if not owner.strip() or not 10 <= lease_seconds <= 1800:
            raise ValueError("Worker lease requires an owner and a duration from 10 to 1800 seconds")
        now = datetime.now(timezone.utc)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute("SELECT owner,expires_at FROM automation_worker_lease WHERE id=1").fetchone()
            if row and row["owner"] != owner:
                try:
                    if datetime.fromisoformat(str(row["expires_at"])) > now:
                        self.connection.rollback()
                        return False
                except ValueError:
                    pass
            expires = (now + timedelta(seconds=lease_seconds)).isoformat()
            self.connection.execute(
                "INSERT INTO automation_worker_lease(id,owner,expires_at,updated_at) VALUES (1,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET owner=excluded.owner,expires_at=excluded.expires_at,updated_at=excluded.updated_at",
                (owner, expires, now.isoformat()),
            )
            self.connection.execute(
                "INSERT INTO automation_worker_status(id,owner,status,started_at,last_heartbeat,next_run_at,last_result) "
                "VALUES (1,?,'running',?,?,NULL,'') ON CONFLICT(id) DO UPDATE SET owner=excluded.owner,status='running', "
                "started_at=excluded.started_at,last_heartbeat=excluded.last_heartbeat,next_run_at=NULL",
                (owner, now.isoformat(), now.isoformat()),
            )
            self.connection.commit()
            return True
        except Exception:
            self.connection.rollback()
            raise

    def renew_worker_lease(self, owner: str, *, lease_seconds: int = 300) -> bool:
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=lease_seconds)).isoformat()
        cursor = self.connection.execute("UPDATE automation_worker_lease SET expires_at=?,updated_at=? WHERE id=1 AND owner=?", (expires, now.isoformat(), owner))
        if cursor.rowcount == 1:
            self.connection.execute(
                "UPDATE automation_worker_status SET last_heartbeat=? WHERE id=1 AND owner=?",
                (now.isoformat(), owner),
            )
        self.connection.commit()
        return cursor.rowcount == 1

    def finish_worker_cycle(self, owner: str, *, status: str, last_result: str, next_run_seconds: int | None = None) -> None:
        if status not in {"completed", "dry_run", "paused", "failed"}:
            raise ValueError("Unsupported worker status")
        if next_run_seconds is not None and not 5 <= next_run_seconds <= 86_400:
            raise ValueError("Next worker run must be between 5 seconds and 24 hours")
        now = datetime.now(timezone.utc)
        next_run_at = (now + timedelta(seconds=next_run_seconds)).isoformat() if next_run_seconds is not None else None
        self.connection.execute(
            "UPDATE automation_worker_status SET owner=NULL,status=?,last_heartbeat=?,next_run_at=?,last_result=? WHERE id=1 AND owner=?",
            (status, now.isoformat(), next_run_at, last_result[:300], owner),
        )
        self.connection.commit()

    def worker_status(self) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT owner,status,started_at,last_heartbeat,next_run_at,last_result FROM automation_worker_status WHERE id=1"
        ).fetchone()
        return dict(row) if row else {
            "owner": None, "status": "not_started", "started_at": None,
            "last_heartbeat": None, "next_run_at": None, "last_result": "",
        }

    def release_worker_lease(self, owner: str) -> None:
        self.connection.execute("DELETE FROM automation_worker_lease WHERE id=1 AND owner=?", (owner,))
        self.connection.commit()

    def ready_applications(self) -> list[dict[str, object]]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM applications WHERE status='QUEUED' AND queue_state='READY' ORDER BY created_at,id")]

    def save_application_review(self, application_id: int, *, package_hash: str, package: dict[str, object]) -> None:
        application = self.application(application_id)
        if not application or application.get("status") not in {"QUEUED", "NEEDS_REVIEW"} or not package_hash.strip():
            raise ValueError("A prepared application and package hash are required")
        payload = json.dumps(package, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        now = datetime.now(timezone.utc).isoformat()
        with self.connection:
            existing = self.connection.execute(
                "SELECT package_hash,state FROM application_reviews WHERE application_id=?",
                (application_id,),
            ).fetchone()
            if existing and existing["package_hash"] == package_hash and existing["state"] == "APPROVED":
                return
            self.connection.execute(
                "INSERT INTO application_reviews(application_id,package_hash,package_json,state,created_at,approved_at) VALUES (?,?,?,'WAITING',?,NULL) "
                "ON CONFLICT(application_id) DO UPDATE SET package_hash=excluded.package_hash,package_json=excluded.package_json,state='WAITING',created_at=excluded.created_at,approved_at=NULL",
                (application_id, package_hash, payload, now),
            )
            self.connection.execute(
                "UPDATE applications SET status='NEEDS_REVIEW',queue_state='WAITING_USER',updated_at=? WHERE id=?",
                (now, application_id),
            )
            self.connection.execute(
                "INSERT INTO application_timeline(application_id,status,note,created_at) VALUES (?,'NEEDS_REVIEW','Exact application package awaits candidate approval',?)",
                (application_id, now),
            )
            self.log("application_package_waiting_review", str(application_id))

    def application_review_approved(self, application_id: int, package_hash: str) -> bool:
        row = self.connection.execute(
            "SELECT package_hash,state FROM application_reviews WHERE application_id=?",
            (application_id,),
        ).fetchone()
        return bool(row and row["state"] == "APPROVED" and row["package_hash"] == package_hash)

    def application_review(self, application_id: int) -> dict[str, object] | None:
        row = self.connection.execute(
            "SELECT package_hash,package_json,state,created_at,approved_at FROM application_reviews WHERE application_id=?",
            (application_id,),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        try:
            result["package"] = json.loads(str(result.pop("package_json")))
        except json.JSONDecodeError:
            return None
        return result

    def approve_application_review(self, application_id: int, package_hash: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            review = self.connection.execute(
                "SELECT package_hash,state FROM application_reviews WHERE application_id=?",
                (application_id,),
            ).fetchone()
            application = self.connection.execute(
                "SELECT status,queue_state FROM applications WHERE id=?",
                (application_id,),
            ).fetchone()
            if (
                not review or review["state"] != "WAITING" or review["package_hash"] != package_hash
                or not application or application["status"] != "NEEDS_REVIEW" or application["queue_state"] != "WAITING_USER"
            ):
                self.connection.rollback()
                return False
            self.connection.execute(
                "UPDATE application_reviews SET state='APPROVED',approved_at=? WHERE application_id=?",
                (now, application_id),
            )
            self.connection.execute(
                "UPDATE applications SET status='QUEUED',queue_state='READY',updated_at=? WHERE id=?",
                (now, application_id),
            )
            self.connection.execute(
                "INSERT INTO application_timeline(application_id,status,note,created_at) VALUES (?,'QUEUED','Candidate approved this exact application package',?)",
                (application_id, now),
            )
            self.log("application_package_approved", str(application_id))
            self.connection.commit()
            return True
        except Exception:
            self.connection.rollback()
            raise

    def finish_submission_attempt(self, application_id: int, *, state: str, message: str) -> None:
        if state not in {"SUBMITTED", "UNKNOWN", "CAPTCHA_HOLD", "FAILED", "CANCELLED"}:
            raise ValueError("Unsupported submission attempt state")
        self.connection.execute("UPDATE application_attempts SET state=?,finished_at=?,message=? WHERE application_id=? AND state='SUBMITTING'", (state, datetime.now(timezone.utc).isoformat(), message[:300], application_id))
        self.connection.commit()

    def submission_attempt_is_active(self, application_id: int, attempt_id: int, *, package_hash: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM application_attempts WHERE id=? AND application_id=? AND state='SUBMITTING' AND package_hash=?",
            (attempt_id, application_id, package_hash),
        ).fetchone()
        return row is not None

    def cancel_unsubmitted_attempt(self, application_id: int, *, message: str) -> bool:
        """Release a reservation only when the adapter confirms no final click occurred."""
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            attempt = self.connection.execute(
                "SELECT id FROM application_attempts WHERE application_id=? AND state='SUBMITTING'",
                (application_id,),
            ).fetchone()
            application = self.connection.execute(
                "SELECT status,queue_state FROM applications WHERE id=?",
                (application_id,),
            ).fetchone()
            if not attempt or not application or application["status"] != "SUBMITTING" or application["queue_state"] != "SUBMITTING":
                self.connection.rollback()
                return False
            self.connection.execute(
                "UPDATE application_attempts SET state='CANCELLED',finished_at=?,message=? WHERE id=? AND state='SUBMITTING'",
                (now, message[:300], int(attempt["id"])),
            )
            self.connection.execute(
                "UPDATE applications SET status='QUEUED',queue_state='READY',preparation_token=NULL,updated_at=? WHERE id=?",
                (now, application_id),
            )
            self.connection.execute(
                "INSERT INTO application_timeline(application_id,status,note,created_at) VALUES (?,'QUEUED','Submission reservation cancelled before the final click',?)",
                (application_id, now),
            )
            self.log("application_submit_cancelled_preclick", str(application_id))
            self.connection.commit()
            return True
        except Exception:
            self.connection.rollback()
            raise

    def recover_interrupted_submissions(self) -> int:
        """Never replay a final-submit click whose process may have crashed."""
        attempts = self.connection.execute("SELECT id,application_id FROM application_attempts WHERE state='SUBMITTING'").fetchall()
        if not attempts:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        with self.connection:
            for attempt in attempts:
                application_id = int(attempt["application_id"])
                self.connection.execute("UPDATE application_attempts SET state='UNKNOWN',finished_at=?,message='Worker stopped during submission; automatic retry disabled' WHERE id=?", (now, int(attempt["id"])))
                self.connection.execute("UPDATE applications SET status='SUBMITTED_UNVERIFIED',queue_state='DO_NOT_RETRY',updated_at=? WHERE id=?", (now, application_id))
                self.connection.execute("INSERT INTO application_timeline(application_id,status,note,created_at) VALUES (?,'SUBMITTED_UNVERIFIED','Worker stopped during submission; reconcile before any retry',?)", (application_id, now))
        return len(attempts)

    def can_queue_or_submit(self, *, daily_limit: int) -> bool:
        return daily_limit > 0 and self.applications_today() < daily_limit

    def analytics(self) -> dict[str, float | int]:
        total = self.count("applications")
        statuses = {row[0]: int(row[1]) for row in self.connection.execute("SELECT status, COUNT(*) FROM applications GROUP BY status")}
        interviews = statuses.get("INTERVIEW", 0)
        responses = sum(statuses.get(status, 0) for status in ("APPLICATION_RECEIVED", "EMPLOYER_VIEWED", "INTERVIEW", "ASSESSMENT", "OFFER", "REJECTED"))
        return {"jobs_discovered": self.count("jobs"), "applications": total, "interviews": interviews, "offers": statuses.get("OFFER", 0), "rejections": statuses.get("REJECTED", 0), "interview_rate": round(interviews / total * 100, 2) if total else 0.0, "response_rate": round(responses / total * 100, 2) if total else 0.0}

    def add_answer(
        self,
        category: str,
        question: str,
        value: str,
        source: str,
        *,
        scope_type: str | None = None,
        scope_country: str = "",
        scope_employer: str = "",
        valid_until: str | None = None,
        source_ref: str = "manual_answer_bank",
    ) -> int:
        clean_question = question.strip()
        clean_value = value.strip()
        if category not in {"FACT", "PREFERENCE", "MOTIVATION"} or source not in {"USER_CONFIRMED", "AI_GENERATED"} or not clean_question or not clean_value:
            raise ValueError("Answer must have a supported category, source, question and value")
        question_id = question_id_for_label(clean_question)
        if not question_id:
            question_id = "custom:" + sha256(" ".join(clean_question.casefold().split()).encode()).hexdigest()[:20]
        metadata = question_metadata(question_id)
        if metadata and category != metadata.category:
            raise ValueError("Answer category does not match the stable question")
        requested_scope = scope_type or (metadata.scope_type if metadata else "GLOBAL")
        allowed_scopes = set(metadata.allowed_scope_types) if metadata and metadata.reusable else {"GLOBAL"}
        if requested_scope not in allowed_scopes:
            raise ValueError("This question cannot be reused with the selected scope")
        scope_type = requested_scope
        if metadata and valid_until and not metadata.supports_expiry:
            raise ValueError("This answer does not support a validity date")
        if scope_type == "COUNTRY" and scope_employer.strip():
            raise ValueError("Country-scoped answers cannot have an employer scope")
        if scope_type == "EMPLOYER" and scope_country.strip():
            raise ValueError("Employer-scoped answers cannot have a country scope")
        if scope_type == "GLOBAL" and (scope_country.strip() or scope_employer.strip()):
            raise ValueError("Global answers cannot have a country or employer scope")
        state = answer_state_for_value(clean_value) if source == "USER_CONFIRMED" else "DRAFT"
        if source == "USER_CONFIRMED" and metadata and not metadata.reusable:
            state = "NON_REUSABLE"
        elif source == "USER_CONFIRMED" and metadata is None:
            state = "NEEDS_RECONFIRMATION"
        elif source == "USER_CONFIRMED" and scope_type == "COUNTRY" and not scope_country.strip():
            state = "NEEDS_RECONFIRMATION"
        if valid_until:
            try:
                expiry_date = date.fromisoformat(valid_until)
            except ValueError as exc:
                raise ValueError("Answer expiry must be an ISO date") from exc
            if expiry_date < datetime.now(timezone.utc).date() and state == "CONFIRMED":
                state = "EXPIRED"
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.connection.execute(
            "INSERT INTO answer_bank(category,question,value,source,created_at,question_id,answer_state,value_type,sensitivity,scope_type,scope_country,scope_employer,valid_until,confirmed_at,source_ref) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                category,
                clean_question,
                clean_value,
                source,
                now,
                question_id,
                state,
                metadata.value_type if metadata else "text",
                metadata.sensitivity if metadata else "NORMAL",
                scope_type,
                scope_country.strip(),
                scope_employer.strip(),
                valid_until,
                now if source == "USER_CONFIRMED" else None,
                source_ref,
            ),
        )
        if state == "CONFIRMED":
            conflicting = self.connection.execute(
                "SELECT id FROM answer_bank WHERE id!=? AND question_id=? AND scope_type=? "
                "AND scope_country=? AND scope_employer=? AND (answer_state='CONFLICT' OR "
                "(answer_state='CONFIRMED' AND lower(trim(value))!=lower(trim(?)))) LIMIT 1",
                (
                    int(cursor.lastrowid),
                    question_id,
                    scope_type,
                    scope_country.strip(),
                    scope_employer.strip(),
                    clean_value,
                ),
            ).fetchone()
            if conflicting:
                conflict_ids = [
                    int(row["id"])
                    for row in self.connection.execute(
                        "SELECT id FROM answer_bank WHERE question_id=? AND scope_type=? AND "
                        "scope_country=? AND scope_employer=? AND answer_state IN ('CONFIRMED','CONFLICT')",
                        (question_id, scope_type, scope_country.strip(), scope_employer.strip()),
                    )
                ]
                self.connection.execute(
                    "UPDATE answer_bank SET answer_state='CONFLICT' WHERE question_id=? AND scope_type=? "
                    "AND scope_country=? AND scope_employer=? AND answer_state IN ('CONFIRMED','CONFLICT')",
                    (question_id, scope_type, scope_country.strip(), scope_employer.strip()),
                )
                self.connection.executemany(
                    "UPDATE facts SET confirmed=0,rejected=1 WHERE source_id=?",
                    [(f"answer_bank:{conflict_id}",) for conflict_id in conflict_ids],
                )
            else:
                if metadata and metadata.candidate_fact_type:
                    self._materialize_answer_facts(int(cursor.lastrowid), metadata.candidate_fact_type, clean_value)
        self.revoke_autopilot("Application answers changed")
        self.connection.commit()
        return int(cursor.lastrowid)

    def register_application_questions(
        self,
        application_id: int,
        *,
        form_signature: str,
        listing_hash: str,
        questions: list[dict[str, object]],
    ) -> int:
        """Persist only safe, required, application-local fields awaiting the candidate."""
        if not re.fullmatch(r"[0-9a-f]{64}", form_signature) or not re.fullmatch(r"[0-9a-f]{64}", listing_hash):
            raise ValueError("Application question context is invalid")
        if not self.application(application_id):
            raise ValueError("Application question target was not found")
        eligible: list[dict[str, object]] = []
        for question in questions:
            field_id = str(question.get("field_id", "")).strip()
            label = " ".join(str(question.get("label", "")).split())
            description = " ".join(str(question.get("description", "")).split())
            kind = str(question.get("kind", ""))
            risk = str(question.get("risk", ""))
            if not field_id or len(field_id) > 200 or not label or len(label) > 1000:
                continue
            if kind not in _APPLICATION_ANSWER_KINDS or risk not in {"LOW", "MEDIUM"} or not question.get("required"):
                continue
            options_raw = question.get("options", ())
            options = [str(option).strip() for option in options_raw if str(option).strip()] if isinstance(options_raw, (tuple, list)) else []
            if kind in {"select", "radio"} and (not options or len(options) > 100 or any(len(option) > 500 for option in options)):
                continue
            constraints_raw = question.get("constraints", {})
            constraints = {
                key: str(constraints_raw[key]).strip()[:512]
                for key in _APPLICATION_ANSWER_CONSTRAINTS
                if isinstance(constraints_raw, dict) and key in constraints_raw and str(constraints_raw[key]).strip()
            }
            eligible.append({
                "field_id": field_id, "label": label, "description": description[:2000],
                "kind": kind, "risk": risk, "options": options, "constraints": constraints,
            })
        now = datetime.now(timezone.utc).isoformat()
        inserted = 0
        with self.connection:
            # A question that was still unanswered on an older page/schema must not
            # remain actionable after the live form changes. Answered earlier steps
            # remain bound to their own signatures for multi-step forms.
            self.connection.execute(
                "UPDATE application_form_answers SET state='STALE' WHERE application_id=? "
                "AND state='PENDING' AND (form_signature!=? OR listing_hash!=?)",
                (application_id, form_signature, listing_hash),
            )
            if self.connection.execute(
                "SELECT 1 FROM application_form_answers WHERE application_id=? AND listing_hash!=? LIMIT 1",
                (application_id, listing_hash),
            ).fetchone():
                self.connection.execute(
                    "UPDATE application_form_answers SET state='STALE' WHERE application_id=? AND listing_hash!=?",
                    (application_id, listing_hash),
                )
            eligible_ids = [str(item["field_id"]) for item in eligible]
            if eligible_ids:
                placeholders = ",".join("?" for _ in eligible_ids)
                self.connection.execute(
                    f"UPDATE application_form_answers SET state='STALE' WHERE application_id=? AND form_signature=? AND listing_hash=? "
                    f"AND state='PENDING' AND field_id NOT IN ({placeholders})",
                    (application_id, form_signature, listing_hash, *eligible_ids),
                )
            else:
                self.connection.execute(
                    "UPDATE application_form_answers SET state='STALE' WHERE application_id=? AND form_signature=? AND listing_hash=? AND state='PENDING'",
                    (application_id, form_signature, listing_hash),
                )
            for question in eligible:
                field_id = str(question["field_id"])
                label = str(question["label"])
                description = str(question["description"])
                kind = str(question["kind"])
                risk = str(question["risk"])
                options = question["options"]
                constraints = question["constraints"]
                self.connection.execute(
                    "INSERT INTO application_form_answers(application_id,field_id,form_signature,listing_hash,label,description,kind,options_json,constraints_json,required,risk,state,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,1,?,'PENDING',?) "
                    "ON CONFLICT(application_id,form_signature,listing_hash,field_id) DO UPDATE SET "
                    "label=excluded.label,description=excluded.description,kind=excluded.kind,options_json=excluded.options_json,constraints_json=excluded.constraints_json,risk=excluded.risk "
                    "WHERE application_form_answers.state='PENDING'",
                    (application_id, field_id, form_signature, listing_hash, label, description[:2000], kind, json.dumps(options, ensure_ascii=False), json.dumps(constraints, ensure_ascii=False), risk, now),
                )
                inserted += int(self.connection.execute("SELECT changes()").fetchone()[0] > 0)
        return inserted

    def application_questions(self, application_id: int | None = None) -> list[dict[str, object]]:
        if application_id is None:
            rows = self.connection.execute(
                "SELECT q.*, a.status AS application_status, a.queue_state, j.title AS job_title, j.company AS employer "
                "FROM application_form_answers q JOIN applications a ON a.id=q.application_id JOIN jobs j ON j.id=a.job_id "
                "WHERE q.state IN ('PENDING','ANSWERED') AND a.status IN ('NEEDS_USER','QUEUED') "
                "AND a.queue_state IN ('WAITING_USER','READY') ORDER BY q.created_at,q.id"
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT q.*, a.status AS application_status, a.queue_state, j.title AS job_title, j.company AS employer "
                "FROM application_form_answers q JOIN applications a ON a.id=q.application_id JOIN jobs j ON j.id=a.job_id "
                "WHERE q.application_id=? AND q.state IN ('PENDING','ANSWERED') "
                "AND a.status IN ('NEEDS_USER','QUEUED') AND a.queue_state IN ('WAITING_USER','READY') "
                "ORDER BY q.created_at,q.id",
                (application_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["options"] = json.loads(str(item.pop("options_json", "[]")))
            except (TypeError, ValueError):
                item["options"] = []
            try:
                item["constraints"] = json.loads(str(item.pop("constraints_json", "{}")))
            except (TypeError, ValueError):
                item["constraints"] = {}
            result.append(item)
        return result

    def answer_application_question(self, application_id: int, question_id: int, value: str, *, confirmed: bool) -> None:
        raw_value = value if isinstance(value, str) else ""
        if confirmed is not True or not raw_value.strip() or len(raw_value) > 4000:
            raise ValueError("A confirmed, non-empty application answer is required")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT q.*,a.status AS application_status,a.queue_state FROM application_form_answers q "
                "JOIN applications a ON a.id=q.application_id WHERE q.id=? AND q.application_id=?",
                (question_id, application_id),
            ).fetchone()
            if not row or row["state"] not in {"PENDING", "ANSWERED"}:
                raise ValueError("Application question is no longer current")
            if row["application_status"] not in {"NEEDS_USER", "QUEUED"} or row["queue_state"] not in {"WAITING_USER", "READY"}:
                raise ValueError("Application is already being processed or is no longer editable")
            clean_value = raw_value.strip() if row["kind"] == "textarea" else " ".join(raw_value.split())
            if not clean_value:
                raise ValueError("A non-empty application answer is required")
            if any(secret in clean_value.casefold() for secret in _APPLICATION_ANSWER_SECRETS):
                raise ValueError("Credential-like values cannot be stored as application answers")
            if row["kind"] in {"select", "radio"}:
                options = json.loads(str(row["options_json"]))
                if clean_value not in options:
                    raise ValueError("Choose one of the saved employer form options")
            try:
                constraints = json.loads(str(row["constraints_json"]))
            except (TypeError, ValueError) as error:
                raise ValueError("Employer form constraints are invalid") from error
            if not isinstance(constraints, dict):
                raise ValueError("Employer form constraints are invalid")
            if row["kind"] in {"text", "textarea", "email", "tel"}:
                for key, comparator in (("minlength", lambda size, limit: size >= limit), ("maxlength", lambda size, limit: size <= limit)):
                    if key in constraints:
                        try:
                            limit = int(constraints[key])
                        except (TypeError, ValueError) as error:
                            raise ValueError("Employer text-length constraint is invalid") from error
                        if limit < 0 or not comparator(len(clean_value), limit):
                            raise ValueError("Answer does not satisfy the employer's text-length requirement")
            if row["kind"] == "date":
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", clean_value):
                    raise ValueError("Enter a date in YYYY-MM-DD format")
                try:
                    parsed_date = date.fromisoformat(clean_value)
                except ValueError as error:
                    raise ValueError("Enter a valid YYYY-MM-DD date") from error
                for key, comparator in (("min", lambda actual, limit: actual >= limit), ("max", lambda actual, limit: actual <= limit)):
                    if key in constraints:
                        try:
                            limit = str(date.fromisoformat(str(constraints[key])))
                        except ValueError as error:
                            raise ValueError("Employer date constraint is invalid") from error
                        if not comparator(clean_value, limit):
                            raise ValueError("Date does not satisfy the employer's allowed range")
                step_raw = str(constraints.get("step", "1"))
                if step_raw != "any":
                    try:
                        step = Decimal(step_raw)
                    except InvalidOperation as error:
                        raise ValueError("Employer date step is invalid") from error
                    base_raw = str(constraints.get("min", constraints.get("step_base", "1970-01-01")))
                    try:
                        base_date = date.fromisoformat(base_raw)
                    except ValueError as error:
                        raise ValueError("Employer date step base is invalid") from error
                    try:
                        step_mismatch = Decimal((parsed_date - base_date).days) % step != 0
                    except DecimalException as error:
                        raise ValueError("Employer date step could not be validated") from error
                    if not step.is_finite() or step <= 0 or step_mismatch:
                        raise ValueError("Date does not satisfy the employer's date-step requirement")
            if row["kind"] == "email":
                parsed_email = parseaddr(clean_value)[1]
                local_part, separator, domain = clean_value.rpartition("@")
                if (
                    parsed_email != clean_value or not separator or clean_value.count("@") != 1
                    or not local_part or not domain or local_part.startswith(".") or local_part.endswith(".")
                    or domain.startswith(".") or domain.endswith(".") or ".." in domain
                    or ".." in local_part or len(clean_value) > 254
                    or not _APPLICATION_EMAIL.fullmatch(clean_value)
                ):
                    raise ValueError("Enter a valid email address")
            if row["kind"] == "number":
                if not _APPLICATION_NUMBER.fullmatch(clean_value):
                    raise ValueError("Enter a valid number")
                try:
                    number = Decimal(clean_value)
                    if not number.is_finite():
                        raise ValueError("Enter a finite number")
                except InvalidOperation as error:
                    raise ValueError("Enter a valid number") from error
                for key, comparator in (("min", lambda actual, limit: actual >= limit), ("max", lambda actual, limit: actual <= limit)):
                    if key in constraints:
                        try:
                            limit = Decimal(str(constraints[key]))
                        except InvalidOperation as error:
                            raise ValueError("Employer numeric constraint is invalid") from error
                        try:
                            in_range = comparator(number, limit)
                        except DecimalException as error:
                            raise ValueError("Employer numeric range could not be validated") from error
                        if not limit.is_finite() or not in_range:
                            raise ValueError("Number does not satisfy the employer's allowed range")
                step_raw = str(constraints.get("step", "1"))
                if step_raw != "any":
                    try:
                        step = Decimal(step_raw)
                        base = Decimal(str(constraints.get("min", constraints.get("step_base", "0"))))
                    except InvalidOperation as error:
                        raise ValueError("Employer numeric step is invalid") from error
                    try:
                        step_mismatch = (number - base) % step != 0
                    except DecimalException as error:
                        raise ValueError("Employer numeric step could not be validated") from error
                    if not step.is_finite() or step <= 0 or not base.is_finite() or step_mismatch:
                        raise ValueError("Number does not satisfy the employer's step requirement")
            now = datetime.now(timezone.utc).isoformat()
            self.connection.execute(
                "UPDATE application_form_answers SET state='ANSWERED',answer_value=?,answered_at=? WHERE id=?",
                (clean_value, now, question_id),
            )
            remaining = int(self.connection.execute(
                "SELECT COUNT(*) FROM application_form_answers WHERE application_id=? AND state='PENDING'",
                (application_id,),
            ).fetchone()[0])
            if remaining == 0 and row["application_status"] == "NEEDS_USER":
                self.connection.execute(
                    "UPDATE applications SET status='QUEUED',queue_state='READY',notes='Candidate supplied application-specific required answers; the worker will recheck the live form.',updated_at=? WHERE id=?",
                    (now, application_id),
                )
                self.connection.execute(
                    "INSERT INTO application_timeline(application_id,status,note,created_at) VALUES (?,'QUEUED','Candidate supplied required application-specific answers; worker will recheck the live form',?)",
                    (application_id, now),
                )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def application_form_answers(self, application_id: int, *, form_signature: str, listing_hash: str) -> dict[str, str]:
        rows = self.connection.execute(
            "SELECT field_id,answer_value FROM application_form_answers WHERE application_id=? "
            "AND form_signature=? AND listing_hash=? AND state='ANSWERED' ORDER BY id",
            (application_id, form_signature, listing_hash),
        ).fetchall()
        return {str(row["field_id"]): str(row["answer_value"]) for row in rows}

    def application_context_fingerprint(self, application_id: int) -> str:
        rows = self.connection.execute(
            "SELECT field_id,form_signature,listing_hash,state,answer_value FROM application_form_answers "
            "WHERE application_id=? AND state='ANSWERED' ORDER BY id",
            (application_id,),
        ).fetchall()
        context = {
            "scope": self.automation_scope_fingerprint(),
            "application_answers": [tuple(row) for row in rows],
        }
        return sha256(json.dumps(context, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    def confirm_onboarding_answers(self, answers: list[AnswerConfirmation]) -> int:
        """Version explicitly reviewed answers and retain their exact scope and freshness."""
        for answer in answers:
            metadata = question_metadata(answer.question_id)
            if (
                not answer.question.strip()
                or not answer.value.strip()
                or metadata is None
                or not metadata.reusable
                or answer.category != metadata.category
                or answer.answer_state != answer_state_for_value(answer.value)
                or answer.value_type != metadata.value_type
                or answer.sensitivity != metadata.sensitivity
                or answer.scope_type != metadata.scope_type
                or (metadata.scope_type == "COUNTRY" and not answer.scope_country.strip())
                or (metadata.scope_type != "COUNTRY" and answer.scope_country.strip())
                or (metadata.scope_type != "EMPLOYER" and answer.scope_employer.strip())
                or (answer.valid_until and not metadata.supports_expiry)
            ):
                raise ValueError("Only non-empty supported answers with valid scope can be confirmed")
            if answer.valid_until:
                try:
                    date.fromisoformat(answer.valid_until)
                except ValueError as exc:
                    raise ValueError("Answer expiry must be an ISO date") from exc
        if not answers:
            return 0

        now = datetime.now(timezone.utc).isoformat()
        with self.connection:
            for answer in answers:
                state = answer_state_for_value(answer.value)
                if state not in ANSWER_STATES:
                    raise ValueError("Unsupported answer state")
                if answer.valid_until and date.fromisoformat(answer.valid_until) < datetime.now(timezone.utc).date() and state == "CONFIRMED":
                    state = "EXPIRED"
                if answer.scope_type == "COUNTRY":
                    prior_rows = self.connection.execute(
                        "SELECT id FROM answer_bank WHERE question_id=? AND answer_state!='SUPERSEDED' "
                        "AND ((scope_type=? AND scope_country=? AND scope_employer=?) OR "
                        "(scope_type='COUNTRY' AND scope_country='' AND answer_state='NEEDS_RECONFIRMATION'))",
                        (answer.question_id, answer.scope_type, answer.scope_country.strip(), answer.scope_employer.strip()),
                    ).fetchall()
                else:
                    prior_rows = self.connection.execute(
                        "SELECT id FROM answer_bank WHERE question_id=? AND scope_type=? AND scope_country=? "
                        "AND scope_employer=? AND answer_state!='SUPERSEDED'",
                        (answer.question_id, answer.scope_type, answer.scope_country.strip(), answer.scope_employer.strip()),
                    ).fetchall()
                prior_ids = [int(row["id"]) for row in prior_rows]
                self.connection.executemany(
                    "UPDATE facts SET confirmed=0,rejected=1 WHERE source_id=?",
                    [(f"answer_bank:{prior_id}",) for prior_id in prior_ids],
                )
                if prior_ids:
                    placeholders = ",".join("?" for _ in prior_ids)
                    self.connection.execute(
                        f"UPDATE answer_bank SET answer_state='SUPERSEDED' WHERE id IN ({placeholders})",
                        prior_ids,
                    )
                cursor = self.connection.execute(
                    "INSERT INTO answer_bank(category,question,value,source,created_at,question_id,answer_state,value_type,sensitivity,scope_type,scope_country,scope_employer,valid_until,confirmed_at,source_ref) "
                    "VALUES (?,?,?,'USER_CONFIRMED',?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        answer.category,
                        answer.question.strip(),
                        answer.value.strip(),
                        now,
                        answer.question_id,
                        state,
                        answer.value_type,
                        answer.sensitivity,
                        answer.scope_type,
                        answer.scope_country.strip(),
                        answer.scope_employer.strip(),
                        answer.valid_until,
                        now,
                        answer.source_ref,
                    ),
                )
                metadata = question_metadata(answer.question_id)
                if metadata and metadata.candidate_fact_type and state == "CONFIRMED":
                    self._materialize_answer_facts(int(cursor.lastrowid), metadata.candidate_fact_type, answer.value)
            self.revoke_autopilot("Questionnaire answers explicitly confirmed or updated")
            self.log("onboarding_answers_confirmed", f"{len(answers)} selected answers")
        return len(answers)

    def answer(self, answer_id: int) -> dict[str, object] | None:
        row = self.connection.execute("SELECT * FROM answer_bank WHERE id=?", (answer_id,)).fetchone()
        return dict(row) if row else None

    def answers(self) -> list[dict[str, object]]:
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM answer_bank WHERE answer_state!='SUPERSEDED' ORDER BY id DESC"
            )
        ]

    def update_answer(self, answer_id: int, category: str, question: str, value: str, source: str) -> None:
        self.connection.execute("UPDATE answer_bank SET category=?, question=?, value=?, source=? WHERE id=?", (category, question, value, source, answer_id))
        self.revoke_autopilot("Application answers changed")
        self.connection.commit()

    def add_submission_evidence(self, *, application_id: int, final_url: str, confirmation_message: str, confirmation_id: str | None, agent_provider: str) -> int:
        from sampoagent.applications.urls import is_safe_public_https_url

        combined = " ".join((final_url, confirmation_message, confirmation_id or "")).casefold()
        if any(marker in combined for marker in ("password", "passwd", "api key", "access token", "secret=")):
            raise ValueError("Submission evidence must not contain credentials")
        if not is_safe_public_https_url(final_url):
            raise ValueError("Submission evidence URL must be a safe public HTTPS destination")
        cursor = self.connection.execute("INSERT INTO submission_evidence(application_id, final_url, confirmation_message, confirmation_id, agent_provider, created_at) VALUES (?, ?, ?, ?, ?, ?)", (application_id, final_url, confirmation_message, confirmation_id, agent_provider, datetime.now(timezone.utc).isoformat()))
        self.connection.commit()
        return int(cursor.lastrowid)

    def submission_evidence(self, evidence_id: int) -> dict[str, object] | None:
        row = self.connection.execute("SELECT application_id, final_url, confirmation_message, confirmation_id, agent_provider, created_at FROM submission_evidence WHERE id=?", (evidence_id,)).fetchone()
        return dict(row) if row else None

    def submission_evidence_for_application(self, application_id: int) -> list[dict[str, object]]:
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT id, final_url, confirmation_message, confirmation_id, agent_provider, created_at FROM submission_evidence WHERE application_id=? ORDER BY id DESC",
                (application_id,),
            )
        ]

    def setting(self, key: str) -> str | None:
        row = self.connection.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_setting(self, key: str, value: str) -> None:
        self.connection.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        self.log("setting_changed", key)
        self.connection.commit()

    def save_preferences(self, preferences: dict[str, object]) -> None:
        self.set_setting("job_preferences", json.dumps(preferences, ensure_ascii=False))

    def preferences(self) -> dict[str, object]:
        raw = self.setting("job_preferences")
        return json.loads(raw) if raw else {}

    def cache_put(self, cache_key: str, value: str) -> None:
        self.connection.execute("INSERT INTO semantic_cache(cache_key, value, created_at) VALUES (?, ?, ?) ON CONFLICT(cache_key) DO UPDATE SET value=excluded.value, created_at=excluded.created_at", (cache_key, value, datetime.now(timezone.utc).isoformat()))
        self.connection.commit()

    def cache_get(self, cache_key: str) -> str | None:
        row = self.connection.execute("SELECT value FROM semantic_cache WHERE cache_key=?", (cache_key,)).fetchone()
        return row[0] if row else None

    def clear_cache(self) -> None:
        self.connection.execute("DELETE FROM semantic_cache")
        self.log("semantic_cache_cleared", "User requested cache reset")
        self.connection.commit()

    def record_ai_usage(
        self,
        *,
        provider: str,
        model: str,
        feature: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_tokens: int = 0,
    ) -> int:
        if any(value < 0 for value in (input_tokens, output_tokens, cached_tokens)):
            raise ValueError("AI token usage cannot be negative")
        cursor = self.connection.execute(
            "INSERT INTO ai_usage(provider, model, feature, input_tokens, output_tokens, cached_tokens, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (provider, model, feature, input_tokens, output_tokens, cached_tokens, datetime.now(timezone.utc).isoformat()),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def ai_usage_summary(self) -> dict[str, int]:
        row = self.connection.execute(
            "SELECT COUNT(*) AS requests, COALESCE(SUM(input_tokens), 0) AS input_tokens, COALESCE(SUM(output_tokens), 0) AS output_tokens, COALESCE(SUM(cached_tokens), 0) AS cached_tokens FROM ai_usage"
        ).fetchone()
        return {key: int(row[key]) for key in ("requests", "input_tokens", "output_tokens", "cached_tokens")}

    def add_document(self, *, kind: str, path: str, checksum: str | None = None) -> int:
        cursor = self.connection.execute(
            "INSERT INTO documents(kind, path, checksum, created_at) VALUES (?, ?, ?, ?)",
            (kind, path, checksum, datetime.now(timezone.utc).isoformat()),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def documents(self, *, kind: str | None = None) -> list[dict[str, object]]:
        if kind is None:
            rows = self.connection.execute("SELECT * FROM documents ORDER BY id DESC")
        else:
            rows = self.connection.execute("SELECT * FROM documents WHERE kind=? ORDER BY id DESC", (kind,))
        return [dict(row) for row in rows]

    @staticmethod
    def _validate_source_values(*, name: str, url: str, country: str, source_type: str, capability: str, listing_selector: str = "", terms_url: str = "", terms_reviewed: bool = False) -> None:
        parsed = urlsplit(url.strip())
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Source URL must be a credential-free HTTPS address")
        if not name.strip() or not country.strip() or not source_type.strip():
            raise ValueError("Source name, country, and type are required")
        if capability not in {"Browser search only", "RSS/Atom feed", "JSON Feed", "Job Market Finland API", "Scrapling public page"}:
            raise ValueError("Unsupported source capability")
        if len(listing_selector.strip()) > 200:
            raise ValueError("Job card selector must be 200 characters or fewer")
        clean_terms_url = terms_url.strip()
        if clean_terms_url:
            terms = urlsplit(clean_terms_url)
            if terms.scheme != "https" or not terms.hostname or terms.username or terms.password or terms.fragment:
                raise ValueError("Terms URL must be a credential-free public HTTPS address")
        if capability == "Scrapling public page" and terms_reviewed and not clean_terms_url:
            raise ValueError("A public HTTPS terms URL is required when recording terms review")
        if capability == "Job Market Finland API" and parsed.hostname not in {"tyomarkkinatori.fi", "www.tyomarkkinatori.fi"}:
            raise ValueError("The official Job Market Finland API source must use tyomarkkinatori.fi")

    def add_source(self, *, name: str, url: str, country: str, source_type: str, notes: str = "", capability: str = "Browser search only", listing_selector: str = "", terms_url: str = "", terms_reviewed: bool = False) -> int:
        clean_url = url.strip()
        clean_selector = listing_selector.strip()
        clean_terms_url = terms_url.strip()
        self._validate_source_values(name=name, url=clean_url, country=country, source_type=source_type, capability=capability, listing_selector=clean_selector, terms_url=clean_terms_url, terms_reviewed=terms_reviewed)
        if self.has_source_url(clean_url):
            raise ValueError("A source with this URL already exists")
        reviewed_at = datetime.now(timezone.utc).isoformat() if terms_reviewed else None
        cursor = self.connection.execute("INSERT INTO job_sources(name, url, country, source_type, notes, capability, listing_selector, terms_url, terms_reviewed, terms_reviewed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (name.strip(), clean_url, country.strip(), source_type.strip(), notes.strip(), capability, clean_selector, clean_terms_url, int(terms_reviewed), reviewed_at))
        self.log("source_added", name)
        self.connection.commit()
        return int(cursor.lastrowid)

    def update_source(self, source_id: int, *, name: str, url: str, country: str, source_type: str, notes: str, capability: str, listing_selector: str = "", terms_url: str = "", terms_reviewed: bool = False) -> None:
        clean_url = url.strip()
        clean_selector = listing_selector.strip()
        clean_terms_url = terms_url.strip()
        self._validate_source_values(name=name, url=clean_url, country=country, source_type=source_type, capability=capability, listing_selector=clean_selector, terms_url=clean_terms_url, terms_reviewed=terms_reviewed)
        previous = self.source(source_id)
        if not previous:
            return
        source_changed = str(previous.get("url") or "") != clean_url or str(previous.get("capability") or "") != capability
        reviewed_policy_changed = bool(previous.get("terms_reviewed")) and str(previous.get("terms_url") or "") != clean_terms_url
        reviewed = terms_reviewed and not source_changed and not reviewed_policy_changed
        reviewed_at = datetime.now(timezone.utc).isoformat() if reviewed else None
        duplicate = self.connection.execute("SELECT 1 FROM job_sources WHERE url=? AND id<>? LIMIT 1", (clean_url, source_id)).fetchone()
        if duplicate:
            raise ValueError("A source with this URL already exists")
        self.connection.execute("UPDATE job_sources SET name=?, url=?, country=?, source_type=?, notes=?, capability=?, listing_selector=?, terms_url=?, terms_reviewed=?, terms_reviewed_at=? WHERE id=?", (name.strip(), clean_url, country.strip(), source_type.strip(), notes.strip(), capability, clean_selector, clean_terms_url, int(reviewed), reviewed_at, source_id))
        self.log("source_updated", str(source_id))
        self.connection.commit()

    def delete_source(self, source_id: int) -> None:
        """Delete a source definition; imported jobs retain their source snapshots."""
        self.connection.execute("DELETE FROM job_sources WHERE id=?", (source_id,))
        self.log("source_deleted", str(source_id))
        self.connection.commit()

    def has_source_url(self, url: str) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM job_sources WHERE url=? LIMIT 1", (url.strip(),)
        ).fetchone() is not None

    def source(self, source_id: int) -> dict[str, object] | None:
        row = self.connection.execute("SELECT * FROM job_sources WHERE id=?", (source_id,)).fetchone()
        return dict(row) if row else None

    def set_source_enabled(self, source_id: int, enabled: bool) -> None:
        self.connection.execute("UPDATE job_sources SET enabled=? WHERE id=?", (int(enabled), source_id))
        self.log("source_enabled", f"{source_id}: {enabled}")
        self.connection.commit()

    def count(self, table: str) -> int:
        if table not in {"facts", "career_profiles", "job_sources", "jobs", "applications", "activity_log", "documents", "application_learning"}:
            raise ValueError("Unsupported table")
        return int(self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def rows(self, table: str) -> list[dict[str, object]]:
        if table not in {"facts", "career_profiles", "job_sources", "jobs", "applications", "activity_log"}:
            raise ValueError("Unsupported table")
        rows = [dict(row) for row in self.connection.execute(f"SELECT * FROM {table} ORDER BY id DESC")]
        return [self._safe_activity_row(row) for row in rows] if table == "activity_log" else rows

    def job(self, job_id: int) -> dict[str, object] | None:
        row = self.connection.execute(
            "SELECT jobs.*, (SELECT snapshot_hash FROM job_verifications WHERE job_id=jobs.id ORDER BY id DESC LIMIT 1) AS verified_snapshot_hash, "
            "COALESCE((SELECT country FROM job_sources WHERE id=jobs.source_id), '') AS country "
            "FROM jobs WHERE id=?",
            (job_id,),
        ).fetchone()
        return dict(row) if row else None

    def set_job_language(self, job_id: int, language: str) -> None:
        if language not in {"fi", "en"}:
            raise ValueError("Unsupported job language")
        self.connection.execute("UPDATE jobs SET language=? WHERE id=?", (language, job_id))
        self.log("job_language_override", f"{job_id}: {language}")
        self.connection.commit()

    def application(self, application_id: int) -> dict[str, object] | None:
        row = self.connection.execute("SELECT * FROM applications WHERE id=?", (application_id,)).fetchone()
        return dict(row) if row else None

    def log(self, action: str, details: str) -> None:
        """Record an allowlisted event code without caller-controlled detail text."""
        safe_action = action if isinstance(action, str) and action in _SAFE_ACTIVITY_ACTIONS else "activity"
        self.connection.execute(
            "INSERT INTO activity_log(action, details, created_at) VALUES (?, ?, ?)",
            (safe_action, _SAFE_ACTIVITY_DETAIL, datetime.now(timezone.utc).isoformat()),
        )

    def activity_details_needing_redaction(self) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) FROM activity_log WHERE details != ?", (_SAFE_ACTIVITY_DETAIL,)
        ).fetchone()
        return int(row[0])

    def redact_activity_details(self) -> int:
        """Replace legacy free-form activity details, preserving event codes and timestamps."""
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = self.connection.execute(
                "UPDATE activity_log SET details=? WHERE details != ?",
                (_SAFE_ACTIVITY_DETAIL, _SAFE_ACTIVITY_DETAIL),
            )
            redacted_count = cursor.rowcount
            if redacted_count:
                self.log("activity_details_redacted", str(redacted_count))
            self.connection.commit()
            return redacted_count
        except Exception:
            self.connection.rollback()
            raise

    @staticmethod
    def _safe_activity_row(row: dict[str, object]) -> dict[str, object]:
        safe = dict(row)
        action = safe.get("action")
        safe["action"] = action if isinstance(action, str) and action in _SAFE_ACTIVITY_ACTIONS else "activity"
        safe["details"] = _SAFE_ACTIVITY_DETAIL
        return safe

    def recent_activity(self, limit: int = 20) -> list[dict[str, object]]:
        rows = [dict(row) for row in self.connection.execute(
            "SELECT action, details, created_at FROM activity_log ORDER BY id DESC LIMIT ?", (limit,)
        )]
        return [self._safe_activity_row(row) for row in rows]
