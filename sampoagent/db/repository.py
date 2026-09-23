"""Small SQLite repository. Candidate data never leaves the local device."""

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import json
from urllib.parse import urlsplit


class Repository:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row

    def initialize(self) -> None:
        self.connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS candidate_profile (id INTEGER PRIMARY KEY CHECK(id=1), name TEXT NOT NULL, email TEXT, locale TEXT NOT NULL DEFAULT 'en');
            CREATE TABLE IF NOT EXISTS facts (id INTEGER PRIMARY KEY, type TEXT NOT NULL, value TEXT NOT NULL, provenance TEXT NOT NULL, source_id TEXT, confidence REAL NOT NULL, confirmed INTEGER NOT NULL DEFAULT 0, rejected INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS candidate_records (id INTEGER PRIMARY KEY, record_type TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS cv_templates (id INTEGER PRIMARY KEY, name TEXT NOT NULL, language TEXT NOT NULL, role_family TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS career_profiles (id INTEGER PRIMARY KEY, name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, notes TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS target_occupations (id INTEGER PRIMARY KEY, title_en TEXT NOT NULL, title_fi TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, UNIQUE(title_en, title_fi));
            CREATE TABLE IF NOT EXISTS job_sources (id INTEGER PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL, country TEXT NOT NULL, source_type TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1, capability TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY, title TEXT NOT NULL, company TEXT NOT NULL, location TEXT, language TEXT NOT NULL, description TEXT NOT NULL, application_url TEXT NOT NULL, fingerprint TEXT UNIQUE NOT NULL, verification_state TEXT NOT NULL, deadline TEXT, source_id INTEGER, source_name TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS job_overrides (job_id INTEGER PRIMARY KEY, decision TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, FOREIGN KEY(job_id) REFERENCES jobs(id));
            CREATE TABLE IF NOT EXISTS applications (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL, status TEXT NOT NULL, queue_state TEXT NOT NULL, language TEXT NOT NULL, cv_path TEXT, notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, FOREIGN KEY(job_id) REFERENCES jobs(id));
            CREATE TABLE IF NOT EXISTS submission_evidence (id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL, final_url TEXT NOT NULL, confirmation_message TEXT NOT NULL, confirmation_id TEXT, agent_provider TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS answer_bank (id INTEGER PRIMARY KEY, category TEXT NOT NULL, question TEXT NOT NULL, value TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS application_timeline (id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL, status TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY(application_id) REFERENCES applications(id));
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS activity_log (id INTEGER PRIMARY KEY, action TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS semantic_cache (cache_key TEXT PRIMARY KEY, value TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ai_usage (id INTEGER PRIMARY KEY, provider TEXT NOT NULL, model TEXT NOT NULL, feature TEXT NOT NULL, input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0, cached_tokens INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS documents (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, path TEXT NOT NULL, checksum TEXT, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS discovery_runs (id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL, query_count INTEGER NOT NULL DEFAULT 0, jobs_found INTEGER NOT NULL DEFAULT 0, imported_count INTEGER NOT NULL DEFAULT 0, duplicates_count INTEGER NOT NULL DEFAULT 0, summary TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS discovery_source_results (id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, source_id INTEGER, source_name TEXT NOT NULL, source_url TEXT NOT NULL, capability TEXT NOT NULL, status TEXT NOT NULL, jobs_found INTEGER NOT NULL DEFAULT 0, imported_count INTEGER NOT NULL DEFAULT 0, duplicates_count INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, FOREIGN KEY(run_id) REFERENCES discovery_runs(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS mailbox_connection (id INTEGER PRIMARY KEY CHECK(id=1), provider TEXT NOT NULL, token_ciphertext TEXT NOT NULL, connected_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS mailbox_messages (id INTEGER PRIMARY KEY, provider TEXT NOT NULL, provider_message_id TEXT NOT NULL, sender TEXT NOT NULL, subject TEXT NOT NULL, snippet TEXT NOT NULL, received_at TEXT NOT NULL, link TEXT NOT NULL, reviewed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, UNIQUE(provider, provider_message_id));
            """
        )
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(job_sources)")}
        if "notes" not in columns:
            self.connection.execute("ALTER TABLE job_sources ADD COLUMN notes TEXT NOT NULL DEFAULT ''")
        if "capability" not in columns:
            self.connection.execute("ALTER TABLE job_sources ADD COLUMN capability TEXT NOT NULL DEFAULT 'Browser search only'")
        if "listing_selector" not in columns:
            self.connection.execute("ALTER TABLE job_sources ADD COLUMN listing_selector TEXT NOT NULL DEFAULT ''")
        job_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(jobs)")}
        for name, declaration in (
            ("source_id", "INTEGER"),
            ("source_name", "TEXT NOT NULL DEFAULT ''"),
            ("source_url", "TEXT NOT NULL DEFAULT ''"),
        ):
            if name not in job_columns:
                self.connection.execute(f"ALTER TABLE jobs ADD COLUMN {name} {declaration}")
        for key, value in {
            "application_mode": "review_everything",
            "daily_limit": "0",
            "ai_usage_mode": "minimal",
            "dry_run": "true",
        }.items():
            self.connection.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, value)
            )
        self.migrate_unambiguous_candidate_fact_links()
        self.seed_builtin_sources()
        self.connection.commit()

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
        jobs = [("Warehouse Worker", "Northern Logistics Oy", "Vantaa", "en", "Synthetic demo: forklift operation and Finnish required.", "https://example.test/apply/warehouse", "demo-warehouse", "VERIFIED"), ("Asiakaspalvelija", "Example Services Oy", "Helsinki", "fi", "Synteettinen demo: asiakaspalvelu ja englanti.", "https://example.test/apply/service", "demo-service", "PARTIALLY_VERIFIED")]
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
        self.set_setting("onboarding_complete", "true")
        self.connection.commit()

    def confirmed_skills(self) -> list[str]:
        return [row[0] for row in self.connection.execute("SELECT value FROM facts WHERE type='skill' AND confirmed=1 AND rejected=0 ORDER BY value")]

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
        cursor = self.connection.execute("INSERT INTO candidate_records(record_type, payload, created_at) VALUES (?, ?, ?)", (record_type, json.dumps(payload, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))
        self.log("candidate_record_added", record_type)
        self.connection.commit()
        return int(cursor.lastrowid)

    def candidate_records(self, record_type: str) -> list[dict[str, str]]:
        rows = self.connection.execute("SELECT payload FROM candidate_records WHERE record_type=? ORDER BY id DESC", (record_type,))
        return [json.loads(row[0]) for row in rows]

    def candidate_record_rows(self, record_type: str) -> list[dict[str, object]]:
        rows = self.connection.execute("SELECT id, payload FROM candidate_records WHERE record_type=? ORDER BY id DESC", (record_type,))
        return [{"id": int(row[0]), **json.loads(row[1])} for row in rows]

    def update_candidate_record(self, record_id: int, *, title: str, details: str) -> None:
        row = self.connection.execute("SELECT record_type, payload FROM candidate_records WHERE id=?", (record_id,)).fetchone()
        if not row or not title.strip():
            raise ValueError("Candidate record was not found or the title is empty")
        payload = json.loads(row[1])
        old_title = str(payload.get("title", ""))
        payload.update({"title": title.strip(), "details": details.strip()})
        self.connection.execute("UPDATE candidate_records SET payload=? WHERE id=?", (json.dumps(payload, ensure_ascii=False), record_id))
        source = f"candidate_record:{record_id}"
        fact = self.connection.execute("SELECT id FROM facts WHERE type=? AND value=? AND provenance='USER_CONFIRMED' AND source_id=? ORDER BY id DESC LIMIT 1", (row[0], old_title, source)).fetchone()
        if fact:
            self.connection.execute("UPDATE facts SET value=? WHERE id=?", (title.strip(), int(fact[0])))
        self.log("candidate_record_updated", str(record_id))
        self.connection.commit()

    def delete_candidate_record(self, record_id: int) -> None:
        row = self.connection.execute("SELECT record_type, payload FROM candidate_records WHERE id=?", (record_id,)).fetchone()
        if row:
            value = str(json.loads(row[1]).get("title", ""))
            self.connection.execute("DELETE FROM facts WHERE type=? AND provenance='USER_CONFIRMED' AND source_id=?", (row[0], f"candidate_record:{record_id}"))
            self.connection.execute("DELETE FROM candidate_records WHERE id=?", (record_id,))
        self.log("candidate_record_deleted", str(record_id))
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

    def add_extracted_fact(self, *, fact_type: str, value: str, source_id: str, confidence: float) -> int:
        cursor = self.connection.execute("INSERT INTO facts(type, value, provenance, source_id, confidence, confirmed) VALUES (?, ?, 'CV_EXTRACTED', ?, ?, 0)", (fact_type, value, source_id, confidence))
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
                datetime.now(timezone.utc).isoformat(),
            ),
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
        if status not in {"completed", "partial", "failed"}:
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

    def mailbox_connection(self) -> dict[str, object] | None:
        row = self.connection.execute("SELECT id, provider, connected_at FROM mailbox_connection WHERE id=1").fetchone()
        return dict(row) if row else None

    def mailbox_ciphertext(self) -> str | None:
        row = self.connection.execute("SELECT token_ciphertext FROM mailbox_connection WHERE id=1").fetchone()
        return str(row[0]) if row else None

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

    def queue_application(self, job_id: int, *, language: str, cv_path: str | None) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.connection.execute("INSERT INTO applications(job_id, status, queue_state, language, cv_path, created_at, updated_at) VALUES (?, 'QUEUED', 'READY', ?, ?, ?, ?)", (job_id, language, cv_path, now, now))
        application_id = int(cursor.lastrowid)
        self.connection.execute("INSERT INTO application_timeline(application_id, status, note, created_at) VALUES (?, 'QUEUED', 'Application added to queue', ?)", (application_id, now))
        self.log("queue_created", str(application_id))
        self.connection.commit()
        return application_id

    def has_application_for_job(self, job_id: int) -> bool:
        """Prevent a job from being prepared or submitted more than once."""
        return self.connection.execute(
            "SELECT 1 FROM applications WHERE job_id=? LIMIT 1", (job_id,)
        ).fetchone() is not None

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

    def update_application_status(self, application_id: int, status: str, note: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            "UPDATE applications SET status=?, notes=CASE WHEN ? <> '' THEN ? ELSE notes END, updated_at=? WHERE id=?",
            (status, note, note, now, application_id),
        )
        self.connection.execute("INSERT INTO application_timeline(application_id, status, note, created_at) VALUES (?, ?, ?, ?)", (application_id, status, note, now))
        self.log("application_status_changed", f"{application_id}: {status}")
        self.connection.commit()

    def application_timeline(self, application_id: int) -> list[dict[str, object]]:
        return [dict(row) for row in self.connection.execute("SELECT status, note, created_at FROM application_timeline WHERE application_id=? ORDER BY id", (application_id,))]

    def applications_today(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM applications WHERE status='APPLIED' AND substr(updated_at, 1, 10)=?", (datetime.now(timezone.utc).date().isoformat(),)).fetchone()[0])

    def can_queue_or_submit(self, *, daily_limit: int) -> bool:
        return daily_limit > 0 and self.applications_today() < daily_limit

    def analytics(self) -> dict[str, float | int]:
        total = self.count("applications")
        statuses = {row[0]: int(row[1]) for row in self.connection.execute("SELECT status, COUNT(*) FROM applications GROUP BY status")}
        interviews = statuses.get("INTERVIEW", 0)
        responses = sum(statuses.get(status, 0) for status in ("APPLICATION_RECEIVED", "EMPLOYER_VIEWED", "INTERVIEW", "ASSESSMENT", "OFFER", "REJECTED"))
        return {"jobs_discovered": self.count("jobs"), "applications": total, "interviews": interviews, "offers": statuses.get("OFFER", 0), "rejections": statuses.get("REJECTED", 0), "interview_rate": round(interviews / total * 100, 2) if total else 0.0, "response_rate": round(responses / total * 100, 2) if total else 0.0}

    def add_answer(self, category: str, question: str, value: str, source: str) -> int:
        cursor = self.connection.execute("INSERT INTO answer_bank(category, question, value, source, created_at) VALUES (?, ?, ?, ?, ?)", (category, question, value, source, datetime.now(timezone.utc).isoformat()))
        self.connection.commit()
        return int(cursor.lastrowid)

    def answer(self, answer_id: int) -> dict[str, object] | None:
        row = self.connection.execute("SELECT * FROM answer_bank WHERE id=?", (answer_id,)).fetchone()
        return dict(row) if row else None

    def answers(self) -> list[dict[str, object]]:
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM answer_bank ORDER BY id DESC"
            )
        ]

    def update_answer(self, answer_id: int, category: str, question: str, value: str, source: str) -> None:
        self.connection.execute("UPDATE answer_bank SET category=?, question=?, value=?, source=? WHERE id=?", (category, question, value, source, answer_id))
        self.connection.commit()

    def add_submission_evidence(self, *, application_id: int, final_url: str, confirmation_message: str, confirmation_id: str | None, agent_provider: str) -> int:
        combined = " ".join((final_url, confirmation_message, confirmation_id or "")).casefold()
        if any(marker in combined for marker in ("password", "passwd", "api key", "access token", "secret=")):
            raise ValueError("Submission evidence must not contain credentials")
        if not final_url.startswith(("https://", "http://")):
            raise ValueError("Submission evidence URL must be HTTP(S)")
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
    def _validate_source_values(*, name: str, url: str, country: str, source_type: str, capability: str, listing_selector: str = "") -> None:
        parsed = urlsplit(url.strip())
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Source URL must be a credential-free HTTPS address")
        if not name.strip() or not country.strip() or not source_type.strip():
            raise ValueError("Source name, country, and type are required")
        if capability not in {"Browser search only", "RSS/Atom feed", "JSON Feed", "Job Market Finland API", "Scrapling public page"}:
            raise ValueError("Unsupported source capability")
        if len(listing_selector.strip()) > 200:
            raise ValueError("Job card selector must be 200 characters or fewer")
        if capability == "Job Market Finland API" and parsed.hostname not in {"tyomarkkinatori.fi", "www.tyomarkkinatori.fi"}:
            raise ValueError("The official Job Market Finland API source must use tyomarkkinatori.fi")

    def add_source(self, *, name: str, url: str, country: str, source_type: str, notes: str = "", capability: str = "Browser search only", listing_selector: str = "") -> int:
        clean_url = url.strip()
        clean_selector = listing_selector.strip()
        self._validate_source_values(name=name, url=clean_url, country=country, source_type=source_type, capability=capability, listing_selector=clean_selector)
        if self.has_source_url(clean_url):
            raise ValueError("A source with this URL already exists")
        cursor = self.connection.execute("INSERT INTO job_sources(name, url, country, source_type, notes, capability, listing_selector) VALUES (?, ?, ?, ?, ?, ?, ?)", (name.strip(), clean_url, country.strip(), source_type.strip(), notes.strip(), capability, clean_selector))
        self.log("source_added", name)
        self.connection.commit()
        return int(cursor.lastrowid)

    def update_source(self, source_id: int, *, name: str, url: str, country: str, source_type: str, notes: str, capability: str, listing_selector: str = "") -> None:
        clean_url = url.strip()
        clean_selector = listing_selector.strip()
        self._validate_source_values(name=name, url=clean_url, country=country, source_type=source_type, capability=capability, listing_selector=clean_selector)
        duplicate = self.connection.execute("SELECT 1 FROM job_sources WHERE url=? AND id<>? LIMIT 1", (clean_url, source_id)).fetchone()
        if duplicate:
            raise ValueError("A source with this URL already exists")
        self.connection.execute("UPDATE job_sources SET name=?, url=?, country=?, source_type=?, notes=?, capability=?, listing_selector=? WHERE id=?", (name.strip(), clean_url, country.strip(), source_type.strip(), notes.strip(), capability, clean_selector, source_id))
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
        if table not in {"facts", "career_profiles", "job_sources", "jobs", "applications", "activity_log", "documents"}:
            raise ValueError("Unsupported table")
        return int(self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def rows(self, table: str) -> list[dict[str, object]]:
        if table not in {"facts", "career_profiles", "job_sources", "jobs", "applications", "activity_log"}:
            raise ValueError("Unsupported table")
        return [dict(row) for row in self.connection.execute(f"SELECT * FROM {table} ORDER BY id DESC")]

    def job(self, job_id: int) -> dict[str, object] | None:
        row = self.connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
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
        self.connection.execute("INSERT INTO activity_log(action, details, created_at) VALUES (?, ?, ?)", (action, details, datetime.now(timezone.utc).isoformat()))

    def recent_activity(self, limit: int = 20) -> list[dict[str, object]]:
        return [dict(row) for row in self.connection.execute("SELECT action, details, created_at FROM activity_log ORDER BY id DESC LIMIT ?", (limit,))]
