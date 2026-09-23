"""Professional local UI without a Node build chain."""

from html import escape
import hmac
from pathlib import Path
import secrets
import time
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from sampoagent.applications.answers import save_answer
from sampoagent.applications.workflow import classify_question
from sampoagent.careers.recommendations import recommend_occupations
from sampoagent.candidate.service import ingest_text_cv, read_cv_file
from sampoagent.country_packs.finland import builtin_sources
from sampoagent.db.repository import Repository
from sampoagent.jobs.matching import matches_preferences
from sampoagent.jobs.service import normalize_job, verification_state
from sampoagent.jobs.runner import run_discovery
from sampoagent.integrations.email_oauth import EmailIntegrationError, OAuthConfig, authorization_url, create_pkce_pair, decrypt_token_payload, encrypt_token_payload, exchange_code, refresh_access_token
from sampoagent.integrations.mailbox import fetch_recent_messages
from sampoagent.jobs.sources import source_health
from sampoagent.scoring.engine import DimensionConfig
from sampoagent.scoring.job_score import dump_configs, evaluate_job, load_configs
from sampoagent.cv.service import ROLE_FAMILIES, generate_cv_pdf, validate_ats_pdf


NAVIGATION = [("Dashboard", "/"), ("Profile", "/profile"), ("Career Suggestions", "/careers"), ("CVs", "/cvs"), ("Jobs", "/jobs"), ("Sources", "/sources"), ("Application Queue", "/queue"), ("Applications", "/applications"), ("Email", "/settings/email"), ("Answer Bank", "/answers"), ("Analytics", "/analytics"), ("Agent", "/agent"), ("Settings", "/settings")]
PRIMARY_NAVIGATION = {"Dashboard", "Profile", "CVs", "Jobs", "Sources", "Applications", "Email", "Settings"}


def _page(title: str, body: str, *, path: str | None = None) -> HTMLResponse:
    """Render the consistent, keyboard-accessible local application shell."""
    current_path = path or next((href for label, href in NAVIGATION if label == title), "")
    primary_nav = "".join(
        f'<a class="nav-link{" active" if href == current_path else ""}" '
        f'aria-current="{"page" if href == current_path else "false"}" href="{href}">{label}</a>'
        for label, href in NAVIGATION if label in PRIMARY_NAVIGATION
    )
    extra_items = [(label, href) for label, href in NAVIGATION if label not in PRIMARY_NAVIGATION]
    extras_open = " open" if any(href == current_path for _, href in extra_items) else ""
    extra_nav = "".join(
        f'<a class="nav-link{" active" if href == current_path else ""}" aria-current="{"page" if href == current_path else "false"}" href="{href}">{label}</a>'
        for label, href in extra_items
    )
    nav = primary_nav + f"<details class='nav-more'{extras_open}><summary class='nav-link'>More tools</summary><div class='nav-extra' style='display:grid;gap:.25rem'>{extra_nav}</div></details>"
    responsive_body = body.replace("<table>", '<div class="table-wrap"><table>').replace("</table>", "</table></div>")
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)} · SampoAgent</title><style>
:root{{--surface-0:#090b14;--surface-1:#111526;--surface-2:#181d32;--surface-3:#222844;--border:#303858;--text:#f3f5ff;--muted:#aeb7d0;--primary:#5141b6;--primary-hover:#c1b8ff;--success:#4fd6a8;--review:#ffc869;--risk:#ff7b91;--focus:#6db8ff;--radius:18px;--shadow:0 18px 45px rgba(0,0,0,.28)}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--surface-0);color:var(--text);font:15px/1.55 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}} a{{color:inherit}} .skip-link{{position:absolute;left:1rem;top:-5rem;background:var(--text);color:var(--surface-0);padding:.65rem 1rem;border-radius:8px;z-index:10}}.skip-link:focus{{top:1rem}}
.app-shell{{display:grid;grid-template-columns:248px minmax(0,1fr);min-height:100vh}}.sidebar{{position:sticky;top:0;height:100vh;overflow-y:auto;padding:1.5rem 1rem;background:linear-gradient(180deg,#12172a,#0c0f1d);border-right:1px solid var(--border)}}.brand{{display:flex;gap:.7rem;align-items:center;padding:.45rem .65rem 1.7rem}}.brand-mark{{display:grid;place-items:center;width:34px;height:34px;border-radius:11px;background:linear-gradient(135deg,var(--primary),#4b61c4);font-weight:900}}.brand-copy strong,.brand-copy span{{display:block}}.brand-copy span{{font-size:.75rem;color:var(--muted)}}.sidebar nav{{display:grid;gap:.25rem}}.nav-link{{padding:.63rem .75rem;color:var(--muted);text-decoration:none;border-radius:10px;font-weight:650}}.nav-link:hover{{background:rgba(139,124,255,.13);color:var(--text)}}.nav-link.active{{background:linear-gradient(90deg,rgba(139,124,255,.28),rgba(109,184,255,.12));color:#fff;box-shadow:inset 3px 0 var(--primary)}}
.main-content{{width:min(1260px,100%);padding:2.5rem clamp(1rem,4vw,4rem);margin:0 auto}}.page-header{{margin:0 0 1.5rem}}.eyebrow{{margin:0 0 .2rem;color:var(--primary-hover);font-size:.78rem;font-weight:800;letter-spacing:.12em;text-transform:uppercase}}h1{{margin:0;font-size:clamp(2rem,4vw,3.1rem);letter-spacing:-.045em}}h2{{letter-spacing:-.025em}}h3{{margin-top:0}} p{{color:var(--muted)}}
section,.panel{{background:linear-gradient(145deg,rgba(30,36,61,.94),rgba(17,21,38,.94));border:1px solid var(--border);border-radius:var(--radius);padding:1.3rem;margin:1rem 0;box-shadow:var(--shadow)}}.panel-heading{{display:flex;align-items:center;justify-content:space-between;gap:1rem}}.table-wrap{{overflow-x:auto;border:1px solid var(--border);border-radius:12px}}table{{border-collapse:collapse;width:100%;min-width:650px}}th,td{{padding:.85rem 1rem;border-bottom:1px solid rgba(48,56,88,.75);text-align:left;vertical-align:top}}th{{background:rgba(255,255,255,.035);color:#cbd4f4;font-size:.76rem;letter-spacing:.08em;text-transform:uppercase}}tr:last-child td{{border-bottom:0}}
.dashboard-hero{{padding:clamp(1.5rem,4vw,2.5rem);background:radial-gradient(circle at 88% 15%,rgba(109,184,255,.27),transparent 27%),linear-gradient(135deg,#252057,#151b38);border-color:#4c4b85}}.dashboard-hero h2{{max-width:650px;font-size:clamp(1.6rem,3vw,2.35rem);margin:.7rem 0 .25rem}}.metric-grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1rem;margin:1rem 0}}.metric-card{{padding:1.15rem;border:1px solid var(--border);border-radius:14px;background:var(--surface-1)}}.metric-card span{{display:block;color:var(--muted);font-size:.82rem;font-weight:700}}.metric-card strong{{display:block;margin-top:.45rem;font-size:1.75rem;letter-spacing:-.04em}}.content-grid{{display:grid;grid-template-columns:1.15fr .85fr;gap:1rem}}.activity-list{{margin:0;padding-left:1.2rem;color:var(--muted)}}.next-step{{margin:.5rem 0;padding:.75rem;border-radius:10px;background:rgba(139,124,255,.1);color:#e2e6ff}}
form{{display:flex;flex-wrap:wrap;gap:.75rem;align-items:end;margin:.9rem 0}}label{{display:grid;gap:.32rem;min-width:150px;color:#d7ddf5;font-size:.86rem;font-weight:650}}input,select,button{{font:inherit;border-radius:10px;padding:.62rem .75rem}}input,select{{min-height:42px;background:#0c1020;color:var(--text);border:1px solid #414b71}}input:focus,select:focus,button:focus,a:focus{{outline:3px solid var(--focus);outline-offset:2px}}button{{border:1px solid transparent;background:linear-gradient(135deg,var(--primary),#392c89);color:white;font-weight:750;cursor:pointer;box-shadow:0 8px 18px rgba(91,91,219,.22)}}button:hover{{filter:none;box-shadow:0 0 0 2px rgba(193,184,255,.35)}}button:disabled{{opacity:.65;cursor:not-allowed}}button.secondary{{background:var(--surface-3);border-color:#485276}}button.danger{{background:linear-gradient(135deg,#98233f,#68152b)}}.notice{{color:#dbe2fc;background:rgba(109,184,255,.1);border-left:3px solid var(--focus);padding:.7rem .85rem;border-radius:8px}}.metric{{font-size:1.8rem;font-weight:800}}.status{{display:inline-flex;align-items:center;gap:.35rem;border-radius:999px;padding:.25rem .58rem;font-size:.78rem;font-weight:800;white-space:nowrap}}.status-success{{color:#a7f3d6;background:rgba(79,214,168,.14)}}.status-review{{color:#ffdc91;background:rgba(255,200,105,.14)}}.status-risk{{color:#ffb3c0;background:rgba(255,123,145,.14)}}.action-row{{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center}}.empty-state{{padding:1.5rem;text-align:center;color:var(--muted)}}.settings-form{{display:block}}.settings-group{{padding:1rem 0;border-top:1px solid var(--border)}}.settings-group:first-child{{border-top:0;padding-top:0}}.settings-group h3{{margin-bottom:.25rem}}.settings-fields{{display:flex;flex-wrap:wrap;gap:.75rem;align-items:end}}fieldset{{min-width:240px;border:1px solid var(--border);border-radius:12px;padding:.85rem}}legend{{color:var(--primary-hover);font-weight:800}}
@media (max-width: 760px){{.app-shell{{display:block}}.sidebar{{position:static;height:auto;padding:1rem;border-right:0;border-bottom:1px solid var(--border)}}.brand{{padding:.2rem .3rem .8rem}}.sidebar nav{{display:flex;overflow-x:auto;padding-bottom:.25rem}}.nav-link{{white-space:nowrap}}.main-content{{padding:1.5rem 1rem}}section,.panel{{padding:1rem}}form{{display:grid}}label{{min-width:0}}button{{min-height:42px}}.metric-grid,.content-grid{{grid-template-columns:1fr}}}}
</style></head><body><a class="skip-link" href="#content">Skip to content</a><div class="app-shell"><aside class="sidebar"><div class="brand"><span class="brand-mark">S</span><span class="brand-copy"><strong>SampoAgent</strong><span>Career workspace</span></span></div><nav aria-label="Main navigation">{nav}</nav></aside><main class="main-content" id="content"><header class="page-header"><p class="eyebrow">Career workspace</p><h1>{escape(title)}</h1></header>{responsive_body}</main></div></body></html>""")


def _status(text: str, tone: str) -> str:
    return f'<span class="status status-{escape(tone)}">{escape(text)}</span>'


def create_app(database_path: str | Path = "sampoagent.db", *, demo_data: bool = False) -> FastAPI:
    from dotenv import load_dotenv

    load_dotenv(override=False)
    repository = Repository(database_path)
    repository.initialize()
    if demo_data:
        repository.load_demo()
    app = FastAPI(title="SampoAgent", docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])
    app.state.repository = repository
    app.state.email_oauth_states = {}

    @app.middleware("http")
    async def protect_local_form_posts(request: Request, call_next):
        if request.method == "POST":
            origin = request.headers.get("origin")
            fetch_site = request.headers.get("sec-fetch-site", "").casefold()
            expected_origin = f"{request.url.scheme}://{request.url.netloc}"
            if (origin is not None and not hmac.compare_digest(origin.rstrip("/"), expected_origin)) or fetch_site == "cross-site":
                return HTMLResponse("Cross-origin form submission rejected.", status_code=403)
        return await call_next(request)

    def score_for_job(job: dict[str, object]) -> object:
        return evaluate_job(
            job=job,
            confirmed_facts=repository.confirmed_fact_values(),
            configs=load_configs(repository.setting("scoring_config")),
        )

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> HTMLResponse:
        cards = [
            ("Jobs found", repository.count("jobs")),
            ("Recommended", len(recommend_occupations(repository.confirmed_skills(), ignored=[]))),
            ("Application queue", repository.count("applications")),
            ("Applied today / daily limit", f"{repository.applications_today()} / {repository.setting('daily_limit')}"),
        ]
        card_markup = "".join(
            f'<article class="metric-card"><span>{escape(label)}</span><strong>{escape(str(value))}</strong></article>'
            for label, value in cards
        )
        activity = "".join(
            f"<li>{escape(str(item['action']).replace('_', ' ').title())}: {escape(str(item['details']))}</li>"
            for item in repository.recent_activity(5)
        ) or "<li>No activity yet.</li>"
        if repository.profile() is None:
            next_step = "Create your profile and upload a CV. SampoAgent will suggest job searches from confirmed experience and skills."
            primary_action = '<a class="nav-link active" href="/onboarding">Set up your career workspace</a>'
        else:
            next_step = "Review confirmed facts, choose the roles that fit you, then find matching jobs in one step."
            primary_action = '<a class="nav-link active" href="/jobs">Find matching jobs</a>'
        body = f"""
<section class="dashboard-hero"><span class="status status-review">Dry Run is on</span><h2>Build momentum without giving up control.</h2><p>Your data stays local. Nothing is submitted until you deliberately move beyond review.</p></section>
<section class="metric-grid">{card_markup}</section>
<div class="content-grid"><section class="panel"><h3>What to do next</h3><p class="next-step">{escape(next_step)}</p>{primary_action}</section><section class="panel"><h3>Recent activity</h3><ul class="activity-list">{activity}</ul></section></div>"""
        return _page("Dashboard", body, path="/")

    @app.get("/profile", response_class=HTMLResponse)
    def profile() -> HTMLResponse:
        candidate = repository.profile()
        facts = repository.rows("facts")
        rows = "".join(f"<tr><td>{escape(str(f['type']))}</td><td>{escape(str(f['value']))}</td><td>{escape(str(f['provenance']))}</td><td>{_status('Rejected', 'risk') if f['rejected'] else (_status('Confirmed', 'success') if f['confirmed'] else _status('Review needed', 'review'))}</td><td><form style='display:inline' method='post' action='/profile/facts/{f['id']}/confirm'><button>Confirm</button></form> <form style='display:inline' method='post' action='/profile/facts/{f['id']}/reject'><button class='danger'>Reject</button></form> <form style='display:inline' method='post' action='/profile/facts/{f['id']}/edit'><input name='value' value='{escape(str(f['value']))}' required><button class='secondary'>Correct</button></form> <form style='display:inline' method='post' action='/profile/facts/{f['id']}/delete'><button class='danger'>Delete</button></form></td></tr>" for f in facts)
        record_rows = "".join(
            f"<tr><td>{escape(record_type.title())}</td><td>{escape(str(record.get('title', '')))}</td><td>{escape(str(record.get('details', '')))}<details><summary>Edit or remove</summary><form method='post' action='/profile/records/{record['id']}/edit'><label>Title <input name='title' value='{escape(str(record.get('title', '')))}' required></label><label>Details <input name='details' value='{escape(str(record.get('details', '')))}'></label><button>Save</button></form><form method='post' action='/profile/records/{record['id']}/delete'><button class='danger'>Remove record</button></form></details></td></tr>"
            for record_type in ("experience", "education", "certificate", "licence", "language", "availability")
            for record in repository.candidate_record_rows(record_type)
        ) or "<tr><td colspan='3'>No structured profile records yet.</td></tr>"
        record_form = "<form method='post' action='/profile/records'><label>Record type <select name='record_type'><option value='experience'>Experience</option><option value='education'>Education</option><option value='certificate'>Certificate</option><option value='licence'>Licence</option><option value='language'>Language</option><option value='availability'>Availability</option></select></label> <label>Title <input name='title' required></label> <label>Details <input name='details'></label> <button>Add record</button></form>"
        identity_form = f"<form method='post' action='/profile/details'><label>Name <input name='name' value='{escape(candidate['name'] if candidate else '')}' required></label> <label>Email <input name='email' type='email' value='{escape(candidate.get('email', '') if candidate else '')}'></label> <label>Preferred language <select name='locale'><option value='fi'{' selected' if candidate and candidate['locale'] == 'fi' else ''}>Finnish</option><option value='en'{' selected' if not candidate or candidate['locale'] == 'en' else ''}>English</option></select></label><button>Save profile</button></form>"
        return _page("Profile", f"<section><h3>Candidate knowledge profile</h3><p>Every fact retains its source and confirmation state.</p>{identity_form}<form method='post' action='/profile/skills'><label>Add confirmed skill <input name='skill' required></label> <button>Add skill</button></form></section><section><h3>Structured background</h3><p>Directly entered records are confirmed facts. CV-imported facts still require your review.</p>{record_form}<table><tr><th>Type</th><th>Title</th><th>Details</th></tr>{record_rows}</table></section><section><table><tr><th>Type</th><th>Value</th><th>Provenance</th><th>State</th><th>Review</th></tr>{rows}</table></section>")

    @app.post("/profile/details")
    def save_profile_details(name: str = Form(...), email: str = Form(""), locale: str = Form(...)) -> RedirectResponse:
        if name.strip() and locale in {"fi", "en"}:
            repository.save_profile(name.strip(), locale, email.strip())
        return RedirectResponse("/profile", status_code=303)

    @app.post("/profile/skills")
    def add_skill(skill: str = Form(...)) -> RedirectResponse:
        repository.add_skill(skill)
        return RedirectResponse("/careers", status_code=303)

    @app.post("/profile/records")
    def add_candidate_record(record_type: str = Form(...), title: str = Form(...), details: str = Form("")) -> RedirectResponse:
        allowed = {"experience", "education", "certificate", "licence", "language", "availability"}
        if record_type in allowed and title.strip():
            record_id = repository.add_candidate_record(record_type, {"title": title.strip(), "details": details.strip()})
            repository.add_confirmed_fact(fact_type=record_type, value=title.strip(), source_id=f"candidate_record:{record_id}")
        return RedirectResponse("/profile", status_code=303)

    @app.post("/profile/records/{record_id}/edit")
    def edit_candidate_record(record_id: int, title: str = Form(...), details: str = Form("")) -> RedirectResponse:
        try:
            repository.update_candidate_record(record_id, title=title, details=details)
        except ValueError:
            pass
        return RedirectResponse("/profile", status_code=303)

    @app.post("/profile/records/{record_id}/delete")
    def delete_candidate_record(record_id: int) -> RedirectResponse:
        repository.delete_candidate_record(record_id)
        return RedirectResponse("/profile", status_code=303)

    @app.post("/profile/facts/{fact_id}/confirm")
    def confirm_profile_fact(fact_id: int) -> RedirectResponse:
        repository.confirm_fact(fact_id)
        return RedirectResponse("/profile", status_code=303)

    @app.post("/profile/facts/{fact_id}/reject")
    def reject_profile_fact(fact_id: int) -> RedirectResponse:
        repository.reject_fact(fact_id)
        return RedirectResponse("/profile", status_code=303)

    @app.post("/profile/facts/{fact_id}/edit")
    def edit_profile_fact(fact_id: int, value: str = Form(...)) -> RedirectResponse:
        if value.strip():
            repository.edit_fact(fact_id, value)
        return RedirectResponse("/profile", status_code=303)

    @app.post("/profile/facts/{fact_id}/delete")
    def delete_profile_fact(fact_id: int) -> RedirectResponse:
        repository.delete_fact(fact_id)
        return RedirectResponse("/profile", status_code=303)

    @app.get("/careers", response_class=HTMLResponse)
    def careers() -> HTMLResponse:
        recommendations = recommend_occupations(repository.confirmed_skills(), ignored=[])
        targets = repository.target_occupations()
        active_targets = {(str(target["title_en"]), str(target["title_fi"])) for target in targets if target["enabled"]}
        content = "".join(
            f"<tr><td>{escape(item.title_en)} / {escape(item.title_fi)}</td><td>{item.score}%</td><td>{escape(', '.join(item.supporting_facts))}</td><td>{'Active target' if (item.title_en, item.title_fi) in active_targets else f'''<form method='post' action='/careers/targets'><input type='hidden' name='title_en' value='{escape(item.title_en)}'><input type='hidden' name='title_fi' value='{escape(item.title_fi)}'><button>Make target</button></form>'''}</td></tr>"
            for item in recommendations
        ) or "<tr><td colspan='4'>Add confirmed skills to receive deterministic recommendations.</td></tr>"
        profiles = "".join(
            f"<tr><td>{escape(str(profile['name']))}</td><td>{escape(str(profile['notes']))}</td><td>{'Active' if profile['enabled'] else 'Inactive'}</td><td><form style='display:inline' method='post' action='/careers/profiles/{profile['id']}/toggle'><button>{'Deactivate' if profile['enabled'] else 'Activate'}</button></form> <details><summary>Edit</summary><form method='post' action='/careers/profiles/{profile['id']}/edit'><label>Name <input name='name' value='{escape(str(profile['name']))}' required></label><label>Notes <input name='notes' value='{escape(str(profile['notes']))}'></label><button>Save</button></form></details> <form method='post' action='/careers/profiles/{profile['id']}/delete'><button class='danger'>Delete</button></form></td></tr>"
            for profile in repository.rows("career_profiles")
        ) or "<tr><td colspan='4'>No career profiles yet.</td></tr>"
        target_rows = "".join(
            f"<tr><td>{escape(str(target['title_en']))} / {escape(str(target['title_fi']))}</td><td>{'Active' if target['enabled'] else 'Inactive'}</td><td><form method='post' action='/careers/targets/{target['id']}/toggle'><button>{'Deactivate' if target['enabled'] else 'Activate'}</button></form><form method='post' action='/careers/targets/{target['id']}/delete'><button class='danger'>Remove</button></form></td></tr>"
            for target in targets
        ) or "<tr><td colspan='3'>No approved targets yet.</td></tr>"
        form = "<form method='post' action='/careers/profiles'><label>Career profile <input name='name' required></label> <label>Notes <input name='notes'></label> <button>Add profile</button></form>"
        return _page("Career Suggestions", f"<section><p>Recommendations are never activated automatically. Choose Make target only for occupations you want to pursue.</p><table><tr><th>Role</th><th>Match</th><th>Supporting facts</th><th>Target</th></tr>{content}</table></section><section><h3>My target occupations</h3><p>These are your explicit, local target choices. You can pause any target without deleting it.</p><table><tr><th>Role</th><th>State</th><th>Action</th></tr>{target_rows}</table></section><section><h3>My career profiles</h3><p>Add more than one career direction; changes affect only your local preferences.</p>{form}<table><tr><th>Name</th><th>Notes</th><th>State</th><th>Actions</th></tr>{profiles}</table></section>")

    @app.post("/careers/targets")
    def add_target_occupation(title_en: str = Form(...), title_fi: str = Form(...)) -> RedirectResponse:
        try:
            repository.add_target_occupation(title_en, title_fi)
        except ValueError:
            pass
        return RedirectResponse("/careers", status_code=303)

    @app.post("/careers/targets/{target_id}/toggle")
    def toggle_target_occupation(target_id: int) -> RedirectResponse:
        target = repository.target_occupation(target_id)
        if target:
            repository.set_target_occupation_enabled(target_id, not bool(target["enabled"]))
        return RedirectResponse("/careers", status_code=303)

    @app.post("/careers/targets/{target_id}/delete")
    def delete_target_occupation(target_id: int) -> RedirectResponse:
        repository.delete_target_occupation(target_id)
        return RedirectResponse("/careers", status_code=303)

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

    @app.post("/careers/profiles/{profile_id}/edit")
    def edit_career_profile(profile_id: int, name: str = Form(...), notes: str = Form("")) -> RedirectResponse:
        try:
            repository.update_career_profile(profile_id, name=name, notes=notes)
        except ValueError:
            pass
        return RedirectResponse("/careers", status_code=303)

    @app.post("/careers/profiles/{profile_id}/delete")
    def delete_career_profile(profile_id: int) -> RedirectResponse:
        if repository.career_profile(profile_id):
            repository.delete_career_profile(profile_id)
        return RedirectResponse("/careers", status_code=303)

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs(notice: str = Query(default="")) -> HTMLResponse:
        job_rows = [job for job in repository.rows("jobs") if matches_preferences(job=job, preferences=repository.preferences())]

        def job_row(job: dict[str, object]) -> str:
            score = score_for_job(job)
            override = repository.job_override(int(job["id"]))
            override_text = "User review override active" if override else ""
            action = ""
            if not score.queue_eligible and not score.hard_blocked and not override:
                action = f"<form method='post' action='/jobs/{job['id']}/override'><input name='note' placeholder='Why review this?' required><button>Override for review</button></form>"
            language_form = f"<form method='post' action='/jobs/{job['id']}/language'><label>Language: {escape(str(job['language']))}<select name='language'><option value='fi'{' selected' if job['language'] == 'fi' else ''}>fi</option><option value='en'{' selected' if job['language'] == 'en' else ''}>en</option></select></label><button>Set</button></form>"
            return f"<tr><td>{escape(str(job['title']))}</td><td>{escape(str(job['company']))}</td><td>{escape(str(job['location']))}</td><td>{escape(str(job['verification_state']))}<br>{language_form}</td><td>{'Blocked: ' + escape('; '.join(score.hard_failures)) if score.hard_blocked else f'{score.final_score:.0f}%'} </td><td>{escape(' · '.join(score.explanations))}<br>{escape(override_text)}{action}</td></tr>"

        rows = "".join(job_row(job) for job in job_rows) or "<tr><td colspan='6'>No jobs match your current preferences.</td></tr>"
        form = "<form method='post' action='/jobs/import'><label>Title <input name='title' required></label> <label>Employer <input name='company' required></label> <label>Location <input name='location' required></label> <label>Description <input name='description' required></label> <label>Application URL <input name='application_url' type='url' required></label> <button class='secondary'>Add a job manually</button></form>"
        last_run = repository.latest_discovery_run()
        summary = "No search run yet. Your confirmed experience and saved role targets will shape the search links." if not last_run else str(last_run["summary"])
        query_links = "".join(
            f"<li><a href='{escape(query.search_url)}' target='_blank' rel='noopener noreferrer'>{escape(query.phrase)}{(' · ' + escape(query.location)) if query.location else ''} — {escape(query.source_name)}</a></li>"
            for query in (run_discovery_preview(repository).queries[:30])
        ) or "<li>Add confirmed experience, a target role, or a search keyword to create personalized searches.</li>"
        message = f"<p class='notice' role='status'>{escape(notice)}</p>" if notice else ""
        latest_results = repository.discovery_source_results(int(last_run["id"])) if last_run else []
        result_cards = "".join(
            f"<article class='metric-card'><strong>{escape(str(result['source_name']))}</strong><p>{escape(str(result['status']).replace('_', ' ').title())} · {int(result['jobs_found'])} found · {int(result['imported_count'])} added</p><p>{escape(str(result['message']))}</p></article>"
            for result in latest_results
        ) or ""
        result_section = f"<section><h2>Last source results</h2><div class='metric-grid'>{result_cards}</div></section>" if result_cards else ""
        body = f"<section class='dashboard-hero'><p>Search from your profile</p><h2>Find matching jobs without typing every role by hand.</h2><p>Search terms come only from confirmed experience, saved target roles and your preferences. Public feed results can be added automatically; other sites open as personalized searches.</p><form method='post' action='/jobs/discover'><button>Find matching jobs</button></form><p class='notice'>{escape(summary)}</p>{message}</section><section><div class='panel-heading'><h2>Personalized search links</h2><a href='/sources'>Manage sources</a></div><ul>{query_links}</ul></section>{result_section}<section><h2>Matching jobs</h2><p>Scores use confirmed facts only. Every imported result keeps its source and verification status for your review.</p><table><tr><th>Title</th><th>Employer</th><th>Location</th><th>Verification</th><th>Score</th><th>Why</th></tr>{rows}</table>{form}</section>"
        return _page("Jobs", body)

    def run_discovery_preview(repository: Repository):
        from sampoagent.jobs.discovery import build_search_plan

        record_types = ("experience", "education", "certificate", "licence", "language", "availability")
        return build_search_plan(
            facts=repository.rows("facts"),
            candidate_records={kind: repository.candidate_records(kind) for kind in record_types},
            targets=repository.target_occupations(),
            career_profiles=repository.rows("career_profiles"),
            preferences=repository.preferences(),
            sources=repository.rows("job_sources"),
        )

    @app.post("/jobs/discover")
    def discover_jobs() -> RedirectResponse:
        if not repository.profile():
            return RedirectResponse("/onboarding", status_code=303)
        report = run_discovery(repository)
        return RedirectResponse("/jobs?notice=" + quote(f"Search finished: {report.jobs_found} found, {report.imported_count} added, {report.duplicates_count} duplicates."), status_code=303)

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

    @app.post("/jobs/{job_id}/language")
    def override_job_language(job_id: int, language: str = Form(...)) -> RedirectResponse:
        if repository.job(job_id) and language in {"fi", "en"}:
            repository.set_job_language(job_id, language)
        return RedirectResponse("/jobs", status_code=303)

    @app.get("/sources", response_class=HTMLResponse)
    def sources(notice: str = Query(default=""), q: str = Query(default="")) -> HTMLResponse:
        last_run = repository.latest_discovery_run()
        latest_results = {int(result["source_id"]): result for result in repository.discovery_source_results(int(last_run["id"])) if result["source_id"] is not None} if last_run else {}
        all_source_rows = repository.rows("job_sources")
        search_key = q.strip().casefold()
        visible_source_rows = [source for source in all_source_rows if not search_key or search_key in " ".join(str(source.get(key, "")) for key in ("name", "url", "source_type", "country", "capability", "notes")).casefold()]
        capabilities = ("Browser search only", "RSS/Atom feed", "JSON Feed", "Job Market Finland API")
        def capability_options(selected: str) -> str:
            return "".join(f"<option value='{escape(value)}{' selected' if value == selected else ''}'>{escape(value)}</option>" for value in capabilities)
        rows = "".join(
            f"<tr><td><strong>{escape(str(source['name']))}</strong><br>{escape(str(source['notes']))}</td><td>{escape(str(source['source_type']))}<br>{escape(str(source['country']))}</td><td>{escape(str(source['capability']))}<br>{escape(source_health(str(source['url']), str(source['capability'])))}</td><td>{escape(str(latest_results.get(int(source['id']), {}).get('status', 'Not checked')))} · {int(latest_results.get(int(source['id']), {}).get('jobs_found', 0))} found / {int(latest_results.get(int(source['id']), {}).get('imported_count', 0))} added</td><td>{'Active' if source['enabled'] else 'Inactive'} <form style='display:inline' method='post' action='/sources/{source['id']}/toggle'><button class='secondary'>{'Disable' if source['enabled'] else 'Enable'}</button></form></td><td><details><summary>Edit source</summary><form method='post' action='/sources/{source['id']}/edit'><label>Name <input name='name' value='{escape(str(source['name']))}' required></label><label>URL <input name='url' type='url' value='{escape(str(source['url']))}' required></label><label>Country <input name='country' value='{escape(str(source['country']))}' required></label><label>Type <select name='source_type'><option>{escape(str(source['source_type']))}</option><option>job board</option><option>public-sector board</option><option>recruitment agency</option><option>employer career site</option><option>custom</option></select></label><label>How to search <select name='capability'>{capability_options(str(source['capability']))}</select></label><label>Notes <input name='notes' value='{escape(str(source['notes']))}'></label><button>Save source</button></form><form method='post' action='/sources/{source['id']}/delete' onsubmit=\"return confirm('Remove this source? Previously imported jobs keep their source details.')\"><button class='danger'>Remove source</button></form></details></td></tr>"
            for source in visible_source_rows
        ) or "<tr><td colspan='6'>No matching sources. Adjust the search or add a source.</td></tr>"
        form = f"<form method='post' action='/sources'><label>Name <input name='name' required></label> <label>URL <input name='url' type='url' placeholder='https://example.org/careers' required></label> <label>Country <input name='country' value='Finland' required></label> <label>Type <select name='source_type'><option>job board</option><option>public-sector board</option><option>recruitment agency</option><option>employer career site</option><option>custom</option></select></label> <label>How to search <select name='capability'>{capability_options('Browser search only')}</select></label> <label>Notes <input name='notes'></label> <button>Add source</button></form>"
        catalogue = " · ".join(source.name for source in builtin_sources())
        builtins = "<form method='post' action='/sources/builtin'><button>Add missing Finland sources</button></form>"
        message = f"<p class='notice' role='status'>{escape(notice)}</p>" if notice else ""
        search_form = f"<form method='get' action='/sources'><label>Find a source <input name='q' value='{escape(q)}' placeholder='Search by name, type, country'></label><button class='secondary'>Search</button></form><p>{len(visible_source_rows)} of {len(all_source_rows)} sources</p>"
        return _page("Sources", f"<section><h2>Control what SampoAgent can search</h2><p>Protected or interactive websites are browser-only. Only explicitly marked public feeds and the official Job Market Finland API are checked automatically. CAPTCHA/proxy bypass is never used.</p>{message}{form}{builtins}<p class='notice'>Finland catalogue: {escape(catalogue)}</p></section><section>{search_form}<table><tr><th>Source</th><th>Category</th><th>Capability</th><th>Last scan</th><th>State</th><th>Manage</th></tr>{rows}</table></section>")

    @app.post("/sources")
    def add_source(name: str = Form(...), url: str = Form(...), country: str = Form(...), source_type: str = Form(...), capability: str = Form("Browser search only"), notes: str = Form("")) -> RedirectResponse:
        try:
            repository.add_source(name=name, url=url, country=country, source_type=source_type, notes=notes, capability=capability)
        except ValueError as exc:
            return RedirectResponse("/sources?notice=" + quote(str(exc)), status_code=303)
        return RedirectResponse("/sources", status_code=303)

    @app.post("/sources/{source_id}/edit")
    def edit_source(source_id: int, name: str = Form(...), url: str = Form(...), country: str = Form(...), source_type: str = Form(...), capability: str = Form(...), notes: str = Form("")) -> RedirectResponse:
        if not repository.source(source_id):
            return RedirectResponse("/sources?notice=" + quote("Source was not found."), status_code=303)
        try:
            repository.update_source(source_id, name=name, url=url, country=country, source_type=source_type, notes=notes, capability=capability)
        except ValueError as exc:
            return RedirectResponse("/sources?notice=" + quote(str(exc)), status_code=303)
        return RedirectResponse("/sources?notice=" + quote("Source settings saved."), status_code=303)

    @app.post("/sources/{source_id}/delete")
    def remove_source(source_id: int) -> RedirectResponse:
        if repository.source(source_id):
            repository.delete_source(source_id)
        return RedirectResponse("/sources?notice=" + quote("Source removed. Existing job source details were kept."), status_code=303)

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

    @app.post("/sources/{source_id}/toggle")
    def toggle_source(source_id: int) -> RedirectResponse:
        source = repository.source(source_id)
        if source:
            repository.set_source_enabled(source_id, not bool(source["enabled"]))
        return RedirectResponse("/sources", status_code=303)

    @app.get("/queue", response_class=HTMLResponse)
    def queue(notice: str = Query(default="")) -> HTMLResponse:
        queued = repository.rows("applications")
        rows = "".join(f"<tr><td>{item['id']}</td><td>{escape(str(item['queue_state']))}</td><td>{escape(str(item['status']))}</td></tr>" for item in queued) or "<tr><td colspan='3'>No prepared applications yet.</td></tr>"
        generated_cvs = repository.documents(kind="generated_cv")
        cv_options = "<option value=''>No CV selected</option>" + "".join(f"<option value='{escape(str(document['path']))}'>{escape(Path(str(document['path'])).name)}</option>" for document in generated_cvs)
        jobs = "".join(
            f"<li>{escape(str(job['title']))} — {f'{score.final_score:.0f}% eligible' if score.queue_eligible else escape('; '.join(score.hard_failures) or 'Below configured score threshold')} "
            + (f"<form style='display:inline' method='post' action='/queue/prepare/{job['id']}'><label>CV <select name='cv_path'>{cv_options}</select></label><button>Prepare safely</button></form>" if score.queue_eligible else "")
            + "</li>"
            for job in repository.rows("jobs")
            for score in [score_for_job(job)]
        )
        message = f"<p class='notice' role='status'>{escape(notice)}</p>" if notice else ""
        return _page("Application Queue", f"<section><p>Dry Run prevents final submission. Hard requirement failures and duplicate applications never enter this queue.</p>{message}<h3>Eligible jobs</h3><ul>{jobs}</ul></section><section><table><tr><th>ID</th><th>Queue state</th><th>Status</th></tr>{rows}</table></section>")

    @app.post("/queue/prepare/{job_id}")
    def prepare_queue(job_id: int, cv_path: str = Form("")) -> RedirectResponse:
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
        allowed_cv_paths = {str(document["path"]) for document in repository.documents(kind="generated_cv")}
        selected_cv = cv_path if cv_path in allowed_cv_paths else None
        repository.queue_application(job_id, language=str(job["language"]), cv_path=selected_cv)
        return RedirectResponse("/queue?notice=" + quote("Application prepared for review. Dry Run remains enabled."), status_code=303)

    @app.get("/applications", response_class=HTMLResponse)
    def applications(notice: str = Query(default="")) -> HTMLResponse:
        application_items = repository.rows("applications")
        rows = "".join(f"<tr><td>{item['id']}</td><td>{escape(str(item['status']))}</td><td>{escape(str(item['cv_path'] or 'Not selected'))}</td><td>{escape(str(item['notes'] or '—'))}</td><td><form method='post' action='/applications/{item['id']}/status'><select name='status'><option>APPLIED</option><option>APPLICATION_RECEIVED</option><option>INTERVIEW</option><option>ASSESSMENT</option><option>OFFER</option><option>REJECTED</option><option>WITHDRAWN</option></select><input name='note' placeholder='Optional note'><button>Update</button></form></td></tr>" for item in application_items) or "<tr><td colspan='5'>No applications tracked yet.</td></tr>"
        timelines = "".join(
            f"<details><summary>Application {item['id']} timeline</summary><ul>"
            + "".join(f"<li>{escape(str(event['status']))}: {escape(str(event['note']))}</li>" for event in repository.application_timeline(int(item["id"])))
            + "</ul></details>"
            for item in application_items
        ) or "<p>No application activity yet.</p>"
        evidence = "".join(
            f"<details><summary>Application {item['id']} submission evidence</summary>"
            + "".join(f"<p>{escape(str(entry['confirmation_message']))} · {escape(str(entry['confirmation_id'] or 'No reference ID'))} · <a href='{escape(str(entry['final_url']))}'>Confirmation URL</a></p>" for entry in repository.submission_evidence_for_application(int(item["id"])))
            + f"<form method='post' action='/applications/{item['id']}/evidence'><label>Confirmation URL <input name='final_url' type='url' required></label> <label>Confirmation message <input name='confirmation_message' required></label> <label>Reference ID (optional) <input name='confirmation_id'></label><button>Record evidence</button></form></details>"
            for item in application_items
        ) or "<p>No submission evidence yet.</p>"
        message = f"<p class='notice' role='status'>{escape(notice)}</p>" if notice else ""
        return _page("Applications", f"<section><p>Track status, CV used, notes, and safe submission evidence. No credentials are stored.</p>{message}<table><tr><th>ID</th><th>Status</th><th>CV used</th><th>Latest note</th><th>Action</th></tr>{rows}</table><h3>Timeline</h3>{timelines}<h3>Submission evidence</h3>{evidence}</section>")

    @app.post("/applications/{application_id}/status")
    def status_application(application_id: int, status: str = Form(...), note: str = Form("")) -> RedirectResponse:
        application = repository.application(application_id)
        allowed = {"APPLIED", "APPLICATION_RECEIVED", "INTERVIEW", "ASSESSMENT", "OFFER", "REJECTED", "WITHDRAWN", "FAILED", "NO_RESPONSE"}
        if status == "APPLIED" and application and application["status"] != "APPLIED":
            daily_limit = int(repository.setting("daily_limit") or "0")
            if not repository.can_queue_or_submit(daily_limit=daily_limit):
                return RedirectResponse("/applications?notice=" + quote("Daily application limit reached. Status was not changed."), status_code=303)
        if status in allowed and application:
            repository.update_application_status(application_id, status, note)
        return RedirectResponse("/applications", status_code=303)

    @app.post("/applications/{application_id}/evidence")
    def record_submission_evidence(
        application_id: int,
        final_url: str = Form(...),
        confirmation_message: str = Form(...),
        confirmation_id: str = Form(""),
    ) -> RedirectResponse:
        if repository.application(application_id) and confirmation_message.strip():
            try:
                repository.add_submission_evidence(
                    application_id=application_id,
                    final_url=final_url.strip(),
                    confirmation_message=confirmation_message.strip(),
                    confirmation_id=confirmation_id.strip() or None,
                    agent_provider="manual",
                )
            except ValueError:
                return RedirectResponse("/applications?notice=" + quote("Evidence must be a safe HTTP(S) confirmation without credentials."), status_code=303)
        return RedirectResponse("/applications", status_code=303)

    @app.get("/answers", response_class=HTMLResponse)
    def answers() -> HTMLResponse:
        rows = "".join(
            f"<tr><td>{escape(str(answer['category']))}</td><td>{escape(str(answer['question']))}</td><td>{escape(str(answer['value']))}</td><td>{escape(str(answer['source']))}</td><td>{_status(classify_question(str(answer['question'])), {'HIGH': 'risk', 'MEDIUM': 'review', 'LOW': 'success'}[classify_question(str(answer['question']))])}</td></tr>"
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
        usage = repository.ai_usage_summary()
        usage_text = f"AI usage: {usage['requests']} requests · {usage['input_tokens']} input tokens · {usage['output_tokens']} output tokens · {usage['cached_tokens']} cached tokens"
        return _page("Agent & System Status", f"<section><table><tr><th>Component</th><th>Status</th></tr><tr><td>Core database</td><td>Online</td></tr><tr><td>Finnish templates</td><td>Valid</td></tr><tr><td>English templates</td><td>Valid</td></tr><tr><td>AI provider</td><td>Not configured — deterministic mode active</td></tr><tr><td>Browser agent</td><td>Not configured — manual action required</td></tr></table><p>{escape(usage_text)}</p><p class='notice'>Usage remains zero in default Minimal mode. Provider adapters may record approximate usage when their API exposes it.</p></section>", path="/agent")

    @app.get("/settings", response_class=HTMLResponse)
    def settings(notice: str = Query(default="")) -> HTMLResponse:
        configs = load_configs(repository.setting("scoring_config"))
        preferences = repository.preferences()
        application_mode = repository.setting("application_mode") or "review_everything"
        ai_usage_mode = repository.setting("ai_usage_mode") or "minimal"
        work_type = str(preferences.get("work_type", "any"))
        score_inputs = "".join(
            f"<fieldset><legend>{name.replace('_', ' ').title()}</legend><label>Enabled <select name='{name}_enabled'><option value='yes'{' selected' if config.enabled else ''}>Yes</option><option value='no'{' selected' if not config.enabled else ''}>No</option></select></label> <label>{name.replace('_', ' ').title()} weight <input name='{name}_weight' type='number' min='0' max='100' value='{config.weight:g}' required></label> <label>{name.replace('_', ' ').title()} minimum <input name='{name}_minimum' type='number' min='0' max='100' value='{config.minimum:g}' required></label></fieldset>"
            for name, config in configs.items()
        )
        select_options = lambda key, choices, default: "".join(f"<option value='{value}'{' selected' if str(preferences.get(key, default)) == value else ''}>{label}</option>" for value, label in choices)
        prefs_fields = (
            f"<label>Preferred locations <input name='locations' value='{escape(str(preferences.get('locations', '')))}' placeholder='Helsinki, Vantaa'></label>"
            f"<label>Exclude locations <input name='locations_exclude' value='{escape(str(preferences.get('locations_exclude', '')))}' placeholder='Tampere'></label>"
            f"<label>Work setting <select name='work_type'>{select_options('work_type', [('any','Any'),('onsite','On-site'),('hybrid','Hybrid'),('remote','Remote')], 'any')}</select></label>"
            f"<label>Employment type <select name='employment_type'>{select_options('employment_type', [('any','Any'),('full_time','Full-time'),('part_time','Part-time'),('temporary','Temporary'),('seasonal','Seasonal')], 'any')}</select></label>"
            f"<label>Preferred shift <select name='schedule'>{select_options('schedule', [('any','Any'),('day','Day'),('evening','Evening'),('night','Night'),('weekend','Weekend')], 'any')}</select></label>"
            f"<label>Job keywords <input name='keywords' value='{escape(str(preferences.get('keywords', '')))}'></label>"
            f"<label>Search phrases to include <input name='search_terms_include' value='{escape(str(preferences.get('search_terms_include', '')))}'></label>"
            f"<label>Search phrases to exclude <input name='search_terms_exclude' value='{escape(str(preferences.get('search_terms_exclude', '')))}'></label>"
            f"<label>Job titles to include <input name='title_include' value='{escape(str(preferences.get('title_include', '')))}'></label>"
            f"<label>Job titles to exclude <input name='title_exclude' value='{escape(str(preferences.get('title_exclude', '')))}'></label>"
            f"<label>Industries to search <input name='industries' value='{escape(str(preferences.get('industries', '')))}'></label>"
            f"<label>Employers to include <input name='employer_include' value='{escape(str(preferences.get('employer_include', '')))}'></label>"
            f"<label>Employers to exclude <input name='employer_exclude' value='{escape(str(preferences.get('employer_exclude', '')))}'></label>"
            f"<label>Minimum monthly salary (€) <input name='salary_minimum' type='number' min='0' value='{escape(str(preferences.get('salary_minimum', 0)))}'></label>"
            f"<label>Search radius (km) <input name='radius_km' type='number' min='0' max='500' value='{escape(str(preferences.get('radius_km', 0)))}'></label>"
            f"<label>Include public-sector sources? <select name='include_public_sector'>{select_options('include_public_sector', [('yes','Yes'),('no','No')], 'yes')}</select></label>"
            f"<label>Include recruitment agencies? <select name='include_recruitment_agencies'>{select_options('include_recruitment_agencies', [('yes','Yes'),('no','No')], 'yes')}</select></label>"
        )
        form = f"<form class='settings-form' method='post' action='/settings'><div class='settings-group'><h3>Application controls</h3><div class='settings-fields'><label>Application mode <select name='application_mode'><option value='review_everything'{' selected' if application_mode == 'review_everything' else ''}>Review Everything</option><option value='smart_approval'{' selected' if application_mode == 'smart_approval' else ''}>Smart Approval</option><option value='autopilot'{' selected' if application_mode == 'autopilot' else ''}>Autopilot</option></select></label><label>Daily application limit <input name='daily_limit' type='number' min='0' value='{escape(repository.setting('daily_limit') or '0')}'></label><label>AI usage <select name='ai_usage_mode'><option value='minimal'{' selected' if ai_usage_mode == 'minimal' else ''}>Minimal</option><option value='balanced'{' selected' if ai_usage_mode == 'balanced' else ''}>Balanced</option><option value='quality'{' selected' if ai_usage_mode == 'quality' else ''}>Quality</option></select></label></div></div><div class='settings-group'><h3>Job preferences &amp; search</h3><p>Comma-separated values are treated as alternatives. Radius is saved for future distance-aware matching; missing posting locations are not guessed.</p><div class='settings-fields'>{prefs_fields}</div></div><div class='settings-group'><h3>Explainable scoring configuration</h3><p>Enabled dimensions are reweighted automatically. A job must meet every enabled minimum.</p><div class='settings-fields'>{score_inputs}</div></div><button>Save settings</button></form>"
        disabled = ", ".join(f"{name.replace('_', ' ').title()} is disabled" for name, config in configs.items() if not config.enabled) or "No disabled dimensions"
        cache_form = "<form method='post' action='/settings/cache/clear'><button>Clear semantic cache</button></form>"
        message = f"<p class='notice' role='status'>{escape(notice)}</p>" if notice else ""
        return _page("Settings", f"<section><p>Application mode: {escape(repository.setting('application_mode') or 'review_everything')}</p><p>Daily application limit: {escape(repository.setting('daily_limit') or '0')}</p><p>AI usage mode: {escape(repository.setting('ai_usage_mode') or 'minimal')}</p><p>Preferred locations: {escape(str(preferences.get('locations', 'Any')))}</p><p>Work type: {escape(str(preferences.get('work_type', 'any')))}</p><p>Minimum monthly salary: {escape(str(preferences.get('salary_minimum', 0)))}</p><p>{escape(disabled)}</p>{message}{form}<h3>Local data control</h3><p>Clears cached semantic interpretations only; it does not delete your profile, jobs, applications, or source documents.</p>{cache_form}</section>")

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
        locations_exclude: str = Form(""),
        employment_type: str = Form("any"),
        schedule: str = Form("any"),
        search_terms_include: str = Form(""),
        search_terms_exclude: str = Form(""),
        title_include: str = Form(""),
        title_exclude: str = Form(""),
        industries: str = Form(""),
        employer_include: str = Form(""),
        employer_exclude: str = Form(""),
        radius_km: int = Form(0),
        include_public_sector: str = Form("yes"),
        include_recruitment_agencies: str = Form("yes"),
    ) -> RedirectResponse:
        if application_mode not in {"review_everything", "smart_approval", "autopilot"} or ai_usage_mode not in {"minimal", "balanced", "quality"} or work_type not in {"any", "onsite", "hybrid", "remote"} or employment_type not in {"any", "full_time", "part_time", "temporary", "seasonal"} or schedule not in {"any", "day", "evening", "night", "weekend"} or include_public_sector not in {"yes", "no"} or include_recruitment_agencies not in {"yes", "no"} or daily_limit < 0 or salary_minimum < 0 or not 0 <= radius_km <= 500:
            return RedirectResponse("/settings?notice=" + quote("Check the application and search preference values; nothing was changed."), status_code=303)
        raw_dimensions = {
            "eligibility": (eligibility_enabled, eligibility_weight, eligibility_minimum),
            "competitive_strength": (competitive_strength_enabled, competitive_strength_weight, competitive_strength_minimum),
            "confidence": (confidence_enabled, confidence_weight, confidence_minimum),
        }
        if any(enabled not in {"yes", "no"} or not 0 <= weight <= 100 or not 0 <= minimum <= 100 for enabled, weight, minimum in raw_dimensions.values()):
            return RedirectResponse("/settings?notice=" + quote("Score values must be between 0 and 100; nothing was changed."), status_code=303)
        configs = {
            name: DimensionConfig(enabled=enabled == "yes", weight=weight, minimum=minimum)
            for name, (enabled, weight, minimum) in raw_dimensions.items()
        }
        if not any(config.enabled and config.weight > 0 for config in configs.values()):
            return RedirectResponse("/settings?notice=" + quote("Enable at least one scoring dimension with a positive weight."), status_code=303)
        repository.set_setting("application_mode", application_mode)
        repository.set_setting("daily_limit", str(daily_limit))
        repository.set_setting("ai_usage_mode", ai_usage_mode)
        repository.set_setting("scoring_config", dump_configs(configs))
        repository.save_preferences({
            "locations": locations.strip(), "locations_exclude": locations_exclude.strip(),
            "work_type": work_type, "employment_type": employment_type, "schedule": schedule,
            "keywords": keywords.strip(), "search_terms_include": search_terms_include.strip(),
            "search_terms_exclude": search_terms_exclude.strip(), "title_include": title_include.strip(),
            "title_exclude": title_exclude.strip(), "industries": industries.strip(),
            "employer_include": employer_include.strip(), "employer_exclude": employer_exclude.strip(),
            "salary_minimum": salary_minimum, "radius_km": radius_km,
            "include_public_sector": include_public_sector, "include_recruitment_agencies": include_recruitment_agencies,
        })
        return RedirectResponse("/settings?notice=" + quote("Your search and review settings were saved."), status_code=303)

    @app.post("/settings/cache/clear")
    def clear_semantic_cache() -> RedirectResponse:
        repository.clear_cache()
        return RedirectResponse("/settings?notice=" + quote("Semantic cache cleared."), status_code=303)

    @app.get("/settings/email", response_class=HTMLResponse)
    def email_settings(notice: str = Query(default="")) -> HTMLResponse:
        connection = repository.mailbox_connection()
        encrypted = bool(repository.mailbox_ciphertext())
        providers = []
        for provider, label in (("gmail", "Gmail"), ("microsoft", "Microsoft Outlook")):
            try:
                OAuthConfig.from_environment(provider)
                configured = True
            except EmailIntegrationError:
                configured = False
            state = "Connected" if connection and connection["provider"] == provider else ("Ready to connect" if configured and encrypted else "Setup needed")
            if connection and connection["provider"] == provider:
                action = ""
            elif connection:
                action = "<p>Disconnect the current mailbox before switching providers.</p>"
            else:
                action = f"<form method='post' action='/email/connect/{provider}'><button {'disabled' if not (configured and encrypted) else ''}>Connect {label}</button></form>"
            providers.append(f"<article class='metric-card'><span>{label}</span><strong>{state}</strong>{action}</article>")
        connected = f"<p class='notice'>Connected provider: {escape(str(connection['provider']).title())}. Only mailbox metadata/snippets are synced.</p><form method='post' action='/email/sync'><button>Check for application replies</button></form><form method='post' action='/email/disconnect'><button class='danger'>Disconnect and delete synced message metadata</button></form>" if connection else "<p>Connect an inbox to spot likely employer responses. Sync is read-only and checks up to 50 recent messages.</p>"
        application_options = "".join(f"<option value='{item['id']}'>Application #{item['id']} — {escape(str(item['status']))}</option>" for item in repository.rows("applications"))
        messages = repository.mailbox_messages(include_reviewed=False)
        def render_message(item: dict[str, object]) -> str:
            link = str(item["link"])
            link_parts = urlsplit(link)
            open_link = f"<a href='{escape(link)}' target='_blank' rel='noopener noreferrer'>Open in mailbox</a>" if link_parts.scheme == "https" and link_parts.hostname in {"mail.google.com", "outlook.office.com", "outlook.live.com"} else ""
            return f"<article class='metric-card'><span>{escape(str(item['received_at']))} · {escape(str(item['sender']))}</span><h3>{escape(str(item['subject']))}</h3><p>{escape(str(item['snippet']))}</p>{open_link}<form method='post' action='/email/messages/{item['id']}/confirm'><label>Related application <select name='application_id' required><option value=''>Choose one</option>{application_options}</select></label><label>Confirm outcome <select name='status'><option>APPLICATION_RECEIVED</option><option>INTERVIEW</option><option>ASSESSMENT</option><option>OFFER</option><option>REJECTED</option></select></label><button>Confirm and update tracker</button></form></article>"

        message_cards = "".join(render_message(item) for item in messages) or "<p class='empty-state'>No unreviewed likely job responses. Sync the inbox after you have applied.</p>"
        message = f"<p class='notice' role='status'>{escape(notice)}</p>" if notice else ""
        setup = "<p class='notice'>Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, MICROSOFT_CLIENT_ID, MICROSOFT_CLIENT_SECRET and SAMPOAGENT_TOKEN_ENCRYPTION_KEY in your local environment. Register the exact callback URLs shown in .env.example. Secrets are never entered in this page.</p>"
        return _page("Email", f"<section><h2>Read-only inbox connection</h2><p>Authorize only the inbox you want SampoAgent to check. The app does not send, move, or delete email. Detected replies are suggestions until you confirm them.</p>{message}{setup}<div class='metric-grid'>{''.join(providers)}</div>{connected}</section><section><h2>Review possible application replies</h2>{message_cards}</section>", path="/settings/email")

    @app.post("/email/connect/{provider}")
    def connect_email(provider: str):
        try:
            config = OAuthConfig.from_environment(provider)
            encrypt_token_payload({"setup_check": True})
        except EmailIntegrationError as exc:
            return RedirectResponse("/settings/email?notice=" + quote(str(exc)), status_code=303)
        state = secrets.token_urlsafe(32)
        verifier, challenge = create_pkce_pair()
        app.state.email_oauth_states = {key: value for key, value in app.state.email_oauth_states.items() if float(value.get("expires_at", 0)) >= time.time()}
        app.state.email_oauth_states[state] = {"provider": provider, "verifier": verifier, "expires_at": time.time() + 600}
        response = RedirectResponse(authorization_url(config, state=state, challenge=challenge), status_code=303)
        response.set_cookie("sampoagent_email_oauth_state", state, httponly=True, samesite="lax", secure=False, max_age=600, path=f"/email/callback/{provider}")
        return response

    @app.get("/email/callback/{provider}")
    def email_callback(provider: str, request: Request):
        state = request.query_params.get("state", "")
        cookie_state = request.cookies.get("sampoagent_email_oauth_state", "")
        pending = app.state.email_oauth_states.pop(state, None) if state and cookie_state and hmac.compare_digest(state, cookie_state) else None
        if not pending or pending.get("provider") != provider or float(pending.get("expires_at", 0)) < time.time():
            response = RedirectResponse("/settings/email?notice=" + quote("Email authorization expired or did not match this browser. Start again."), status_code=303)
        elif request.query_params.get("error"):
            response = RedirectResponse("/settings/email?notice=" + quote("Email authorization was cancelled."), status_code=303)
        elif not request.query_params.get("code"):
            response = RedirectResponse("/settings/email?notice=" + quote("Email provider returned no authorization code."), status_code=303)
        else:
            try:
                config = OAuthConfig.from_environment(provider)
                tokens = exchange_code(config, code=request.query_params["code"], verifier=str(pending["verifier"]))
                if not tokens.get("refresh_token"):
                    raise EmailIntegrationError("Provider did not issue offline refresh access. Re-authorize and approve continued read-only access.")
                tokens["expires_at"] = time.time() + float(tokens.get("expires_in", 3600))
                repository.save_mailbox_connection(provider, encrypt_token_payload(tokens))
                response = RedirectResponse("/settings/email?notice=" + quote("Mailbox connected securely."), status_code=303)
            except (EmailIntegrationError, TypeError, ValueError):
                response = RedirectResponse("/settings/email?notice=" + quote("Could not finish email authorization. Check local provider setup and try again."), status_code=303)
        response.delete_cookie("sampoagent_email_oauth_state", path=f"/email/callback/{provider}")
        return response

    @app.post("/email/sync")
    def sync_email() -> RedirectResponse:
        connection = repository.mailbox_connection()
        ciphertext = repository.mailbox_ciphertext()
        if not connection or not ciphertext:
            return RedirectResponse("/settings/email?notice=" + quote("Connect an email account first."), status_code=303)
        try:
            tokens = decrypt_token_payload(ciphertext)
            if float(tokens.get("expires_at", 0)) <= time.time() + 60:
                config = OAuthConfig.from_environment(str(connection["provider"]))
                refresh = str(tokens.get("refresh_token", ""))
                if not refresh:
                    raise EmailIntegrationError("Authorization expired. Reconnect the mailbox.")
                tokens = refresh_access_token(config, refresh_token=refresh)
                tokens["expires_at"] = time.time() + float(tokens.get("expires_in", 3600))
                repository.save_mailbox_connection(str(connection["provider"]), encrypt_token_payload(tokens))
            messages = fetch_recent_messages(str(connection["provider"]), str(tokens["access_token"]), limit=50)
            repository.store_mailbox_messages(str(connection["provider"]), messages)
        except (EmailIntegrationError, KeyError, TypeError, ValueError) as exc:
            return RedirectResponse("/settings/email?notice=" + quote(str(exc)), status_code=303)
        return RedirectResponse("/settings/email?notice=" + quote(f"Inbox checked. {len(messages)} possible job replies are ready for review."), status_code=303)

    @app.post("/email/disconnect")
    def disconnect_email() -> RedirectResponse:
        repository.remove_mailbox_connection()
        return RedirectResponse("/settings/email?notice=" + quote("Mailbox disconnected and synced metadata removed."), status_code=303)

    @app.post("/email/messages/{message_id}/confirm")
    def confirm_email_response(message_id: int, application_id: int = Form(...), status: str = Form(...)) -> RedirectResponse:
        try:
            repository.confirm_mailbox_response(message_id, application_id, status)
        except ValueError as exc:
            return RedirectResponse("/settings/email?notice=" + quote(str(exc)), status_code=303)
        return RedirectResponse("/settings/email?notice=" + quote("Application tracker updated from the response you confirmed."), status_code=303)

    @app.get("/cvs", response_class=HTMLResponse)
    def cvs(job_id: int | None = Query(default=None)) -> HTMLResponse:
        families = "".join(f"<option value='{family}'>{family.replace('_', ' ').title()}</option>" for family in ROLE_FAMILIES)
        custom_templates = repository.cv_templates()
        template_rows = "".join(f"<tr><td>{escape(str(template['name']))}</td><td>{escape(str(template['language']))}</td><td>{escape(str(template['role_family']))}</td><td>Metadata only</td></tr>" for template in custom_templates) or "<tr><td colspan='4'>No custom template metadata registered.</td></tr>"
        template_form = f"<form method='post' action='/cvs/templates'><label>Template name <input name='name' required></label><label>Language <select name='language'><option value='fi'>Finnish</option><option value='en'>English</option></select></label><label>Role family <select name='role_family'>{families}</select></label><label>Notes <input name='notes'></label><button>Register template metadata</button></form>"
        selected_job = repository.job(job_id) if job_id is not None else None
        profile = repository.profile() or {}
        selected_language = str(selected_job["language"] if selected_job else profile.get("locale", "en"))
        if selected_language not in {"fi", "en"}:
            selected_language = "en"
        language_options = "".join(
            f"<option value='{language}'{' selected' if language == selected_language else ''}>{label}</option>"
            for language, label in (("fi", "Finnish"), ("en", "English"))
        )
        job_context = (
            f"<p class='notice'>Generate for job: {escape(str(selected_job['company']))} — {escape(str(selected_job['title']))}. The job language selected the template language; you may still change it.</p><input type='hidden' name='job_id' value='{selected_job['id']}'>"
            if selected_job
            else ""
        )
        return _page("CVs", f"<section><p>Upload TXT, DOCX, or text-based PDF CVs. Extracted facts remain unconfirmed until reviewed. Built-in FI and EN role-family templates produce ATS-readable PDFs.</p><form method='post' action='/cvs/upload' enctype='multipart/form-data'><label>CV file <input name='file' type='file' accept='.txt,.docx,.pdf' required></label> <button>Upload and extract</button></form></section><section><h3>Generate confirmed-fact CV</h3><form method='post' action='/cvs/generate'>{job_context}<label>Language <select name='language'>{language_options}</select></label><label>Role family <select name='role_family'>{families}</select></label><label>Target company (optional) <input name='company' value='{escape(str(selected_job['company'])) if selected_job else ''}'></label><label>Filename pattern <input name='filename_pattern' value='{{first}}_{{last}}_{{role}}_{{language}}.pdf'></label><p class='notice'>Available placeholders: first, last, fullname, role, company, language.</p><button>Generate PDF</button></form></section><section><h3>Custom template metadata</h3><p>Metadata only in V1: SampoAgent records your template preference but does not alter arbitrary DOCX layouts.</p>{template_form}<table><tr><th>Name</th><th>Language</th><th>Role family</th><th>Support</th></tr>{template_rows}</table></section>")

    @app.post("/cvs/templates")
    def register_cv_template(name: str = Form(...), language: str = Form(...), role_family: str = Form(...), notes: str = Form("")) -> RedirectResponse:
        if name.strip() and language in {"fi", "en"} and role_family in ROLE_FAMILIES:
            repository.add_cv_template(name=name, language=language, role_family=role_family, notes=notes)
        return RedirectResponse("/cvs", status_code=303)

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
        repository.add_document(kind="generated_cv", path=str(path))
        download_path = "/cvs/generated/" + quote(path.name)
        return _page("CV Generated", f"<section><p>Generated: <a href='{escape(download_path)}'>{escape(path.name)}</a></p><p>ATS readability: {report.score}%</p></section>", path="/cvs")

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
