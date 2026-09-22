"""Small SQLite repository. Candidate data never leaves the local device."""

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import json


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
            CREATE TABLE IF NOT EXISTS career_profiles (id INTEGER PRIMARY KEY, name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, notes TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS job_sources (id INTEGER PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL, country TEXT NOT NULL, source_type TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1, capability TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY, title TEXT NOT NULL, company TEXT NOT NULL, location TEXT, language TEXT NOT NULL, description TEXT NOT NULL, application_url TEXT NOT NULL, fingerprint TEXT UNIQUE NOT NULL, verification_state TEXT NOT NULL, deadline TEXT);
            CREATE TABLE IF NOT EXISTS job_overrides (job_id INTEGER PRIMARY KEY, decision TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, FOREIGN KEY(job_id) REFERENCES jobs(id));
            CREATE TABLE IF NOT EXISTS applications (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL, status TEXT NOT NULL, queue_state TEXT NOT NULL, language TEXT NOT NULL, cv_path TEXT, notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, FOREIGN KEY(job_id) REFERENCES jobs(id));
            CREATE TABLE IF NOT EXISTS submission_evidence (id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL, final_url TEXT NOT NULL, confirmation_message TEXT NOT NULL, confirmation_id TEXT, agent_provider TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS answer_bank (id INTEGER PRIMARY KEY, category TEXT NOT NULL, question TEXT NOT NULL, value TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS application_timeline (id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL, status TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY(application_id) REFERENCES applications(id));
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS activity_log (id INTEGER PRIMARY KEY, action TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS semantic_cache (cache_key TEXT PRIMARY KEY, value TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS documents (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, path TEXT NOT NULL, checksum TEXT, created_at TEXT NOT NULL);
            """
        )
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(job_sources)")}
        if "notes" not in columns:
            self.connection.execute("ALTER TABLE job_sources ADD COLUMN notes TEXT NOT NULL DEFAULT ''")
        self.connection.commit()

    def load_demo(self) -> None:
        if self.profile():
            return
        self.connection.execute("INSERT INTO candidate_profile(id, name, email, locale) VALUES(1, ?, ?, ?)", ("Aino Example", "aino@example.test", "en"))
        self.connection.executemany("INSERT INTO facts(type, value, provenance, source_id, confidence, confirmed) VALUES (?, ?, 'USER_CONFIRMED', 'demo', 1, 1)", [("skill", "forklift operation"), ("skill", "customer service"), ("language", "Finnish"), ("language", "English")])
        self.connection.executemany("INSERT INTO career_profiles(name, notes) VALUES (?, ?)", [("Logistics", "Synthetic demo career profile"), ("Customer Service", "Synthetic demo career profile")])
        sources = [("Duunitori", "https://duunitori.fi", "Finland", "job board", "Browser search only"), ("Työmarkkinatori", "https://tyomarkkinatori.fi", "Finland", "public-sector board", "Browser search only"), ("Kuntarekry", "https://kuntarekry.fi", "Finland", "public-sector board", "Browser search only")]
        self.connection.executemany("INSERT INTO job_sources(name, url, country, source_type, capability) VALUES (?, ?, ?, ?, ?)", sources)
        jobs = [("Warehouse Worker", "Northern Logistics Oy", "Vantaa", "en", "Synthetic demo: forklift operation and Finnish required.", "https://example.test/apply/warehouse", "demo-warehouse", "VERIFIED"), ("Asiakaspalvelija", "Example Services Oy", "Helsinki", "fi", "Synteettinen demo: asiakaspalvelu ja englanti.", "https://example.test/apply/service", "demo-service", "PARTIALLY_VERIFIED")]
        self.connection.executemany("INSERT INTO jobs(title, company, location, language, description, application_url, fingerprint, verification_state) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", jobs)
        for key, value in {"application_mode": "review_everything", "daily_limit": "5", "ai_usage_mode": "minimal", "dry_run": "true"}.items():
            self.connection.execute("INSERT INTO settings(key, value) VALUES (?, ?)", (key, value))
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

    def add_job(self, job: object, verification: str) -> int | None:
        """Persist a normalized job once; return None when its fingerprint already exists."""
        try:
            cursor = self.connection.execute("INSERT INTO jobs(title, company, location, language, description, application_url, fingerprint, verification_state) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (job.title, job.company, job.location, job.language, job.description, job.application_url, job.fingerprint, verification))
        except sqlite3.IntegrityError:
            return None
        self.log("job_imported", job.title)
        self.connection.commit()
        return int(cursor.lastrowid)

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

    def add_source(self, *, name: str, url: str, country: str, source_type: str, notes: str = "") -> int:
        if not url.startswith(("https://", "http://")):
            raise ValueError("Source URL must be HTTP(S)")
        cursor = self.connection.execute("INSERT INTO job_sources(name, url, country, source_type, notes, capability) VALUES (?, ?, ?, ?, ?, 'Browser search only')", (name.strip(), url.strip(), country.strip(), source_type.strip(), notes.strip()))
        self.log("source_added", name)
        self.connection.commit()
        return int(cursor.lastrowid)

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
