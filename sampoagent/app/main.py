"""Professional local UI without a Node build chain."""

from html import escape
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from sampoagent.applications.answers import save_answer
from sampoagent.applications.workflow import classify_question
from sampoagent.careers.recommendations import recommend_occupations
from sampoagent.candidate.service import ingest_text_cv, read_cv_file
from sampoagent.country_packs.finland import builtin_sources
from sampoagent.db.repository import Repository
from sampoagent.jobs.matching import matches_preferences
from sampoagent.jobs.service import normalize_job, verification_state
from sampoagent.jobs.sources import source_health
from sampoagent.scoring.engine import DimensionConfig
from sampoagent.scoring.job_score import dump_configs, evaluate_job, load_configs
from sampoagent.cv.service import ROLE_FAMILIES, generate_cv_pdf, validate_ats_pdf


NAVIGATION = [("Dashboard", "/"), ("Profile", "/profile"), ("Career Suggestions", "/careers"), ("CVs", "/cvs"), ("Jobs", "/jobs"), ("Sources", "/sources"), ("Application Queue", "/queue"), ("Applications", "/applications"), ("Answer Bank", "/answers"), ("Analytics", "/analytics"), ("Agent", "/agent"), ("Settings", "/settings")]


def _page(title: str, body: str) -> HTMLResponse:
    nav = "".join(f'<a href="{path}">{label}</a>' for label, path in NAVIGATION)
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)} · SampoAgent</title><style>body{{margin:0;font:16px system-ui,sans-serif;background:#f4f6f5;color:#18312b}}header{{background:#103d36;color:#fff;padding:1.1rem 2rem}}nav a{{color:#dcebe7;margin-right:1rem;text-decoration:none}}main{{max-width:1100px;margin:2rem auto;padding:0 1rem}}section{{background:#fff;border:1px solid #d6e0dc;border-radius:10px;padding:1.25rem;margin:1rem 0}}table{{border-collapse:collapse;width:100%}}th,td{{padding:.6rem;border-bottom:1px solid #dbe5e1;text-align:left}}input,button,select{{padding:.55rem;border-radius:5px;border:1px solid #81948e}}button{{background:#176b5b;color:#fff;border:0;cursor:pointer}}.metric{{font-size:1.8rem;font-weight:700}}.notice{{color:#4b645d}}</style></head><body><header><h1>SampoAgent</h1><nav>{nav}</nav></header><main><h2>{escape(title)}</h2>{body}</main></body></html>""")


def create_app(database_path: str | Path = "sampoagent.db") -> FastAPI:
    repository = Repository(database_path)
    repository.initialize()
    repository.load_demo()
    app = FastAPI(title="SampoAgent", docs_url=None, redoc_url=None)

    def score_for_job(job: dict[str, object]) -> object:
        return evaluate_job(
            job=job,
            confirmed_facts=repository.confirmed_fact_values(),
            configs=load_configs(repository.setting("scoring_config")),
        )

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> HTMLResponse:
        cards = [("Jobs found", repository.count("jobs")), ("Recommended", len(recommend_occupations(repository.confirmed_skills(), ignored=[]))), ("Application queue", repository.count("applications")), ("Applied today / daily limit", f"0 / {repository.setting('daily_limit')}")]
        activity = "".join(f"<li>{escape(str(item['action']))}: {escape(str(item['details']))}</li>" for item in repository.recent_activity(5)) or "<li>No activity yet.</li>"
        return _page("Dashboard", "<section>" + "".join(f'<div class="metric">{escape(str(value))}<small> {label}</small></div>' for label, value in cards) + f"</section><section><h3>Safe by default</h3><p>Dry Run is enabled. SampoAgent never fabricates candidate facts or bypasses application security controls.</p></section><section><h3>Recent activity</h3><ul>{activity}</ul></section>")

    @app.get("/profile", response_class=HTMLResponse)
    def profile() -> HTMLResponse:
        facts = repository.rows("facts")
        rows = "".join(f"<tr><td>{escape(str(f['type']))}</td><td>{escape(str(f['value']))}</td><td>{escape(str(f['provenance']))}</td><td>{'Rejected' if f['rejected'] else ('Confirmed' if f['confirmed'] else 'Review needed')}</td><td><form style='display:inline' method='post' action='/profile/facts/{f['id']}/confirm'><button>Confirm</button></form> <form style='display:inline' method='post' action='/profile/facts/{f['id']}/reject'><button>Reject</button></form></td></tr>" for f in facts)
        record_rows = "".join(
            f"<tr><td>{escape(record_type.title())}</td><td>{escape(str(record.get('title', '')))}</td><td>{escape(str(record.get('details', '')))}</td></tr>"
            for record_type in ("experience", "education", "certificate", "licence", "language", "availability")
            for record in repository.candidate_records(record_type)
        ) or "<tr><td colspan='3'>No structured profile records yet.</td></tr>"
        record_form = "<form method='post' action='/profile/records'><label>Record type <select name='record_type'><option value='experience'>Experience</option><option value='education'>Education</option><option value='certificate'>Certificate</option><option value='licence'>Licence</option><option value='language'>Language</option><option value='availability'>Availability</option></select></label> <label>Title <input name='title' required></label> <label>Details <input name='details'></label> <button>Add record</button></form>"
        return _page("Profile", f"<section><h3>Candidate knowledge profile</h3><p>Every fact retains its source and confirmation state.</p><form method='post' action='/profile/skills'><label>Add confirmed skill <input name='skill' required></label> <button>Add skill</button></form></section><section><h3>Structured background</h3><p>Directly entered records are confirmed facts. CV-imported facts still require your review.</p>{record_form}<table><tr><th>Type</th><th>Title</th><th>Details</th></tr>{record_rows}</table></section><section><table><tr><th>Type</th><th>Value</th><th>Provenance</th><th>State</th><th>Review</th></tr>{rows}</table></section>")

    @app.post("/profile/skills")
    def add_skill(skill: str = Form(...)) -> RedirectResponse:
        repository.add_skill(skill)
        return RedirectResponse("/careers", status_code=303)

    @app.post("/profile/records")
    def add_candidate_record(record_type: str = Form(...), title: str = Form(...), details: str = Form("")) -> RedirectResponse:
        allowed = {"experience", "education", "certificate", "licence", "language", "availability"}
        if record_type in allowed and title.strip():
            repository.add_candidate_record(record_type, {"title": title.strip(), "details": details.strip()})
            repository.add_confirmed_fact(fact_type=record_type, value=title.strip())
        return RedirectResponse("/profile", status_code=303)

    @app.post("/profile/facts/{fact_id}/confirm")
    def confirm_profile_fact(fact_id: int) -> RedirectResponse:
        repository.confirm_fact(fact_id)
        return RedirectResponse("/profile", status_code=303)

    @app.post("/profile/facts/{fact_id}/reject")
    def reject_profile_fact(fact_id: int) -> RedirectResponse:
        repository.reject_fact(fact_id)
        return RedirectResponse("/profile", status_code=303)

    @app.get("/careers", response_class=HTMLResponse)
    def careers() -> HTMLResponse:
        recommendations = recommend_occupations(repository.confirmed_skills(), ignored=[])
        content = "".join(f"<tr><td>{escape(item.title_en)} / {escape(item.title_fi)}</td><td>{item.score}%</td><td>{escape(', '.join(item.supporting_facts))}</td><td>Approval required</td></tr>" for item in recommendations) or "<tr><td colspan='4'>Add confirmed skills to receive deterministic recommendations.</td></tr>"
        profiles = "".join(
            f"<tr><td>{escape(str(profile['name']))}</td><td>{escape(str(profile['notes']))}</td><td>{'Active' if profile['enabled'] else 'Inactive'}</td><td><form style='display:inline' method='post' action='/careers/profiles/{profile['id']}/toggle'><button>{'Deactivate' if profile['enabled'] else 'Activate'}</button></form> <form style='display:inline' method='post' action='/careers/profiles/{profile['id']}/delete'><button>Delete</button></form></td></tr>"
            for profile in repository.rows("career_profiles")
        ) or "<tr><td colspan='4'>No career profiles yet.</td></tr>"
        form = "<form method='post' action='/careers/profiles'><label>Career profile <input name='name' required></label> <label>Notes <input name='notes'></label> <button>Add profile</button></form>"
        return _page("Career Suggestions", f"<section><p>Recommendations are never activated automatically.</p><table><tr><th>Role</th><th>Match</th><th>Supporting facts</th><th>Target</th></tr>{content}</table></section><section><h3>My career profiles</h3><p>Add more than one career direction; changes affect only your local preferences.</p>{form}<table><tr><th>Name</th><th>Notes</th><th>State</th><th>Actions</th></tr>{profiles}</table></section>")

    @app.post("/careers/profiles")
    def add_career_profile(name: str = Form(...), notes: str = Form("")) -> RedirectResponse:
        if name.strip():
            repository.add_career_profile(name, notes)
        return RedirectResponse("/careers", status_code=303)

    @app.post("/careers/profiles/{profile_id}/toggle")
    def toggle_career_profile(profile_id: int) -> RedirectResponse:
        profile = repository.career_profile(profile_id)
        if profile:
            repository.set_career_profile_enabled(profile_id, not bool(profile["enabled"]))
        return RedirectResponse("/careers", status_code=303)

    @app.post("/careers/profiles/{profile_id}/delete")
    def delete_career_profile(profile_id: int) -> RedirectResponse:
        if repository.career_profile(profile_id):
            repository.delete_career_profile(profile_id)
        return RedirectResponse("/careers", status_code=303)

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs() -> HTMLResponse:
        job_rows = [job for job in repository.rows("jobs") if matches_preferences(job=job, preferences=repository.preferences())]

        def job_row(job: dict[str, object]) -> str:
            score = score_for_job(job)
            override = repository.job_override(int(job["id"]))
            override_text = "User review override active" if override else ""
            action = ""
            if not score.queue_eligible and not score.hard_blocked and not override:
                action = f"<form method='post' action='/jobs/{job['id']}/override'><input name='note' placeholder='Why review this?' required><button>Override for review</button></form>"
            return f"<tr><td>{escape(str(job['title']))}</td><td>{escape(str(job['company']))}</td><td>{escape(str(job['location']))}</td><td>{escape(str(job['verification_state']))}</td><td>{'Blocked: ' + escape('; '.join(score.hard_failures)) if score.hard_blocked else f'{score.final_score:.0f}%'} </td><td>{escape(' · '.join(score.explanations))}<br>{escape(override_text)}{action}</td></tr>"

        rows = "".join(job_row(job) for job in job_rows) or "<tr><td colspan='6'>No jobs match your current preferences.</td></tr>"
        form = "<form method='post' action='/jobs/import'><label>Title <input name='title' required></label> <label>Employer <input name='company' required></label> <label>Location <input name='location' required></label> <label>Description <input name='description' required></label> <label>Application URL <input name='application_url' type='url' required></label> <button>Import job</button></form>"
        return _page("Jobs", f"<section><p>Manual URL and description import is available locally; protected sites remain browser-only.</p>{form}</section><section><p>Scores use only confirmed facts. Eligibility, competitive strength, and confidence are calculated per job; hard failures override the numerical score.</p><table><tr><th>Title</th><th>Employer</th><th>Location</th><th>Verification</th><th>Score</th><th>Why</th></tr>{rows}</table></section>")

    @app.post("/jobs/import")
    def import_job(title: str = Form(...), company: str = Form(...), location: str = Form(...), description: str = Form(...), application_url: str = Form(...)) -> RedirectResponse:
        job = normalize_job(title=title, company=company, location=location, description=description, application_url=application_url)
        repository.add_job(job, verification_state(deadline=None, employer=company, application_url=application_url))
        return RedirectResponse("/jobs", status_code=303)

    @app.post("/jobs/{job_id}/override")
    def override_job_for_review(job_id: int, note: str = Form(...)) -> RedirectResponse:
        job = repository.job(job_id)
        if job and note.strip():
            score = score_for_job(job)
            if not score.hard_blocked and not score.queue_eligible:
                repository.set_job_override(job_id, decision="review", note=note)
        return RedirectResponse("/jobs", status_code=303)

    @app.get("/sources", response_class=HTMLResponse)
    def sources() -> HTMLResponse:
        rows = "".join(f"<tr><td>{escape(str(source['name']))}</td><td>{escape(str(source['source_type']))}</td><td>{escape(str(source['country']))}</td><td>{escape(source_health(str(source['url']), str(source['capability'])))}</td></tr>" for source in repository.rows("job_sources"))
        form = "<form method='post' action='/sources'><label>Name <input name='name' required></label> <label>URL <input name='url' type='url' required></label> <label>Country <input name='country' value='Finland' required></label> <label>Type <select name='source_type'><option>job board</option><option>public-sector board</option><option>recruitment agency</option><option>employer career site</option><option>custom</option></select></label> <label>Notes <input name='notes'></label> <button>Add source</button></form>"
        catalogue = " · ".join(source.name for source in builtin_sources())
        builtins = "<form method='post' action='/sources/builtin'><button>Add missing Finland sources</button></form>"
        return _page("Sources", f"<section><p>Sources respect access controls and robots restrictions. Protected or interactive sources are browser-only, never scraped.</p>{form}{builtins}<p class='notice'>Finland catalogue: {escape(catalogue)}</p></section><section><table><tr><th>Name</th><th>Type</th><th>Country</th><th>Capability / local health</th></tr>{rows}</table></section>")

    @app.post("/sources")
    def add_source(name: str = Form(...), url: str = Form(...), country: str = Form(...), source_type: str = Form(...), notes: str = Form("")) -> RedirectResponse:
        repository.add_source(name=name, url=url, country=country, source_type=source_type, notes=notes)
        return RedirectResponse("/sources", status_code=303)

    @app.post("/sources/builtin")
    def add_builtin_sources() -> RedirectResponse:
        for source in builtin_sources():
            if not repository.has_source_url(source.url):
                repository.add_source(
                    name=source.name,
                    url=source.url,
                    country="Finland",
                    source_type=source.source_type,
                    notes="Bundled Finland country-pack source",
                )
        return RedirectResponse("/sources", status_code=303)

    @app.get("/queue", response_class=HTMLResponse)
    def queue(notice: str = Query(default="")) -> HTMLResponse:
        queued = repository.rows("applications")
        rows = "".join(f"<tr><td>{item['id']}</td><td>{escape(str(item['queue_state']))}</td><td>{escape(str(item['status']))}</td></tr>" for item in queued) or "<tr><td colspan='3'>No prepared applications yet.</td></tr>"
        jobs = "".join(
            f"<li>{escape(str(job['title']))} — {f'{score.final_score:.0f}% eligible' if score.queue_eligible else escape('; '.join(score.hard_failures) or 'Below configured score threshold')} "
            + (f"<form style='display:inline' method='post' action='/queue/prepare/{job['id']}'><button>Prepare safely</button></form>" if score.queue_eligible else "")
            + "</li>"
            for job in repository.rows("jobs")
            for score in [score_for_job(job)]
        )
        message = f"<p class='notice' role='status'>{escape(notice)}</p>" if notice else ""
        return _page("Application Queue", f"<section><p>Dry Run prevents final submission. Hard requirement failures and duplicate applications never enter this queue.</p>{message}<h3>Eligible jobs</h3><ul>{jobs}</ul></section><section><table><tr><th>ID</th><th>Queue state</th><th>Status</th></tr>{rows}</table></section>")

    @app.post("/queue/prepare/{job_id}")
    def prepare_queue(job_id: int) -> RedirectResponse:
        job = repository.job(job_id)
        if not job:
            return RedirectResponse("/queue?notice=" + quote("Job was not found."), status_code=303)
        if repository.has_application_for_job(job_id):
            return RedirectResponse("/queue?notice=" + quote("This job already has an application record."), status_code=303)
        score = score_for_job(job)
        override = repository.job_override(job_id)
        if not score.queue_eligible and not (override and override["decision"] == "review" and not score.hard_blocked):
            reason = " ".join(score.hard_failures) or "This job is below the configured queue threshold."
            return RedirectResponse("/queue?notice=" + quote(reason), status_code=303)
        repository.queue_application(job_id, language=str(job["language"]), cv_path=None)
        return RedirectResponse("/queue?notice=" + quote("Application prepared for review. Dry Run remains enabled."), status_code=303)

    @app.get("/applications", response_class=HTMLResponse)
    def applications(notice: str = Query(default="")) -> HTMLResponse:
        application_items = repository.rows("applications")
        rows = "".join(f"<tr><td>{item['id']}</td><td>{escape(str(item['status']))}</td><td>{escape(str(item['cv_path'] or 'Not selected'))}</td><td>{escape(str(item['notes'] or '—'))}</td><td><form method='post' action='/applications/{item['id']}/status'><select name='status'><option>APPLIED</option><option>INTERVIEW</option><option>OFFER</option><option>REJECTED</option><option>WITHDRAWN</option></select><input name='note' placeholder='Optional note'><button>Update</button></form></td></tr>" for item in application_items) or "<tr><td colspan='5'>No applications tracked yet.</td></tr>"
        timelines = "".join(
            f"<details><summary>Application {item['id']} timeline</summary><ul>"
            + "".join(f"<li>{escape(str(event['status']))}: {escape(str(event['note']))}</li>" for event in repository.application_timeline(int(item["id"])))
            + "</ul></details>"
            for item in application_items
        ) or "<p>No application activity yet.</p>"
        message = f"<p class='notice' role='status'>{escape(notice)}</p>" if notice else ""
        return _page("Applications", f"<section><p>Track status, CV used, notes, and safe submission evidence. No credentials are stored.</p>{message}<table><tr><th>ID</th><th>Status</th><th>CV used</th><th>Latest note</th><th>Action</th></tr>{rows}</table><h3>Timeline</h3>{timelines}</section>")

    @app.post("/applications/{application_id}/status")
    def status_application(application_id: int, status: str = Form(...), note: str = Form("")) -> RedirectResponse:
        application = repository.application(application_id)
        allowed = {"APPLIED", "INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN", "FAILED", "NO_RESPONSE"}
        if status == "APPLIED" and application and application["status"] != "APPLIED":
            daily_limit = int(repository.setting("daily_limit") or "0")
            if not repository.can_queue_or_submit(daily_limit=daily_limit):
                return RedirectResponse("/applications?notice=" + quote("Daily application limit reached. Status was not changed."), status_code=303)
        if status in allowed and application:
            repository.update_application_status(application_id, status, note)
        return RedirectResponse("/applications", status_code=303)

    @app.get("/answers", response_class=HTMLResponse)
    def answers() -> HTMLResponse:
        rows = "".join(
            f"<tr><td>{escape(str(answer['category']))}</td><td>{escape(str(answer['question']))}</td><td>{escape(str(answer['value']))}</td><td>{escape(str(answer['source']))}</td><td>{escape(classify_question(str(answer['question'])))}</td></tr>"
            for answer in repository.answers()
        ) or "<tr><td colspan='5'>No saved answers yet.</td></tr>"
        form = "<form method='post' action='/answers'><label>Category <select name='category'><option value='FACT'>Fact</option><option value='PREFERENCE'>Preference</option><option value='MOTIVATION'>Motivation</option></select></label> <label>Question <input name='question' required></label> <label>Answer <input name='value' required></label> <label>Source <select name='source'><option value='USER_CONFIRMED'>User confirmed</option><option value='AI_GENERATED'>AI generated draft</option></select></label><button>Save answer</button></form>"
        return _page("Answer Bank", f"<section><p>High-risk questions always require your intervention. A user-confirmed factual answer cannot be overwritten by generated text.</p>{form}<table><tr><th>Category</th><th>Question</th><th>Answer</th><th>Source</th><th>Risk</th></tr>{rows}</table></section>")

    @app.post("/answers")
    def add_answer(category: str = Form(...), question: str = Form(...), value: str = Form(...), source: str = Form(...)) -> RedirectResponse:
        if category in {"FACT", "PREFERENCE", "MOTIVATION"} and source in {"USER_CONFIRMED", "AI_GENERATED"} and question.strip() and value.strip():
            save_answer(repository, category=category, question=question.strip(), value=value.strip(), source=source)
        return RedirectResponse("/answers", status_code=303)

    @app.get("/analytics", response_class=HTMLResponse)
    def analytics() -> HTMLResponse:
        metrics = repository.analytics()
        return _page("Analytics", f"<section><p>Jobs discovered: {metrics['jobs_discovered']} · Applications: {metrics['applications']} · Interviews: {metrics['interviews']} · Offers: {metrics['offers']}</p><p>Interview rate: {metrics['interview_rate']}% · Response rate: {metrics['response_rate']}%</p><p>Metrics describe recorded outcomes, not hire probability.</p></section>")

    @app.get("/agent", response_class=HTMLResponse)
    def agent() -> HTMLResponse:
        return _page("Agent & System Status", "<section><table><tr><th>Component</th><th>Status</th></tr><tr><td>Core database</td><td>Online</td></tr><tr><td>Finnish templates</td><td>Valid</td></tr><tr><td>English templates</td><td>Valid</td></tr><tr><td>AI provider</td><td>Not configured — deterministic mode active</td></tr><tr><td>Browser agent</td><td>Not configured — manual action required</td></tr></table></section>")

    @app.get("/settings", response_class=HTMLResponse)
    def settings() -> HTMLResponse:
        configs = load_configs(repository.setting("scoring_config"))
        preferences = repository.preferences()
        score_inputs = "".join(
            f"<fieldset><legend>{name.replace('_', ' ').title()}</legend><label>Enabled <select name='{name}_enabled'><option value='yes'{' selected' if config.enabled else ''}>Yes</option><option value='no'{' selected' if not config.enabled else ''}>No</option></select></label> <label>{name.replace('_', ' ').title()} weight <input name='{name}_weight' type='number' min='0' max='100' value='{config.weight:g}' required></label> <label>{name.replace('_', ' ').title()} minimum <input name='{name}_minimum' type='number' min='0' max='100' value='{config.minimum:g}' required></label></fieldset>"
            for name, config in configs.items()
        )
        form = f"<form method='post' action='/settings'><label>Application mode <select name='application_mode'><option value='review_everything'>Review Everything</option><option value='smart_approval'>Smart Approval</option><option value='autopilot'>Autopilot</option></select></label> <label>Daily application limit <input name='daily_limit' type='number' min='0' value='{escape(repository.setting('daily_limit') or '0')}'></label> <label>AI usage <select name='ai_usage_mode'><option value='minimal'>Minimal</option><option value='balanced'>Balanced</option><option value='quality'>Quality</option></select></label><h3>Job preferences</h3><label>Preferred locations <input name='locations' value='{escape(str(preferences.get('locations', '')))}' placeholder='Helsinki, Vantaa'></label> <label>Work type <select name='work_type'><option value='any'>Any</option><option value='onsite'>On-site</option><option value='hybrid'>Hybrid</option><option value='remote'>Remote</option></select></label> <label>Search keywords <input name='keywords' value='{escape(str(preferences.get('keywords', '')))}'></label> <label>Minimum monthly salary (€) <input name='salary_minimum' type='number' min='0' value='{escape(str(preferences.get('salary_minimum', 0)))}'></label><h3>Explainable scoring configuration</h3><p>Enabled dimensions are reweighted automatically. A job must meet every enabled minimum.</p>{score_inputs}<button>Save settings</button></form>"
        disabled = ", ".join(f"{name.replace('_', ' ').title()} is disabled" for name, config in configs.items() if not config.enabled) or "No disabled dimensions"
        return _page("Settings", f"<section><p>Application mode: {escape(repository.setting('application_mode') or 'review_everything')}</p><p>Daily application limit: {escape(repository.setting('daily_limit') or '0')}</p><p>AI usage mode: {escape(repository.setting('ai_usage_mode') or 'minimal')}</p><p>Preferred locations: {escape(str(preferences.get('locations', 'Any')))}</p><p>Work type: {escape(str(preferences.get('work_type', 'any')))}</p><p>Minimum monthly salary: {escape(str(preferences.get('salary_minimum', 0)))}</p><p>{escape(disabled)}</p>{form}</section>")

    @app.post("/settings")
    def save_settings(
        application_mode: str = Form(...),
        daily_limit: int = Form(...),
        ai_usage_mode: str = Form(...),
        eligibility_enabled: str = Form("yes"),
        eligibility_weight: float = Form(50),
        eligibility_minimum: float = Form(60),
        competitive_strength_enabled: str = Form("yes"),
        competitive_strength_weight: float = Form(35),
        competitive_strength_minimum: float = Form(40),
        confidence_enabled: str = Form("yes"),
        confidence_weight: float = Form(15),
        confidence_minimum: float = Form(40),
        locations: str = Form(""),
        work_type: str = Form("any"),
        keywords: str = Form(""),
        salary_minimum: int = Form(0),
    ) -> RedirectResponse:
        if application_mode not in {"review_everything", "smart_approval", "autopilot"} or ai_usage_mode not in {"minimal", "balanced", "quality"} or work_type not in {"any", "onsite", "hybrid", "remote"} or daily_limit < 0 or salary_minimum < 0:
            return RedirectResponse("/settings", status_code=303)
        raw_dimensions = {
            "eligibility": (eligibility_enabled, eligibility_weight, eligibility_minimum),
            "competitive_strength": (competitive_strength_enabled, competitive_strength_weight, competitive_strength_minimum),
            "confidence": (confidence_enabled, confidence_weight, confidence_minimum),
        }
        if any(enabled not in {"yes", "no"} or not 0 <= weight <= 100 or not 0 <= minimum <= 100 for enabled, weight, minimum in raw_dimensions.values()):
            return RedirectResponse("/settings", status_code=303)
        configs = {
            name: DimensionConfig(enabled=enabled == "yes", weight=weight, minimum=minimum)
            for name, (enabled, weight, minimum) in raw_dimensions.items()
        }
        if not any(config.enabled and config.weight > 0 for config in configs.values()):
            return RedirectResponse("/settings", status_code=303)
        repository.set_setting("application_mode", application_mode)
        repository.set_setting("daily_limit", str(daily_limit))
        repository.set_setting("ai_usage_mode", ai_usage_mode)
        repository.set_setting("scoring_config", dump_configs(configs))
        repository.save_preferences({"locations": locations.strip(), "work_type": work_type, "keywords": keywords.strip(), "salary_minimum": salary_minimum})
        return RedirectResponse("/settings", status_code=303)

    @app.get("/cvs", response_class=HTMLResponse)
    def cvs() -> HTMLResponse:
        families = "".join(f"<option value='{family}'>{family.replace('_', ' ').title()}</option>" for family in ROLE_FAMILIES)
        return _page("CVs", f"<section><p>Upload TXT, DOCX, or text-based PDF CVs. Extracted facts remain unconfirmed until reviewed. Built-in FI and EN role-family templates produce ATS-readable PDFs.</p><form method='post' action='/cvs/upload' enctype='multipart/form-data'><label>CV file <input name='file' type='file' accept='.txt,.docx,.pdf' required></label> <button>Upload and extract</button></form></section><section><h3>Generate confirmed-fact CV</h3><form method='post' action='/cvs/generate'><label>Language <select name='language'><option value='fi'>Finnish</option><option value='en'>English</option></select></label><label>Role family <select name='role_family'>{families}</select></label><label>Target company (optional) <input name='company'></label><label>Filename pattern <input name='filename_pattern' value='{{first}}_{{last}}_{{role}}_{{language}}.pdf'></label><p class='notice'>Available placeholders: first, last, fullname, role, company, language.</p><button>Generate PDF</button></form></section>")

    @app.post("/cvs/upload")
    async def upload_cv(file: UploadFile = File(...)) -> RedirectResponse:
        if not file.filename or Path(file.filename).suffix.lower() not in {".txt", ".docx", ".pdf"}:
            return RedirectResponse("/cvs", status_code=303)
        payload = await file.read()
        if len(payload) > 5 * 1024 * 1024:
            return RedirectResponse("/cvs", status_code=303)
        safe_name = Path(file.filename).name
        upload_dir = Path("application_data") / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        stored_path = upload_dir / safe_name
        stored_path.write_bytes(payload)
        content = read_cv_file(stored_path)
        result = ingest_text_cv(content, source_id=safe_name, storage_dir=upload_dir)
        for fact in result.facts:
            repository.add_extracted_fact(fact_type=fact.type, value=fact.value, source_id=fact.source_id, confidence=fact.confidence)
        return RedirectResponse("/profile", status_code=303)

    @app.post("/cvs/generate")
    def generate_cv(
        language: str = Form(...),
        role_family: str = Form(...),
        filename_pattern: str = Form(""),
        company: str = Form(""),
    ) -> HTMLResponse:
        profile = repository.profile()
        if not profile:
            return _page("CVs", "<section>Candidate profile is required before generating a CV.</section>")
        facts = repository.rows("facts")
        record_types = ("experience", "education", "certificate", "licence")
        records = {record_type: repository.candidate_records(record_type) for record_type in record_types}
        path = generate_cv_pdf(output_dir=Path("application_data") / "generated", language=language, role_family=role_family, candidate=profile, facts=facts, filename_pattern=filename_pattern or None, company=company, records=records)
        required = [profile["name"]]
        if profile.get("email"):
            required.append(profile["email"])
        required.extend(str(fact["value"]) for fact in facts if fact.get("confirmed") and fact.get("type") in {"skill", "language"})
        required.extend(str(record.get("title") or record.get("name") or "") for group in records.values() for record in group if record.get("title") or record.get("name"))
        report = validate_ats_pdf(path, required=required)
        download_path = "/cvs/generated/" + quote(path.name)
        return _page("CV Generated", f"<section><p>Generated: <a href='{escape(download_path)}'>{escape(path.name)}</a></p><p>ATS readability: {report.score}%</p></section>")

    @app.get("/cvs/generated/{filename}")
    def download_generated_cv(filename: str) -> FileResponse:
        safe_filename = Path(filename).name
        path = Path("application_data") / "generated" / safe_filename
        if safe_filename != filename or path.suffix.casefold() != ".pdf" or not path.is_file():
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Generated CV not found")
        return FileResponse(path, media_type="application/pdf", filename=safe_filename)

    @app.get("/onboarding", response_class=HTMLResponse)
    def onboarding() -> HTMLResponse:
        return _page("Getting Started", "<section><ol><li>Choose a language and enter candidate details.</li><li>Upload CVs and confirm extracted facts.</li><li>Add skills, targets, locations and sources.</li><li>Review scoring, daily limit and application mode.</li><li>Start a Dry Run.</li></ol><form method='post' action='/onboarding/complete'><label>Name <input name='name' required></label> <label>Language <select name='locale'><option value='fi'>Finnish</option><option value='en'>English</option></select></label> <button>Start Dry Run</button></form></section>")

    @app.post("/onboarding/complete")
    def complete_onboarding(name: str = Form(...), locale: str = Form(...)) -> RedirectResponse:
        repository.save_profile(name, locale)
        return RedirectResponse("/", status_code=303)

    return app
