"""Professional local UI without a Node build chain."""

from html import escape
from hashlib import sha256
import hmac
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
import secrets
import sqlite3
import time
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware

from sampoagent.applications.answers import save_answer
from sampoagent.applications.urls import is_safe_public_https_url
from sampoagent.applications.workflow import classify_question
from sampoagent.careers.recommendations import OccupationRecommendation, recommend_occupations
from sampoagent.candidate.service import ingest_text_cv, read_cv_file
from sampoagent.country_packs.finland import builtin_sources
from sampoagent.db.repository import Repository
from sampoagent.data.lifecycle import create_backup_archive, encrypt_backup_archive
from sampoagent.jobs.matching import matches_preferences
from sampoagent.jobs.service import normalize_job, verification_state
from sampoagent.jobs.runner import run_discovery
from sampoagent.integrations.email_oauth import EmailIntegrationError, OAuthConfig, authorization_url, create_pkce_pair, decrypt_token_payload, encrypt_token_payload, exchange_code, fetch_send_account_identity, refresh_access_token, required_email_scope
from sampoagent.integrations.email_send import send_email_message
from sampoagent.integrations.mailbox import fetch_recent_messages
from sampoagent.integrations.outbox import EmailOutboxError, create_application_email_draft, send_approved_application_email
from sampoagent.jobs.sources import source_health
from sampoagent.scoring.engine import DimensionConfig
from sampoagent.scoring.job_score import dump_configs, evaluate_job, load_configs
from sampoagent.cv.service import ROLE_FAMILIES, check_pdf_text, generate_cv_pdf
from sampoagent.cv.ocr import OCRProviderUnavailable


NAVIGATION = [("Dashboard", "/"), ("Profile", "/profile"), ("Career Suggestions", "/careers"), ("CVs", "/cvs"), ("Jobs", "/jobs"), ("Sources", "/sources"), ("Application Queue", "/queue"), ("CAPTCHA Queue", "/captcha"), ("Applications", "/applications"), ("Email", "/settings/email"), ("Answer Bank", "/answers"), ("Analytics", "/analytics"), ("Agent", "/agent"), ("Settings", "/settings")]
PRIMARY_NAVIGATION = {"Dashboard", "Profile", "CVs", "Jobs", "Sources", "Applications", "Email", "Settings"}
NAVIGATION.insert(2, ("Application Profile", "/onboarding"))
NAVIGATION.insert(3, ("Search & Permissions", "/onboarding/ready"))
_APP_LOGGER = logging.getLogger("sampoagent.app")


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
.search-links-disclosure{{border:1px solid var(--border);border-radius:12px;background:var(--surface-1);padding:.8rem 1rem}}.search-links-disclosure>summary{{cursor:pointer;color:#d7ddf5;font-weight:750}}.search-links-disclosure>summary:focus-visible{{outline:3px solid var(--focus);outline-offset:3px;border-radius:4px}}.search-source-groups{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,240px),1fr));gap:.7rem;list-style:none;padding:0;margin:1rem 0 0}}.search-source-group{{min-width:0;border:1px solid var(--border);border-radius:10px;padding:.75rem;background:var(--surface-2);list-style:none}}.search-source-group>strong{{display:block;margin-bottom:.45rem;color:#d7ddf5}}.search-link-list{{display:grid;gap:.25rem;list-style:none;padding:0;margin:0}}.search-link-list a{{color:var(--primary-hover);text-decoration-thickness:1px;text-underline-offset:3px}}.search-link-list a:hover{{color:var(--text)}}
.dashboard-hero{{padding:clamp(1.5rem,4vw,2.5rem);background:radial-gradient(circle at 88% 15%,rgba(109,184,255,.27),transparent 27%),linear-gradient(135deg,#252057,#151b38);border-color:#4c4b85}}.dashboard-hero h2{{max-width:650px;font-size:clamp(1.6rem,3vw,2.35rem);margin:.7rem 0 .25rem}}.metric-grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1rem;margin:1rem 0}}.metric-card{{padding:1.15rem;border:1px solid var(--border);border-radius:14px;background:var(--surface-1)}}.metric-card span{{display:block;color:var(--muted);font-size:.82rem;font-weight:700}}.metric-card strong{{display:block;margin-top:.45rem;font-size:1.75rem;letter-spacing:-.04em}}.content-grid{{display:grid;grid-template-columns:1.15fr .85fr;gap:1rem}}.activity-list{{margin:0;padding-left:1.2rem;color:var(--muted)}}.next-step{{margin:.5rem 0;padding:.75rem;border-radius:10px;background:rgba(139,124,255,.1);color:#e2e6ff}}
form{{display:flex;flex-wrap:wrap;gap:.75rem;align-items:end;margin:.9rem 0}}label{{display:grid;gap:.32rem;min-width:150px;color:#d7ddf5;font-size:.86rem;font-weight:650}}input,select,button{{font:inherit;border-radius:10px;padding:.62rem .75rem}}input,select{{min-height:42px;background:#0c1020;color:var(--text);border:1px solid #414b71}}input:focus,select:focus,button:focus,a:focus{{outline:3px solid var(--focus);outline-offset:2px}}button{{border:1px solid transparent;background:linear-gradient(135deg,var(--primary),#392c89);color:white;font-weight:750;cursor:pointer;box-shadow:0 8px 18px rgba(91,91,219,.22)}}button:hover{{filter:none;box-shadow:0 0 0 2px rgba(193,184,255,.35)}}button:disabled{{opacity:.65;cursor:not-allowed}}button.secondary{{background:var(--surface-3);border-color:#485276}}button.danger{{background:linear-gradient(135deg,#98233f,#68152b)}}.notice{{color:#dbe2fc;background:rgba(109,184,255,.1);border-left:3px solid var(--focus);padding:.7rem .85rem;border-radius:8px}}.metric{{font-size:1.8rem;font-weight:800}}.status{{display:inline-flex;align-items:center;gap:.35rem;border-radius:999px;padding:.25rem .58rem;font-size:.78rem;font-weight:800;white-space:nowrap}}.status-success{{color:#a7f3d6;background:rgba(79,214,168,.14)}}.status-review{{color:#ffdc91;background:rgba(255,200,105,.14)}}.status-risk{{color:#ffb3c0;background:rgba(255,123,145,.14)}}.action-row{{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center}}.empty-state{{padding:1.5rem;text-align:center;color:var(--muted)}}.settings-form{{display:block}}.settings-group{{padding:1rem 0;border-top:1px solid var(--border)}}.settings-group:first-child{{border-top:0;padding-top:0}}.settings-group h3{{margin-bottom:.25rem}}.settings-fields{{display:flex;flex-wrap:wrap;gap:.75rem;align-items:end}}fieldset{{min-width:240px;border:1px solid var(--border);border-radius:12px;padding:.85rem}}legend{{color:var(--primary-hover);font-weight:800}}
input,select{{max-width:100%;min-width:0}}.action-link{{display:inline-flex;align-items:center;justify-content:center;min-height:42px;padding:.65rem 1rem;border-radius:10px;background:linear-gradient(135deg,var(--primary),#392c89);color:#fff;text-decoration:none;font-weight:750;box-shadow:0 8px 18px rgba(91,91,219,.22)}}.action-link:hover{{box-shadow:0 0 0 2px rgba(193,184,255,.35)}}
@media (max-width: 760px){{.app-shell{{display:block}}.sidebar{{position:static;height:auto;padding:1rem;border-right:0;border-bottom:1px solid var(--border)}}.brand{{padding:.2rem .3rem .8rem}}.sidebar nav{{display:flex;overflow-x:auto;padding-bottom:.25rem}}.nav-link{{white-space:nowrap}}.main-content{{padding:1.5rem 1rem}}section,.panel{{padding:1rem}}form{{display:grid}}label{{min-width:0}}button{{min-height:42px}}.metric-grid,.content-grid{{grid-template-columns:1fr}}}}
</style></head><body><a class="skip-link" href="#content">Skip to content</a><div class="app-shell"><aside class="sidebar"><div class="brand"><span class="brand-mark">S</span><span class="brand-copy"><strong>SampoAgent</strong><span>Career workspace</span></span></div><nav aria-label="Main navigation">{nav}</nav></aside><main class="main-content" id="content"><header class="page-header"><p class="eyebrow">Career workspace</p><h1>{escape(title)}</h1></header>{responsive_body}</main></div></body></html>""")


def _status(text: str, tone: str) -> str:
    return f'<span class="status status-{escape(tone)}">{escape(text)}</span>'


def _local_form_token_matches(request: Request, submitted: str, app: FastAPI) -> bool:
    expected = str(getattr(app.state, "local_action_token", ""))
    cookie = request.cookies.get("sampoagent_local_action", "")
    return bool(expected and cookie and submitted and hmac.compare_digest(expected, submitted) and hmac.compare_digest(expected, cookie))


def _set_local_form_cookie(response: Response, app: FastAPI) -> None:
    response.set_cookie(
        "sampoagent_local_action", str(app.state.local_action_token),
        httponly=True, samesite="strict", secure=False, path="/", max_age=86_400,
    )


def _elapsed_label(value: object) -> str:
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        seconds = max(0, int((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()))
    except (TypeError, ValueError):
        return "time unavailable"
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86_400:
        return f"{seconds // 3600} h ago"
    return f"{seconds // 86_400} d ago"


def create_app(
    database_path: str | Path = "sampoagent.db",
    *,
    demo_data: bool = False,
    storage_dir: str | Path = "application_data",
    manage_automation_worker: bool = False,
    automation_worker: object | None = None,
) -> FastAPI:
    from dotenv import load_dotenv

    load_dotenv(override=False)
    repository = Repository(database_path)
    repository.initialize()
    if demo_data:
        repository.load_demo()
    app = FastAPI(title="SampoAgent", docs_url=None, redoc_url=None)
    app.state.storage_dir = Path(storage_dir)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])
    app.state.repository = repository
    app.state.email_oauth_states = {}
    app.state.local_action_token = secrets.token_hex(32)
    if automation_worker is None and manage_automation_worker:
        from sampoagent.applications.worker_controller import AutopilotWorkerController

        automation_worker = AutopilotWorkerController(database_path, storage_dir)
    app.state.automation_worker = automation_worker
    if automation_worker is not None:
        app.router.add_event_handler("startup", automation_worker.sync)
        app.router.add_event_handler("shutdown", automation_worker.close)

    def sync_automation_worker() -> None:
        if automation_worker is not None:
            automation_worker.sync()

    @app.middleware("http")
    async def protect_local_form_posts(request: Request, call_next):
        if request.method == "POST":
            origin = request.headers.get("origin")
            fetch_site = request.headers.get("sec-fetch-site", "").casefold()
            expected_origin = f"{request.url.scheme}://{request.url.netloc}"
            if (origin is not None and not hmac.compare_digest(origin.rstrip("/"), expected_origin)) or fetch_site == "cross-site":
                return HTMLResponse("Cross-origin form submission rejected.", status_code=403)
        return await call_next(request)

    @app.middleware("http")
    async def redact_unhandled_request_errors(request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as error:
            reference = secrets.token_hex(6)
            _APP_LOGGER.error(
                "Request failed (reference=%s, error_type=%s)",
                reference,
                type(error).__name__,
            )
            return HTMLResponse(
                f"The request could not be completed. Reference: {reference}",
                status_code=500,
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            )

    def score_for_job(job: dict[str, object]) -> object:
        from sampoagent.cv.archive import role_family_for_job

        return evaluate_job(
            job=job,
            confirmed_facts=repository.confirmed_fact_values(),
            confirmed_records={
                record_type: repository.candidate_records(record_type)
                for record_type in ("experience", "education", "certificate", "licence", "language", "availability")
            },
            configs=load_configs(repository.setting("scoring_config")),
            learning_adjustment=repository.learning_adjustment(role_family_for_job(job)),
        )

    def occupation_recommendations() -> list[OccupationRecommendation]:
        taxonomy = repository.esco_occupations() if repository.esco_taxonomy_metadata() else None
        return recommend_occupations(repository.recommendation_skills(), ignored=[], taxonomy=taxonomy, country_code="FI")

    @app.get("/", response_class=HTMLResponse)
    def dashboard(notice: str = Query(default="")) -> HTMLResponse:
        worker = repository.worker_status()
        worker_status = str(worker.get("status") or "not_started")
        heartbeat = str(worker.get("last_heartbeat") or "")
        worker_status_label = "Worker has not run yet" if worker_status == "not_started" else f"Worker {worker_status} · last heartbeat {_elapsed_label(heartbeat)}"
        next_run = str(worker.get("next_run_at") or "")
        worker_result = str(worker.get("last_result") or "No worker result recorded yet.")
        worker_status_panel = (
            f"<section aria-label='Automation worker status'><h3>Automation worker</h3><p>{escape(worker_status_label)}</p>"
            f"<p>{escape(worker_result)}</p>{f'<p>Next check: {escape(next_run)}</p>' if next_run else ''}</section>"
        )
        autopilot_live = repository.setting("application_mode") == "autopilot" and repository.autopilot_authorized() and repository.setting("dry_run") == "false"
        paused = repository.setting("automation_paused") == "true"
        managed_worker_state = str(getattr(automation_worker, "state", ""))
        managed_worker_running = bool(getattr(automation_worker, "is_running", False))
        if paused:
            dry_run_status = "Automation paused"
            automation_notice = "The emergency stop is active; no new application will be submitted."
        elif autopilot_live and automation_worker is not None:
            worker_label = "running" if managed_worker_running else ("starting" if managed_worker_state == "starting" else managed_worker_state or "stopped")
            dry_run_status = f"Autopilot authorized · local worker {worker_label}"
            automation_notice = "The app-managed local worker discovers and processes eligible applications within your saved scope. CAPTCHA and unresolved items wait in their review queues."
        elif autopilot_live:
            dry_run_status = "Autopilot authorized · local worker not attached"
            automation_notice = "Run `sampoagent automate --watch` to discover and process eligible applications."
        else:
            dry_run_status = "Dry Run is on" if repository.setting("dry_run") != "false" else "Automatic submissions are not authorized"
            automation_notice = "Your data stays local. No final submission is allowed until the selected mode and authorization rules are met."
        cards = [
            ("Jobs found", repository.count("jobs")),
            ("Recommended", len(occupation_recommendations())),
            ("Application queue", repository.count("applications")),
            ("CAPTCHA tasks needing you", len(repository.captcha_tasks())),
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
            primary_action = '<a class="action-link" href="/onboarding">Set up your career workspace</a>'
        else:
            next_step = "Review confirmed facts, choose the roles that fit you, then find matching jobs in one step."
            primary_action = '<a class="action-link" href="/onboarding/ready">Review search scope &amp; permissions</a>'
        pause_form = f"<form method='post' action='/automation/pause'><input type='hidden' name='paused' value='{'false' if paused else 'true'}'><button class='{'secondary' if paused else 'danger'}'>{'Resume automation' if paused else 'Emergency stop'}</button></form>"
        notice_html = f"<p class='notice' role='status'>{escape(notice)}</p>" if notice else ""
        captcha_count = len(repository.captcha_tasks())
        captcha_notice = (
            f"<section class='notice' role='alert'>{captcha_count} CAPTCHA task{'s' if captcha_count != 1 else ''} need{'s' if captcha_count == 1 else ''} your help. "
            "<a href='/captcha'>Handle CAPTCHA tasks</a> one at a time; other eligible applications can continue automatically.</section>"
            if captcha_count else ""
        )
        body = f"""
<section class="dashboard-hero"><span class="status status-review">{escape(dry_run_status)}</span><h2>Build momentum with a clear view of every application.</h2><p>{escape(automation_notice)}</p>{notice_html}{pause_form}<a href="/captcha">Review CAPTCHA tasks</a></section>
{captcha_notice}
{worker_status_panel}
<section class="metric-grid">{card_markup}</section>
<div class="content-grid"><section class="panel"><h3>What to do next</h3><p class="next-step">{escape(next_step)}</p>{primary_action}</section><section class="panel"><h3>Recent activity</h3><ul class="activity-list">{activity}</ul></section></div>"""
        return _page("Dashboard", body, path="/")

    @app.get("/profile", response_class=HTMLResponse)
    def profile(notice: str = Query(default="")) -> HTMLResponse:
        candidate = repository.profile()
        facts = repository.rows("facts")
        fact_rows: list[str] = []
        for fact in facts:
            try:
                evidence = json.loads(str(fact.get("evidence_json") or "{}"))
            except (TypeError, ValueError):
                evidence = {}
            evidence_markup = "No text span recorded"
            if isinstance(evidence, dict) and evidence:
                location = []
                if evidence.get("page") is not None:
                    location.append(f"page {escape(str(evidence['page']))}")
                if evidence.get("line") is not None:
                    location.append(f"line {escape(str(evidence['line']))}")
                excerpt = escape(str(evidence.get("excerpt", "")))
                evidence_markup = f"{' · '.join(location)}<blockquote>{excerpt}</blockquote>" if excerpt else "Source location recorded"
            source = f"<details><summary>{escape(str(fact.get('source_id') or 'Source'))}</summary>{evidence_markup}</details>"
            fact_rows.append(
                f"<tr><td>{escape(str(fact['type']))}</td><td>{escape(str(fact['value']))}</td><td>{escape(str(fact['provenance']))}</td><td>{source}</td>"
                f"<td>{_status('Rejected', 'risk') if fact['rejected'] else (_status('Confirmed', 'success') if fact['confirmed'] else _status('Review needed', 'review'))}</td>"
                f"<td><form style='display:inline' method='post' action='/profile/facts/{fact['id']}/confirm'><button>Confirm</button></form> <form style='display:inline' method='post' action='/profile/facts/{fact['id']}/reject'><button class='danger'>Reject</button></form> <form style='display:inline' method='post' action='/profile/facts/{fact['id']}/edit'><input name='value' value='{escape(str(fact['value']))}' required><button class='secondary'>Correct</button></form> <form style='display:inline' method='post' action='/profile/facts/{fact['id']}/delete'><button class='danger'>Delete</button></form></td></tr>"
            )
        rows = "".join(fact_rows)
        record_markup: list[str] = []
        for record_type in ("experience", "education", "certificate", "licence", "language", "availability"):
            for record in repository.candidate_record_rows(record_type):
                state = str(record.get("review_state", "CONFIRMED"))
                start_date = str(record.get("start_date", ""))
                end_date = str(record.get("end_date", ""))
                date_range = f"{start_date} – {end_date or ('Present' if record.get('is_current') else '')}".strip(" –") if start_date else ""
                organization = escape(str(record.get("organization", "")))
                location = escape(str(record.get("location", "")))
                evidence = record.get("evidence", {})
                evidence_excerpt = escape(str(evidence.get("excerpt", ""))) if isinstance(evidence, dict) else ""
                evidence_html = f"<details><summary>CV evidence · {escape(str(record.get('source_id', 'local profile')))}</summary><blockquote>{evidence_excerpt or escape(str(record.get('details', '')))}</blockquote></details>" if record.get("source_id") or evidence_excerpt else ""
                details = " · ".join(value for value in (organization, location, escape(date_range), escape(str(record.get("details", "")))) if value)
                if state == "CONFLICT":
                    prior_id = int(record.get("conflict_with_record_id", 0) or 0)
                    prior = repository.candidate_record(int(prior_id)) if prior_id else None
                    prior_text = escape(str(prior.get("title", "Existing record"))) if prior else "Existing confirmed record"
                    actions = (
                        f"<p role='alert'>This CV version differs from {prior_text}. Nothing was replaced.</p>"
                        f"<form method='post' action='/profile/records/{record['id']}/resolve'><button name='decision' value='keep_existing'>Keep existing record</button> "
                        f"<button name='decision' value='use_new'>Use CV version</button> "
                        f"<button name='decision' value='add_separate'>Add as separate record</button></form>"
                    )
                elif state == "DRAFT":
                    actions = (
                        f"<form method='post' action='/profile/records/{record['id']}/confirm'><button>Confirm history record</button></form> "
                        f"<form method='post' action='/profile/records/{record['id']}/reject'><button class='danger'>Reject draft</button></form>"
                    )
                elif state == "SUPERSEDED":
                    actions = "<small>Superseded by a candidate-reviewed CV update; retained in local history.</small>"
                elif state == "REJECTED":
                    actions = "<small>Rejected CV draft; retained in local history.</small>"
                else:
                    actions = (
                        f"<details><summary>Edit or remove</summary><form method='post' action='/profile/records/{record['id']}/edit'>"
                        f"<label>Title <input name='title' value='{escape(str(record.get('title', '')), quote=True)}' required></label>"
                        f"<label>Organization <input name='organization' value='{escape(str(record.get('organization', '')), quote=True)}'></label>"
                        f"<label>Location <input name='location' value='{escape(str(record.get('location', '')), quote=True)}'></label>"
                        f"<label>Start date <input name='start_date' type='month' value='{escape(start_date, quote=True)}'></label>"
                        f"<label>End date <input name='end_date' type='month' value='{escape(end_date, quote=True)}'></label>"
                        f"<label><input type='checkbox' name='is_current' value='true'{ ' checked' if record.get('is_current') else ''}>Current role/program</label>"
                        f"<label>Details <input name='details' value='{escape(str(record.get('details', '')), quote=True)}'></label><button>Save</button></form>"
                        f"<form method='post' action='/profile/records/{record['id']}/delete'><button class='danger'>Remove record</button></form></details>"
                    )
                record_markup.append(
                    f"<tr><td>{escape(record_type.title())}</td><td>{escape(str(record.get('title', '')))}<br><small>{escape(state.title().replace('_', ' '))}</small></td>"
                    f"<td>{details}{evidence_html}{actions}</td></tr>"
                )
        record_rows = "".join(record_markup) or "<tr><td colspan='3'>No structured profile records yet.</td></tr>"
        record_form = "<form method='post' action='/profile/records'><label>Record type <select name='record_type'><option value='experience'>Experience</option><option value='education'>Education</option><option value='certificate'>Certificate</option><option value='licence'>Licence</option><option value='language'>Language</option><option value='availability'>Availability</option></select></label> <label>Title <input name='title' required></label> <label>Organization / school <input name='organization'></label> <label>Location <input name='location'></label> <label>Start date <input name='start_date' type='month'></label> <label>End date <input name='end_date' type='month'></label> <label><input type='checkbox' name='is_current' value='true'>Current role/program</label> <label>Details <input name='details'></label> <button>Add confirmed record</button></form>"
        identity_form = f"<form method='post' action='/profile/details'><label>Name <input name='name' value='{escape(candidate['name'] if candidate else '')}' required></label> <label>Email <input name='email' type='email' value='{escape(candidate.get('email', '') if candidate else '')}'></label> <label>Preferred language <select name='locale'><option value='fi'{' selected' if candidate and candidate['locale'] == 'fi' else ''}>Finnish</option><option value='en'{' selected' if not candidate or candidate['locale'] == 'en' else ''}>English</option></select></label><button>Save profile</button></form>"
        notice_html = f"<p class='notice' role='status'>{escape(notice)}</p>" if notice else ""
        return _page("Profile", f"<section><h3>Candidate knowledge profile</h3><p>CV-extracted claims remain drafts until you confirm them. Where available, each claim links back to its page and line in the source document.</p>{notice_html}{identity_form}<form method='post' action='/profile/skills'><label>Add confirmed skill <input name='skill' required></label> <button>Add skill</button></form><p><a href='/onboarding/ready'>Review search scope, recommendations, and permissions</a></p></section><section><h3>Structured background</h3><p>Directly entered records are confirmed facts. CV-imported history remains excluded from generated CVs and application forms until reviewed. If a later CV differs from confirmed history, choose which version to keep.</p>{record_form}<table><tr><th>Type</th><th>Title / state</th><th>Details and review</th></tr>{record_rows}</table></section><section><table><tr><th>Type</th><th>Value</th><th>Provenance</th><th>Source evidence</th><th>State</th><th>Review</th></tr>{rows}</table></section>")

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
    def add_candidate_record(
        record_type: str = Form(...), title: str = Form(...), details: str = Form(""),
        organization: str = Form(""), location: str = Form(""), start_date: str = Form(""),
        end_date: str = Form(""), is_current: bool = Form(False),
    ) -> RedirectResponse:
        allowed = {"experience", "education", "certificate", "licence", "language", "availability"}
        if record_type in allowed and title.strip():
            try:
                record_id = repository.add_candidate_record(record_type, {
                    "title": title.strip(), "details": details.strip(), "organization": organization.strip(),
                    "location": location.strip(), "start_date": start_date.strip(), "end_date": end_date.strip(),
                    "is_current": is_current,
                })
            except ValueError as error:
                return RedirectResponse("/profile?notice=" + quote("Could not save this profile entry. Check the fields and try again."), status_code=303)
            repository.add_confirmed_fact(fact_type=record_type, value=title.strip(), source_id=f"candidate_record:{record_id}")
        return RedirectResponse("/profile", status_code=303)

    @app.post("/profile/records/{record_id}/edit")
    def edit_candidate_record(
        record_id: int, title: str = Form(...), details: str = Form(""), organization: str = Form(""),
        location: str = Form(""), start_date: str = Form(""), end_date: str = Form(""),
        is_current: bool = Form(False),
    ) -> RedirectResponse:
        try:
            repository.update_candidate_record(
                record_id, title=title, details=details, organization=organization, location=location,
                start_date=start_date, end_date=end_date, is_current=is_current,
            )
        except ValueError as error:
            return RedirectResponse("/profile?notice=" + quote("Could not update this profile entry. Refresh the page and try again."), status_code=303)
        return RedirectResponse("/profile", status_code=303)

    @app.post("/profile/records/{record_id}/confirm")
    def confirm_candidate_record(record_id: int) -> RedirectResponse:
        try:
            repository.resolve_candidate_record(record_id, decision="confirm")
        except ValueError:
            pass
        return RedirectResponse("/profile", status_code=303)

    @app.post("/profile/records/{record_id}/reject")
    def reject_candidate_record(record_id: int) -> RedirectResponse:
        try:
            repository.resolve_candidate_record(record_id, decision="reject")
        except ValueError:
            pass
        return RedirectResponse("/profile", status_code=303)

    @app.post("/profile/records/{record_id}/resolve")
    def resolve_candidate_record(record_id: int, decision: str = Form(...)) -> RedirectResponse:
        try:
            repository.resolve_candidate_record(record_id, decision=decision)
        except ValueError:
            return RedirectResponse("/profile?notice=" + quote("The history changed or the selected resolution is no longer available."), status_code=303)
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
        recommendations = occupation_recommendations()
        targets = repository.target_occupations()
        active_targets = {(str(target["title_en"]), str(target["title_fi"])) for target in targets if target["enabled"]}

        def render_qualification_advisories(item: OccupationRecommendation) -> str:
            return "".join(
                "<p>" + escape(advisory.message)
                + " <a href='" + escape(advisory.source_url, quote=True)
                + "' target='_blank' rel='noopener noreferrer'>" + escape(advisory.source_title)
                + "</a> (checked " + escape(advisory.checked_on) + "; not an eligibility decision)</p>"
                for advisory in item.qualification_advisories
            ) or "No country-pack alert"

        content = "".join(
            f"<tr><td>{escape(item.title_en)} / {escape(item.title_fi)}</td><td>{item.score}%</td><td>{escape(', '.join(item.supporting_facts))}</td><td>{escape(', '.join(item.missing_requirements)) or 'No unrepresented essential ESCO skill links'}</td><td>{render_qualification_advisories(item)}</td><td>{'Active target' if (item.title_en, item.title_fi) in active_targets else f'''<form method='post' action='/careers/targets'><input type='hidden' name='title_en' value='{escape(item.title_en)}'><input type='hidden' name='title_fi' value='{escape(item.title_fi)}'><button>Make target</button></form>'''}</td></tr>"
            for item in recommendations
        ) or "<tr><td colspan='6'>Add confirmed skills to receive deterministic recommendations.</td></tr>"
        metadata = repository.esco_taxonomy_metadata()
        if metadata:
            taxonomy_note = (
                f"<p class='notice'>{escape(metadata['attribution'])} ESCO {escape(metadata['version'])}; "
                f"language files: {escape(metadata['languages'])}. {escape(metadata['modified'])} "
                f"Reuse terms: {escape(metadata['license_statement'])}. "
                f"{escape(metadata['quality_notice'])} "
                f"<a href='{escape(metadata['source_url'])}'>Source and reuse terms</a>.</p>"
            )
        else:
            taxonomy_note = (
                "<p class='notice'>Showing the small built-in starter catalogue. For a broad occupation and skill index, "
                "download an ESCO CSV package from the European Commission, then import it locally with "
                "<code>sampoagent import-esco &lt;directory&gt; --version 1.2.1 --languages fi en</code>. "
                "The package is not bundled or uploaded by SampoAgent.</p>"
            )
        profiles = "".join(
            f"<tr><td>{escape(str(profile['name']))}</td><td>{escape(str(profile['notes']))}</td><td>{'Active' if profile['enabled'] else 'Inactive'}</td><td><form style='display:inline' method='post' action='/careers/profiles/{profile['id']}/toggle'><button>{'Deactivate' if profile['enabled'] else 'Activate'}</button></form> <details><summary>Edit</summary><form method='post' action='/careers/profiles/{profile['id']}/edit'><label>Name <input name='name' value='{escape(str(profile['name']))}' required></label><label>Notes <input name='notes' value='{escape(str(profile['notes']))}'></label><button>Save</button></form></details> <form method='post' action='/careers/profiles/{profile['id']}/delete'><button class='danger'>Delete</button></form></td></tr>"
            for profile in repository.rows("career_profiles")
        ) or "<tr><td colspan='4'>No career profiles yet.</td></tr>"
        target_rows = "".join(
            f"<tr><td>{escape(str(target['title_en']))} / {escape(str(target['title_fi']))}</td><td>{'Active' if target['enabled'] else 'Inactive'}</td><td><form method='post' action='/careers/targets/{target['id']}/toggle'><button>{'Deactivate' if target['enabled'] else 'Activate'}</button></form><form method='post' action='/careers/targets/{target['id']}/delete'><button class='danger'>Remove</button></form></td></tr>"
            for target in targets
        ) or "<tr><td colspan='3'>No approved targets yet.</td></tr>"
        form = "<form method='post' action='/careers/profiles'><label>Career profile <input name='name' required></label> <label>Notes <input name='notes'></label> <button>Add profile</button></form>"
        return _page("Career Suggestions", f"<section><p>Recommendations are never activated automatically. Choose Make target only for occupations you want to pursue. ESCO essential skill links are matching evidence, not legal licence or education requirements. Country-pack notices below are sourced prompts to check the relevant authority, never an automated eligibility decision.</p>{taxonomy_note}<table><tr><th>Role</th><th>Match</th><th>Supporting facts</th><th>Essential skill links not yet represented</th><th>Country qualification checks</th><th>Target</th></tr>{content}</table></section><section><h3>My target occupations</h3><p>These are your explicit, local target choices. You can pause any target without deleting it.</p><table><tr><th>Role</th><th>State</th><th>Action</th></tr>{target_rows}</table></section><section><h3>My career profiles</h3><p>Add more than one career direction; changes affect only your local preferences.</p>{form}<table><tr><th>Name</th><th>Notes</th><th>State</th><th>Actions</th></tr>{profiles}</table></section>")

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
        from sampoagent.applications.runner import _active_verified_job

        def job_row(job: dict[str, object]) -> str:
            job = repository.job(int(job["id"])) or job
            score = score_for_job(job)
            override = repository.job_override(int(job["id"]))
            override_text = "User review override active" if override else ""
            verification_record = repository.job_verification(int(job["id"]))
            verification_detail = escape(str(verification_record["method"]).replace("_", " ").title()) if verification_record else "No verification evidence"
            verification_active = _active_verified_job(job)
            verification_status = "RE-VERIFY REQUIRED" if job["verification_state"] == "VERIFIED" and not verification_active else str(job["verification_state"])
            application_url = str(job["application_url"])
            posting_link = f"<a href='{escape(application_url)}' target='_blank' rel='noopener noreferrer'>Open employer posting</a>" if is_safe_public_https_url(application_url) else "Unsafe application destination; manual correction required."
            verification_action = ""
            if not verification_active and job["verification_state"] != "EXPIRED" and is_safe_public_https_url(application_url):
                verification_action = f"<form method='post' action='/jobs/{job['id']}/verify'><label><input type='checkbox' name='reviewed_current' value='yes' required> I reviewed the live posting, employer, deadline and application destination</label><button>Verify for 24 hours</button></form>"
            action = ""
            if not score.queue_eligible and not score.hard_blocked and not override:
                action = f"<form method='post' action='/jobs/{job['id']}/override'><input name='note' placeholder='Why review this?' required><button>Override for review</button></form>"
            language_form = f"<form method='post' action='/jobs/{job['id']}/language'><label>Language: {escape(str(job['language']))}<select name='language'><option value='fi'{' selected' if job['language'] == 'fi' else ''}>fi</option><option value='en'{' selected' if job['language'] == 'en' else ''}>en</option></select></label><button>Set</button></form>"
            return f"<tr><td>{escape(str(job['title']))}</td><td>{escape(str(job['company']))}</td><td>{escape(str(job['location']))}</td><td>{escape(verification_status)} · {verification_detail}<br>{posting_link}{verification_action}<br>{language_form}</td><td>{'Blocked: ' + escape('; '.join(score.hard_failures)) if score.hard_blocked else f'{score.final_score:.0f}%'} </td><td>{escape(' · '.join(score.explanations))}<br>{escape(override_text)}{action}</td></tr>"

        rows = "".join(job_row(job) for job in job_rows) or "<tr><td colspan='6'>No jobs match your current preferences.</td></tr>"
        form = "<form method='post' action='/jobs/import'><label>Title <input name='title' required></label> <label>Employer <input name='company' required></label> <label>Location <input name='location' required></label> <label>Description <input name='description' required></label> <label>Application URL <input name='application_url' type='url' required></label> <button class='secondary'>Add a job manually</button></form>"
        last_run = repository.latest_discovery_run()
        summary = "No search run yet. Choose a target occupation or add an explicit search keyword to begin." if not last_run else str(last_run["summary"])
        search_preview = run_discovery_preview(repository)
        query_groups: dict[int, tuple[str, list[str]]] = {}
        for query in search_preview.queries[:30]:
            source_name, links = query_groups.setdefault(query.source_id, (query.source_name, []))
            label = f"{escape(query.phrase)}{(' · ' + escape(query.location)) if query.location else ''}"
            links.append(
                f"<li><a href='{escape(query.search_url, quote=True)}' title='Search Google for jobs at {escape(source_name, quote=True)}' target='_blank' rel='noopener noreferrer'>{label}</a></li>"
            )
        if query_groups:
            source_groups = "".join(
                f"<li class='search-source-group'><strong>{escape(source_name)}</strong><ul class='search-link-list'>{''.join(links)}</ul></li>"
                for source_name, links in query_groups.values()
            )
            query_count = sum(len(links) for _, links in query_groups.values())
            search_links = (
                f"<details class='search-links-disclosure'><summary>{query_count} searches across {len(query_groups)} sources</summary>"
                f"<ul class='search-source-groups'>{source_groups}</ul></details>"
            )
        else:
            search_links = "<p>Choose a target role to generate searches. <a href='/careers'>Review career suggestions</a>, or add a search phrase in Settings.</p>"
        search_action = (
            "<form method='post' action='/jobs/discover'><button>Find matching jobs</button></form>"
            if search_preview.terms
            else "<p class='notice'>Choose a target role to generate searches. <a href='/careers'>Review career suggestions</a> or add an explicit search keyword in Settings.</p>"
        )
        message = f"<p class='notice' role='status'>{escape(notice)}</p>" if notice else ""
        latest_results = repository.discovery_source_results(int(last_run["id"])) if last_run else []
        result_cards = "".join(
            f"<article class='metric-card'><strong>{escape(str(result['source_name']))}</strong><p>{escape(str(result['status']).replace('_', ' ').title())} · {int(result['jobs_found'])} found · {int(result['imported_count'])} added</p><p>{escape(str(result['message']))}</p></article>"
            for result in latest_results
        ) or ""
        result_section = f"<section><h2>Last source results</h2><div class='metric-grid'>{result_cards}</div></section>" if result_cards else ""
        body = f"<section class='dashboard-hero'><p>Search from your chosen career goals</p><h2>Find matching jobs without typing every role by hand.</h2><p>Search terms come only from target occupations, active career profiles and explicit search preferences. Experience and skills inform suggestions but never activate a search. Public feed results can be added automatically; other sites open as personalized searches.</p>{search_action}<p class='notice'>{escape(summary)}</p>{message}</section><section><div class='panel-heading'><h2>Personalized search links</h2><a href='/sources'>Manage sources</a></div>{search_links}</section>{result_section}<section><h2>Matching jobs</h2><p>Scores use confirmed facts only. Every imported result keeps its source and verification status for your review.</p><table><tr><th>Title</th><th>Employer</th><th>Location</th><th>Verification</th><th>Score</th><th>Why</th></tr>{rows}</table>{form}</section>"
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
        notice = (
            "Choose a target occupation or add an explicit search keyword before discovery."
            if report.status == "no_search_terms"
            else f"Search finished: {report.jobs_found} found, {report.imported_count} added, {report.duplicates_count} duplicates."
        )
        return RedirectResponse("/jobs?notice=" + quote(notice), status_code=303)

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

    @app.post("/jobs/{job_id}/verify")
    def verify_job(job_id: int, reviewed_current: str = Form(...)) -> RedirectResponse:
        if reviewed_current != "yes":
            return RedirectResponse("/jobs?notice=" + quote("Confirm that you reviewed the current employer posting before enabling automation."), status_code=303)
        try:
            repository.mark_job_user_reviewed(job_id, reviewed_current=True)
        except ValueError as error:
            return RedirectResponse("/jobs?notice=" + quote("Could not verify this listing. Reload the job and try again."), status_code=303)
        return RedirectResponse("/jobs?notice=" + quote("Listing verified for 24 hours. It may now be considered by the worker within your saved scope."), status_code=303)

    @app.get("/sources", response_class=HTMLResponse)
    def sources(notice: str = Query(default=""), q: str = Query(default="")) -> HTMLResponse:
        last_run = repository.latest_discovery_run()
        latest_results = {int(result["source_id"]): result for result in repository.discovery_source_results(int(last_run["id"])) if result["source_id"] is not None} if last_run else {}
        source_states = repository.discovery_source_states()
        all_source_rows = repository.rows("job_sources")
        search_key = q.strip().casefold()
        visible_source_rows = [source for source in all_source_rows if not search_key or search_key in " ".join(str(source.get(key, "")) for key in ("name", "url", "source_type", "country", "capability", "notes")).casefold()]
        capabilities = ("Browser search only", "RSS/Atom feed", "JSON Feed", "Job Market Finland API", "Scrapling public page")
        def capability_options(selected: str) -> str:
            return "".join(f"<option value='{escape(value)}{' selected' if value == selected else ''}'>{escape(value)}</option>" for value in capabilities)
        def source_scan_label(source: dict[str, object]) -> str:
            source_id = int(source["id"])
            state = source_states.get(source_id, {})
            result = latest_results.get(source_id, {})
            status = str(result.get("status") or state.get("last_status") or "Not checked")
            parts = [status]
            if result:
                parts.append(f"{int(result.get('jobs_found', 0))} found / {int(result.get('imported_count', 0))} added")
            if state.get("last_checked_at"):
                parts.append(f"last checked {_elapsed_label(state['last_checked_at'])}")
            retry_at = state.get("next_attempt_at")
            if retry_at and repository.source_is_in_backoff(source_id):
                parts.append(f"retry after {retry_at}")
            return "<br>".join(escape(part) for part in parts)
        def source_terms_link(source: dict[str, object]) -> str:
            terms_url = str(source.get("terms_url") or "")
            if not terms_url:
                return ""
            return f"<br><a href='{escape(terms_url)}' target='_blank' rel='noopener noreferrer'>Open terms</a>"
        rows = "".join(
            f"<tr><td><strong>{escape(str(source['name']))}</strong><br>{escape(str(source['notes']))}</td><td>{escape(str(source['source_type']))}<br>{escape(str(source['country']))}</td><td>{escape(str(source['capability']))}<br>{escape(source_health(str(source['url']), str(source['capability'])))}</td><td>{source_scan_label(source)}</td><td>{'Active' if source['enabled'] else 'Inactive'} <form style='display:inline' method='post' action='/sources/{source['id']}/toggle'><button class='secondary'>{'Disable' if source['enabled'] else 'Enable'}</button></form></td><td><details><summary>Edit source</summary><form method='post' action='/sources/{source['id']}/edit'><label>Name <input name='name' value='{escape(str(source['name']))}' required></label><label>URL <input name='url' type='url' value='{escape(str(source['url']))}' required></label><label>Country <input name='country' value='{escape(str(source['country']))}' required></label><label>Type <select name='source_type'><option>{escape(str(source['source_type']))}</option><option>job board</option><option>public-sector board</option><option>recruitment agency</option><option>employer career site</option><option>custom</option></select></label><label>How to search <select name='capability'>{capability_options(str(source['capability']))}</select></label><label>Notes <input name='notes' value='{escape(str(source['notes']))}'></label><button>Save source</button></form><form method='post' action='/sources/{source['id']}/delete' onsubmit=\"return confirm('Remove this source? Previously imported jobs keep their source details.')\"><button class='danger'>Remove source</button></form></details></td></tr>"
            for source in visible_source_rows
        ) or "<tr><td colspan='6'>No matching sources. Adjust the search or add a source.</td></tr>"
        selector_field = "<label>Job card CSS selector <input name='listing_selector' maxlength='200' placeholder=\"li.job-card\"></label>"
        rows = rows.replace("</select></label><label>Notes <input name='notes' value=", f"</select></label>{selector_field}<label>Notes <input name='notes' value=")
        form = f"<form method='post' action='/sources'><label>Name <input name='name' required></label> <label>URL <input name='url' type='url' placeholder='https://example.org/careers' required></label> <label>Country <input name='country' value='Finland' required></label> <label>Type <select name='source_type'><option>job board</option><option>public-sector board</option><option>recruitment agency</option><option>employer career site</option><option>custom</option></select></label> <label>How to search <select name='capability'>{capability_options('Browser search only')}</select></label> {selector_field}<label>Notes <input name='notes'></label> <button>Add source</button></form>"
        catalogue = " · ".join(source.name for source in builtin_sources())
        builtins = "<form method='post' action='/sources/builtin'><button>Add missing Finland sources</button></form>"
        policy_rows = "".join(
            f"<tr><td>{escape(str(source['name']))}<br>{escape(str(source['url']))}</td>"
            f"<td>{('Reviewed ' + escape(str(source.get('terms_reviewed_at') or ''))) if source.get('terms_reviewed') else 'Not reviewed · public-page retrieval blocked'}"
            f"{source_terms_link(source)}</td>"
            f"<td><form method='post' action='/sources/{source['id']}/terms'><label>Terms-of-use URL <input name='terms_url' type='url' value='{escape(str(source.get('terms_url') or ''))}'></label>"
            f"<label><input type='checkbox' name='terms_reviewed' value='yes'{' checked' if source.get('terms_reviewed') else ''}> I reviewed this policy and permit public-listing-only retrieval</label>"
            f"<button>Save policy review</button></form></td></tr>"
            for source in visible_source_rows if str(source.get('capability', '')).casefold() == 'scrapling public page'
        ) or "<tr><td colspan='3'>No public-page sources configured.</td></tr>"
        message = f"<p class='notice' role='status'>{escape(notice)}</p>" if notice else ""
        search_form = f"<form method='get' action='/sources'><label>Find a source <input name='q' value='{escape(q)}' placeholder='Search by name, type, country'></label><button class='secondary'>Search</button></form><p>{len(visible_source_rows)} of {len(all_source_rows)} sources</p>"
        return _page("Sources", f"<section><h2>Control what SampoAgent can search</h2><p>Scrapling uses its standard parser on explicitly selected public pages; configure a job-card CSS selector if the page has no JobPosting JSON-LD. Requests respect robots.txt and stop on access denials. Public-page retrieval requires an explicitly recorded review of the source's terms URL; SampoAgent does not interpret terms automatically. When unclear, keep it browser-only. Automatic sources have a persisted 60-second minimum polling interval and errors trigger longer backoff. Protected or interactive websites remain browser-only; CAPTCHA/proxy bypass is never used.</p>{message}{form}{builtins}<p class='notice'>Finland catalogue: {escape(catalogue)}</p></section><section><h3>Public-page terms review</h3><p>This is your record that you reviewed the applicable policy; it is not legal advice or automatic terms analysis.</p><table><tr><th>Source</th><th>Review state</th><th>Record</th></tr>{policy_rows}</table></section><section>{search_form}<table><tr><th>Source</th><th>Category</th><th>Capability</th><th>Last scan</th><th>State</th><th>Manage</th></tr>{rows}</table></section>")

    @app.post("/sources")
    def add_source(name: str = Form(...), url: str = Form(...), country: str = Form(...), source_type: str = Form(...), capability: str = Form("Browser search only"), notes: str = Form(""), listing_selector: str = Form("")) -> RedirectResponse:
        try:
            repository.add_source(name=name, url=url, country=country, source_type=source_type, notes=notes, capability=capability, listing_selector=listing_selector)
        except ValueError as exc:
            return RedirectResponse("/sources?notice=" + quote("Could not add this source. Check the source settings and try again."), status_code=303)
        return RedirectResponse("/sources", status_code=303)

    @app.post("/sources/{source_id}/edit")
    def edit_source(source_id: int, name: str = Form(...), url: str = Form(...), country: str = Form(...), source_type: str = Form(...), capability: str = Form(...), notes: str = Form(""), listing_selector: str = Form("")) -> RedirectResponse:
        if not repository.source(source_id):
            return RedirectResponse("/sources?notice=" + quote("Source was not found."), status_code=303)
        try:
            repository.update_source(source_id, name=name, url=url, country=country, source_type=source_type, notes=notes, capability=capability, listing_selector=listing_selector)
        except ValueError as exc:
            return RedirectResponse("/sources?notice=" + quote("Could not update this source. Check the source settings and try again."), status_code=303)
        return RedirectResponse("/sources?notice=" + quote("Source settings saved."), status_code=303)

    @app.post("/sources/{source_id}/terms")
    def record_source_terms(source_id: int, terms_url: str = Form(""), terms_reviewed: str = Form("no")) -> RedirectResponse:
        source = repository.source(source_id)
        if not source or str(source.get("capability", "")).casefold() != "scrapling public page":
            return RedirectResponse("/sources?notice=" + quote("Public-page source was not found."), status_code=303)
        try:
            repository.update_source(
                source_id,
                name=str(source["name"]), url=str(source["url"]), country=str(source["country"]),
                source_type=str(source["source_type"]), notes=str(source.get("notes") or ""),
                capability=str(source["capability"]), listing_selector=str(source.get("listing_selector") or ""),
                terms_url=terms_url, terms_reviewed=terms_reviewed == "yes",
            )
        except ValueError as error:
            return RedirectResponse("/sources?notice=" + quote("Could not save the terms review. Check the URL and try again."), status_code=303)
        updated = repository.source(source_id) or {}
        notice = "Terms review recorded. A source URL or terms URL change requires a fresh review." if updated.get("terms_reviewed") else "Source terms review is required again before automatic page retrieval."
        return RedirectResponse("/sources?notice=" + quote(notice), status_code=303)

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
        queue_rows = []
        for item in queued:
            captcha_action = f"<form method='post' action='/queue/{item['id']}/captcha'><button>Move to CAPTCHA queue</button></form>" if item["queue_state"] == "READY" else "—"
            job = repository.job(int(item["job_id"])) or {}
            wait_reason = str(item.get("notes") or "—") if item["queue_state"] in {"WAITING_USER", "DO_NOT_RETRY", "SUBMITTING", "PREPARING"} else "—"
            queue_rows.append(
                f"<tr><td>{item['id']}</td><td>{escape(str(job.get('title') or 'Job unavailable'))} — {escape(str(job.get('company') or ''))}</td>"
                f"<td>{escape(str(item['queue_state']))}<br>{escape(str(item['status']))}</td>"
                f"<td>{escape(_elapsed_label(item.get('created_at')))}</td><td>{escape(wait_reason)}</td><td>{captcha_action}</td></tr>"
            )
        rows = "".join(queue_rows) or "<tr><td colspan='6'>No prepared applications yet.</td></tr>"
        generated_cvs = repository.cv_archives()
        cv_options = "<option value=''>Auto select or create a matching CV</option>" + "".join(f"<option value='{escape(str(document['path']))}'>{escape(Path(str(document['path'])).name)} · {escape(str(document['role_family']))} · {document['fit_score']}%</option>" for document in generated_cvs)
        jobs = "".join(
            f"<li>{escape(str(job['title']))} — {f'{score.final_score:.0f}% eligible' if score.queue_eligible else escape('; '.join(score.hard_failures) or 'Below configured score threshold')} "
            + (f"<form style='display:inline' method='post' action='/queue/prepare/{job['id']}'><label>CV <select name='cv_path'>{cv_options}</select></label><button>Prepare safely</button></form>" if score.queue_eligible else "")
            + "</li>"
            for job in repository.rows("jobs")
            for score in [score_for_job(job)]
        )
        message = f"<p class='notice' role='status'>{escape(notice)}</p>" if notice else ""
        dry_run_label = "Dry Run prevents external submission." if repository.setting("dry_run") != "false" else "Full Autopilot is enabled only for scoped jobs with resolved required answers."
        return _page("Application Queue", f"<section><p>{dry_run_label} Hard requirement failures and duplicate applications never enter this queue.</p>{message}<h3>Eligible jobs</h3><ul>{jobs}</ul></section><section><table><tr><th>ID</th><th>Job</th><th>State</th><th>Queue age</th><th>Wait reason</th><th>Access challenge</th></tr>{rows}</table></section>")

    @app.get("/captcha", response_class=HTMLResponse)
    def captcha_queue() -> HTMLResponse:
        tasks = repository.captcha_tasks()
        cards = []
        active_task = next((task for task in tasks if task["state"] == "IN_PROGRESS"), None)
        next_waiting_task = next(
            (task for task in tasks if task["state"] == "WAITING_USER"), None
        ) if active_task is None else None
        for task in tasks:
            if task["state"] == "IN_PROGRESS":
                start = "<span class='status status-review'>In progress · finish before starting another</span>"
                instructions = "<p>Automatic activity is paused at this challenge. Complete its CAPTCHA yourself, then record the outcome before handling the next item.</p>"
                official_url = str(task.get("official_url", ""))
                application_link = (
                    f"<a href='{escape(official_url)}' target='_blank' rel='noopener noreferrer'>Open official application</a>"
                    if official_url
                    else "<span class='status status-review'>No safe public employer application link is available</span>"
                )
            elif next_waiting_task is not None and task["id"] == next_waiting_task["id"]:
                start = f"<form method='post' action='/captcha/{task['id']}/start'><button>Handle this one</button></form>"
                instructions = "<p>This is the next waiting CAPTCHA task. Start it to open its official application page for manual handling.</p>"
                application_link = ""
            else:
                start = "<span class='status status-review'>Waiting for the current CAPTCHA task to finish</span>"
                instructions = "<p>This challenge is queued. Its official application page stays closed until it becomes the active task.</p>"
                application_link = ""
            finish = f"<form method='post' action='/captcha/{task['id']}/finish'><label>Outcome <select name='outcome'><option value='submitted'>I submitted and saw confirmation</option><option value='not_submitted'>Not submitted</option><option value='skip'>Skip this job</option></select></label><label>Employer confirmation (required if submitted) <input name='confirmation_message' maxlength='300'></label><button>Record outcome</button></form>" if task["state"] == "IN_PROGRESS" else ""
            cards.append(f"<article class='metric-card'><h3>{escape(str(task['title']))} — {escape(str(task['company']))}</h3><p>Application {task['application_id']} · {escape(str(task['state']))}</p>{instructions}{application_link}{start}{finish}</article>")
        body = "<section><h2>CAPTCHA tasks</h2><p>Blocked applications stay here until you handle them individually. SampoAgent will not solve or bypass CAPTCHA.</p>" + ("".join(cards) or "<p class='empty-state'>No CAPTCHA tasks are waiting.</p>") + "</section>"
        return _page("CAPTCHA Queue", body)

    @app.post("/queue/{application_id}/captcha")
    def queue_captcha(application_id: int) -> RedirectResponse:
        application = repository.application(application_id)
        if not application:
            return RedirectResponse("/queue?notice=" + quote("Application was not found."), status_code=303)
        job = repository.job(int(application["job_id"]))
        if not job:
            return RedirectResponse("/queue?notice=" + quote("Job was not found."), status_code=303)
        try:
            repository.hold_for_captcha(application_id)
        except ValueError as error:
            return RedirectResponse("/queue?notice=" + quote("Could not move this application to manual CAPTCHA handling. Refresh the queue and try again."), status_code=303)
        return RedirectResponse("/captcha", status_code=303)

    @app.post("/captcha/{task_id}/start")
    def start_captcha(task_id: int) -> RedirectResponse:
        try:
            repository.begin_captcha_task(task_id)
        except ValueError as error:
            return RedirectResponse("/captcha?notice=" + quote("Could not start this CAPTCHA task. Refresh the queue and try again."), status_code=303)
        return RedirectResponse("/captcha", status_code=303)

    @app.post("/captcha/{task_id}/finish")
    def finish_captcha(task_id: int, outcome: str = Form(...), confirmation_message: str = Form("")) -> RedirectResponse:
        try:
            repository.finish_captcha_task(task_id, outcome=outcome, confirmation_message=confirmation_message)
        except ValueError as error:
            return RedirectResponse("/captcha?notice=" + quote("Could not save the CAPTCHA outcome. Check the outcome and try again."), status_code=303)
        return RedirectResponse("/captcha", status_code=303)

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
        from sampoagent.applications.packages import copy_cv_to_application, prepare_job_cv

        try:
            prepared = prepare_job_cv(repository, job, app.state.storage_dir, requested_path=cv_path)
            choice = prepared.choice
        except (ValueError, OSError) as error:
            return RedirectResponse("/queue?notice=" + quote("Could not prepare a verified CV. Check the selected archive and try again."), status_code=303)
        selected_cv = str(choice.path)
        archive_notes = prepared.note
        repository.add_document(kind="generated_cv", path=selected_cv, checksum=choice.checksum)
        repository.archive_cv(
            path=selected_cv, checksum=choice.checksum, language=choice.language,
            role_family=choice.role_family, source_job_id=job_id, fit_score=choice.score,
            text_check_score=choice.text_check_score if choice.text_check_performed else None,
            strategy=choice.strategy,
        )
        try:
            application_id = repository.queue_application(job_id, language=str(job["language"]), cv_path=selected_cv, notes=archive_notes)
            application_cv = copy_cv_to_application(selected_cv, app.state.storage_dir, application_id)
            repository.add_document(kind="application_cv", path=str(application_cv), checksum=choice.checksum)
            repository.update_application_cv_path(application_id, str(application_cv))
        except (ValueError, OSError):
            return RedirectResponse("/queue?notice=" + quote("Could not store the application package safely. Check local storage and try again."), status_code=303)
        return RedirectResponse("/queue?notice=" + quote("Application prepared for review; the tailored CV package is stored."), status_code=303)

    @app.get("/applications", response_class=HTMLResponse)
    def applications(request: Request, notice: str = Query(default="")) -> HTMLResponse:
        repository.recover_interrupted_email_sends()
        application_items = repository.rows("applications")
        row_markup = []
        reviews = []
        email_cards = []
        application_question_cards = []
        csrf = str(app.state.local_action_token)
        questions_by_application: dict[int, list[dict[str, object]]] = {}
        for question in repository.application_questions():
            questions_by_application.setdefault(int(question["application_id"]), []).append(question)
        for application_id, questions in questions_by_application.items():
            first = questions[0]
            question_forms = []
            for question in questions:
                label = escape(str(question.get("label", "Required employer question")))
                description = str(question.get("description") or "").strip()
                description_markup = f"<p>{escape(description)}</p>" if description else ""
                state = str(question.get("state", "PENDING"))
                current_value = str(question.get("answer_value", "")) if state == "ANSWERED" else ""
                kind = str(question.get("kind", "text"))
                constraints = question.get("constraints", {})
                if not isinstance(constraints, dict):
                    constraints = {}
                control_id = f"application-question-{int(question['id'])}"
                label_markup = f"<label for='{control_id}'>{label}</label>"
                try:
                    maximum = min(4000, max(1, int(constraints.get("maxlength", 4000))))
                except (TypeError, ValueError):
                    maximum = 4000
                length_attrs = f" maxlength='{maximum}'"
                if "minlength" in constraints:
                    try:
                        minimum = min(4000, max(0, int(constraints["minlength"])))
                        length_attrs = f" minlength='{minimum}'" + length_attrs
                    except (TypeError, ValueError):
                        pass
                if kind in {"select", "radio"}:
                    options = question.get("options", [])
                    if not isinstance(options, list) or not options:
                        continue
                    control = f"<select id='{control_id}' name='value' required><option value=''>Choose an answer</option>" + "".join(
                        f"<option value='{escape(str(option), quote=True)}'{' selected' if str(option) == current_value else ''}>{escape(str(option))}</option>" for option in options
                    ) + "</select>"
                    if kind == "radio":
                        label_markup = ""
                        control = f"<fieldset><legend>{label}</legend>" + "".join(
                            f"<label><input id='{control_id}-{index}' type='radio' name='value' value='{escape(str(option), quote=True)}'{' checked' if str(option) == current_value else ''} required>{escape(str(option))}</label>" for index, option in enumerate(options)
                        ) + "</fieldset>"
                elif kind == "textarea":
                    control = f"<textarea id='{control_id}' name='value'{length_attrs} required>{escape(current_value)}</textarea>"
                elif kind in {"text", "email", "tel", "number", "date"}:
                    input_type = "text" if kind == "text" else kind
                    attrs = "".join(
                        f" {key}='{escape(str(constraints[key]), quote=True)}'"
                        for key in ("min", "max", "step", "pattern")
                        if key in constraints and not (key == "step" and "step_base" in constraints and "min" not in constraints)
                    )
                    control = f"<input id='{control_id}' name='value' type='{input_type}' value='{escape(current_value, quote=True)}'{attrs}{length_attrs} required autocomplete='off'>"
                else:
                    continue
                question_forms.append(
                    f"<form class='panel settings-form' method='post' action='/applications/{application_id}/questions/{int(question['id'])}'>"
                    f"{label_markup}{description_markup}{control}"
                    f"<p><strong>Application-only answer.</strong> This response is not added to reusable candidate records.</p>"
                    f"<label><input type='checkbox' name='confirmed' value='yes' required> I confirm this answer is accurate for this application. It will not be reused for other applications.</label>"
                    f"<input type='hidden' name='csrf_token' value='{escape(csrf, quote=True)}'><button>{'Update' if state == 'ANSWERED' else 'Save'} answer for this application</button></form>"
                )
            if question_forms:
                application_question_cards.append(
                    f"<article class='metric-card'><h3>{escape(str(first.get('job_title') or 'Job'))} · {escape(str(first.get('employer') or 'Employer'))}</h3>"
                    f"<p>These are required fields from the employer form. Review the prompt as untrusted employer text and answer truthfully. Responses are private to this application and never added to the reusable answer bank.</p>"
                    + "".join(question_forms) + "</article>"
                )
        for item in application_items:
            resume_form = f"<form method='post' action='/applications/{item['id']}/resume'><button class='secondary'>Resume after resolving</button></form>" if item["status"] in {"NEEDS_USER", "NEEDS_AUTH", "NOT_SUBMITTED"} else ""
            review = repository.application_review(int(item["id"]))
            if review and review["state"] == "WAITING":
                package = review["package"]
                job = package.get("job", {}) if isinstance(package, dict) else {}
                answers = package.get("answers", []) if isinstance(package, dict) else []
                cv = package.get("cv", {}) if isinstance(package, dict) else {}
                answer_rows = "".join(
                    f"<tr><th scope='row'>{escape(str(answer.get('label', answer.get('field_id', 'Field'))))}</th><td>{escape(str(answer.get('value') if answer.get('value') is not None else 'Not answered'))}</td><td>{escape(str(answer.get('source', '')))}</td></tr>"
                    for answer in answers if isinstance(answer, dict)
                )
                attachment = (
                    f"<p>Attachment: {escape(str(cv.get('name') or 'None'))}</p><p>SHA-256: <code>{escape(str(cv.get('sha256') or 'None'))}</code></p>"
                    if isinstance(cv, dict) else "<p>No CV attachment recorded.</p>"
                )
                reviews.append(
                    f"<article class='panel' aria-labelledby='review-{item['id']}'><h3 id='review-{item['id']}'>Application {item['id']} · Awaiting your approval</h3>"
                    f"<p>{escape(str(job.get('title', 'Job')))} · {escape(str(job.get('company', 'Employer')))}</p>"
                    f"<p><a href='{escape(str(job.get('application_url', '')))}' rel='noreferrer'>Open verified job posting</a></p>"
                    f"<p>Exact application answers and attachment are shown below. Approval is bound to this package; any changed field, listing detail, or CV invalidates it.</p>"
                    f"<div class='table-wrap'><table><thead><tr><th>Form question</th><th>Prepared answer</th><th>Source</th></tr></thead><tbody>{answer_rows}</tbody></table></div>{attachment}"
                    f"<p>Package SHA-256: <code>{escape(str(review['package_hash']))}</code></p>"
                    f"<form method='post' action='/applications/{item['id']}/approve'><input type='hidden' name='package_hash' value='{escape(str(review['package_hash']))}'><button>Approve this exact application</button></form></article>"
                )
            notes = str(item["notes"] or "—")
            conflict_links = (
                "<br><small>Compare both sources before resuming: "
                "<a href='/profile'>Review CV facts</a> · "
                "<a href='/answers'>Review questionnaire answers</a></small>"
                if "conflicting candidate answers" in notes.casefold()
                else ""
            )
            row_markup.append(
                f"<tr><td>{item['id']}</td><td>{escape(str(item['status']))}</td><td>{escape(str(item['cv_path'] or 'Not selected'))}</td><td>{escape(notes)}{conflict_links}</td>"
                f"<td><form method='post' action='/applications/{item['id']}/status'><select name='status'><option>APPLIED</option><option>APPLICATION_RECEIVED</option><option>INTERVIEW</option><option>ASSESSMENT</option><option>OFFER</option><option>REJECTED</option><option>WITHDRAWN</option></select><input name='note' placeholder='Optional note'><button>Update</button></form>{resume_form}</td></tr>"
            )
            job = repository.job(int(item["job_id"]))
            if not job:
                continue
            outbox = repository.email_outbox_for_application(int(item["id"]))
            if outbox and outbox["state"] == "READY":
                try:
                    package = decrypt_token_payload(repository.email_outbox_payload_ciphertext(int(item["id"])) or "")
                except EmailIntegrationError:
                    email_cards.append(f"<article class='metric-card'><h3>Email draft for application {item['id']}</h3><p>The encrypted draft cannot be opened. No email was sent; check the local encryption key.</p></article>")
                    continue
                send_form = (
                    f"<form method='post' action='/email/applications/{item['id']}/send'>"
                    f"<input type='hidden' name='package_hash' value='{escape(str(outbox['package_hash']))}'>"
                    f"<input type='hidden' name='csrf_token' value='{csrf}'>"
                    "<label><input type='checkbox' name='send_confirmation' value='yes' required> I reviewed the exact recipient, message and attachment above and authorize this one email to be sent now.</label>"
                    "<button class='danger'>Send this exact email once</button></form>"
                    f"<form method='post' action='/email/applications/{item['id']}/cancel'><input type='hidden' name='csrf_token' value='{csrf}'><button class='secondary'>Discard unsent draft</button></form>"
                )
                email_cards.append(
                    f"<article class='metric-card'><h3>Review email application #{item['id']}: {escape(str(package.get('role', '')))} · {escape(str(package.get('employer', '')))}</h3>"
                    f"<p>To: <strong>{escape(str(package.get('recipient', '')))}</strong></p>"
                    f"<p>From: <strong>{escape(str(package.get('sender_email', '')))}</strong> · {escape(str(package.get('provider', '')).title())} ({'Google-verified' if package.get('sender_address_status') == 'verified' else 'provider-reported address'})</p>"
                    f"<p>Recipient source: {escape('current verified listing' if package.get('recipient_source') == 'verified_listing' else 'candidate-confirmed current listing')} · rule <code>{escape(str(package.get('recipient_cue_id', '')))}</code> · snapshot <code>{escape(str(package.get('verified_snapshot_hash', '')))}</code></p>"
                    f"<p>Subject: {escape(str(package.get('subject', '')))}</p>"
                    f"<p>Message:</p><pre>{escape(str(package.get('body', '')))}</pre>"
                    f"<p>Attachment: {escape(str(package.get('attachment_name', '')))} · SHA-256 <code>{escape(str(package.get('attachment_sha256', '')))}</code></p>"
                    f"<p>Package SHA-256: <code>{escape(str(outbox.get('package_hash', '')))}</code>. This draft is local and encrypted; nothing has been sent.</p>"
                    f"{send_form}</article>"
                )
            elif outbox and outbox["state"] != "CANCELLED":
                try:
                    sent_package = decrypt_token_payload(repository.email_outbox_payload_ciphertext(int(item["id"])) or "")
                    sent_identity = (
                        f"<p>From: {escape(str(sent_package.get('sender_email', '')))} · To: {escape(str(sent_package.get('recipient', '')))} · source: {escape('verified listing' if sent_package.get('recipient_source') == 'verified_listing' else 'candidate-confirmed listing')}</p>"
                        f"<p>Package SHA-256: <code>{escape(str(outbox.get('package_hash', '')))}</code></p>"
                    )
                except EmailIntegrationError:
                    sent_identity = "<p>Encrypted sender/recipient details are unavailable; check the local encryption key.</p>"
                email_cards.append(
                    f"<article class='metric-card'><h3>Email application #{item['id']} · {escape(str(outbox['state']))}</h3>"
                    f"{sent_identity}"
                    f"<p>{escape(str(outbox.get('message') or 'No delivery confirmation is available.'))}</p></article>"
                )
            elif item["status"] == "QUEUED" and item["queue_state"] == "READY":
                job_link = str(job.get("application_url", ""))
                if repository.mail_send_connection() and Path(str(item.get("cv_path") or "")).is_file():
                    email_cards.append(
                        f"<article class='metric-card'><h3>Prepare email application #{item['id']}</h3>"
                        f"<p>{escape(str(job.get('company', 'Employer')))} · {escape(str(job.get('title', 'Job')))}. Enter only an address shown on the current employer posting; confirm below. This creates a local encrypted draft only.</p>"
                        f"<p><a href='{escape(job_link)}' target='_blank' rel='noopener noreferrer'>Open reviewed employer listing</a></p>"
                        f"<form method='post' action='/email/applications/{item['id']}/draft'>"
                        f"<input type='hidden' name='csrf_token' value='{csrf}'>"
                        "<label>Employer email from the current posting <input name='recipient' type='email' maxlength='320' required autocomplete='off'></label>"
                        "<label><input type='checkbox' name='confirm_recipient_from_posting' value='yes' required> I copied this address from the current employer listing.</label>"
                        "<button>Prepare encrypted draft · does not send</button></form></article>"
                    )
        rows = "".join(row_markup) or "<tr><td colspan='5'>No applications tracked yet.</td></tr>"
        timelines = "".join(
            f"<details><summary>Application {item['id']} timeline</summary><ul>"
            + "".join(f"<li>{escape(str(event['status']))}: {escape(str(event['note']))}</li>" for event in repository.application_timeline(int(item["id"])))
            + "</ul></details>"
            for item in application_items
        ) or "<p>No application activity yet.</p>"
        evidence = "".join(
            f"<details><summary>Application {item['id']} submission evidence</summary>"
            + "".join(
                (
                    f"<p>Manual CAPTCHA report · {escape(str(entry['confirmation_message']))} · no confirmation URL was captured.</p>"
                    if entry["agent_provider"] == "manual_captcha"
                    else f"<p>{escape(str(entry['confirmation_message']))} · {escape(str(entry['confirmation_id'] or 'No reference ID'))} · <a href='{escape(str(entry['final_url']))}'>Confirmation URL</a></p>"
                )
                for entry in repository.submission_evidence_for_application(int(item["id"]))
            )
            + f"<form method='post' action='/applications/{item['id']}/evidence'><label>Confirmation URL <input name='final_url' type='url' required></label> <label>Confirmation message <input name='confirmation_message' required></label> <label>Reference ID (optional) <input name='confirmation_id'></label><button>Record evidence</button></form></details>"
            for item in application_items
        ) or "<p>No submission evidence yet.</p>"
        message = f"<p class='notice' role='status'>{escape(notice)}</p>" if notice else ""
        review_markup = "".join(reviews)
        email_cards_markup = "".join(email_cards) or "<p class='empty-state'>No email drafts need review.</p>"
        questions_markup = "".join(application_question_cards) or "<p class='empty-state'>No required application answers need your input.</p>"
        response = _page("Applications", f"<section><p>Track status, CV used, notes, and safe submission evidence. No credentials are stored.</p>{message}{review_markup}<h2>Required answers</h2>{questions_markup}<table><tr><th>ID</th><th>Status</th><th>CV used</th><th>Latest note</th><th>Action</th></tr>{rows}</table><h3>Email application outbox</h3><p>Every outgoing message requires separate send-only OAuth permission and confirmation of the exact recipient, body and PDF. Provider acceptance is not proof of delivery. An uncertain result is never automatically retried.</p>{email_cards_markup}<h3>Timeline</h3>{timelines}<h3>Submission evidence</h3>{evidence}</section>")
        _set_local_form_cookie(response, app)
        return response

    @app.post("/applications/{application_id}/questions/{question_id}")
    def answer_application_question(
        application_id: int,
        question_id: int,
        request: Request,
        value: str = Form(""),
        confirmed: str = Form(""),
        csrf_token: str = Form(""),
    ) -> RedirectResponse:
        if not _local_form_token_matches(request, csrf_token, app):
            notice = "The local form token was invalid. Refresh Applications and try again."
        elif confirmed != "yes":
            notice = "Confirm that your answer is accurate for this application before saving it."
        else:
            try:
                repository.answer_application_question(application_id, question_id, value, confirmed=True)
                sync_automation_worker()
                notice = "Answer saved only to this application. Its existing mode and safety checks still control the next step."
            except ValueError:
                notice = "Could not save this answer. The question may be stale, unsupported, or no longer editable. Refresh Applications and review the current form."
        return RedirectResponse("/applications?notice=" + quote(notice), status_code=303)

    @app.post("/applications/{application_id}/approve")
    def approve_application(application_id: int, package_hash: str = Form(...)) -> RedirectResponse:
        if not 1 <= len(package_hash) <= 128:
            return RedirectResponse("/applications?notice=" + quote("Invalid application package approval token."), status_code=303)
        approved = repository.approve_application_review(application_id, package_hash)
        notice = "This exact application package was approved and returned to the local worker queue." if approved else "The package changed or is no longer awaiting approval. Review the current version before approving."
        return RedirectResponse("/applications?notice=" + quote(notice), status_code=303)

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

    @app.post("/applications/{application_id}/resume")
    def resume_application(application_id: int) -> RedirectResponse:
        resumed = repository.resume_application(application_id)
        notice = "Application returned to the local worker queue." if resumed else "This application cannot be resumed; check its status, CAPTCHA task and submission history."
        return RedirectResponse("/applications?notice=" + quote(notice), status_code=303)

    @app.post("/automation/pause")
    def pause_automation(paused: str = Form(...)) -> RedirectResponse:
        if paused not in {"true", "false"}:
            return RedirectResponse("/?notice=" + quote("Invalid automation state."), status_code=303)
        repository.set_setting("automation_paused", paused)
        sync_automation_worker()
        notice = "Emergency stop enabled." if paused == "true" else "Automation resumed; the saved grant and current limits still apply."
        return RedirectResponse("/?notice=" + quote(notice), status_code=303)

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
    def answers(notice: str = Query(default="")) -> HTMLResponse:
        state_labels = {
            "CONFIRMED": ("Confirmed", "success"),
            "DECLINED": ("Declined — never reused", "review"),
            "UNKNOWN": ("Unknown — needs an answer", "review"),
            "CONFLICT": ("Conflicting answers", "risk"),
            "EXPIRED": ("Expired — never reused", "risk"),
            "NEEDS_RECONFIRMATION": ("Needs reconfirmation", "review"),
            "NON_REUSABLE": ("Application-specific", "review"),
            "DRAFT": ("Draft — never reused", "review"),
        }
        row_items = []
        for answer in repository.answers():
            risk = classify_question(str(answer["question"]))
            state, tone = state_labels.get(str(answer.get("answer_state", "DRAFT")), ("Not reusable", "review"))
            scope_type = str(answer.get("scope_type", "GLOBAL"))
            scope_value = (
                str(answer.get("scope_country", ""))
                if scope_type == "COUNTRY"
                else str(answer.get("scope_employer", ""))
                if scope_type == "EMPLOYER"
                else "All employers / countries"
            )
            scope = f"{scope_type.title()}: {scope_value}" if scope_value else scope_type.title()
            validity = f"<br><small>Valid until {escape(str(answer['valid_until']))}</small>" if answer.get("valid_until") else ""
            provenance = f"<br><small>{escape(str(answer.get('source_ref') or ''))} · {escape(str(answer.get('confirmed_at') or 'Not confirmed'))}</small>"
            state_html = _status(state, tone)
            if answer.get("answer_state") == "NEEDS_RECONFIRMATION":
                state_html += "<br><a href='/onboarding?section=review'>Review onboarding answers</a>"
            row_items.append(
                f"<tr><td>{escape(str(answer['category']))}</td><td>{escape(str(answer['question']))}</td>"
                f"<td style='white-space:pre-wrap'>{escape(str(answer['value']))}</td><td>{escape(str(answer['source']))}{provenance}</td>"
                f"<td>{escape(str(answer.get('question_id') or 'Unmapped'))}</td><td>{escape(scope)}{validity}</td>"
                f"<td>{state_html}</td><td>{_status(risk, {'HIGH': 'risk', 'MEDIUM': 'review', 'LOW': 'success'}[risk])}</td></tr>"
            )
        rows = "".join(row_items) or "<tr><td colspan='8'>No saved answers yet.</td></tr>"
        form = (
            "<form method='post' action='/answers'><label>Category <select name='category'><option value='FACT'>Fact</option><option value='PREFERENCE'>Preference</option><option value='MOTIVATION'>Motivation</option></select></label> "
            "<label>Question <input name='question' required></label> <label>Answer <input name='value' required></label> "
            "<label>Reuse scope <select name='scope_type'><option value=''>Question default</option><option value='EMPLOYER'>This employer only (supported mapped questions)</option></select></label> "
            "<label>Country scope (if applicable) <input name='scope_country' autocomplete='country-name'></label> "
            "<label>Employer scope (required for employer-only reuse) <input name='scope_employer'></label> <label>Valid until <input name='valid_until' type='date'></label> "
            "<label>Source <select name='source'><option value='USER_CONFIRMED'>User confirmed</option><option value='AI_GENERATED'>AI generated draft</option></select></label><button>Save answer</button></form>"
        )
        warning = f"<p role='status'>{escape(notice)}</p>" if notice else ""
        return _page(
            "Answer Bank",
            f"<section><p>Only mapped, confirmed, in-scope, unexpired answers can be reused. Employer-only reuse is currently limited to salary, notice period, start date and shifts, and requires an exact employer match. Vacancy-specific motivation, assessment, demographic and privacy prompts are never stored as reusable profile answers. Declined, unknown, conflicting or unmapped answers stay out of automatic forms; high-risk questions always require your intervention.</p>{warning}{form}<table><tr><th>Category</th><th>Question</th><th>Answer</th><th>Provenance</th><th>Stable field</th><th>Scope / validity</th><th>Reuse state</th><th>Risk</th></tr>{rows}</table></section>",
        )

    @app.post("/answers")
    def add_answer(
        category: str = Form(...),
        question: str = Form(...),
        value: str = Form(...),
        source: str = Form(...),
        scope_type: str = Form(""),
        scope_country: str = Form(""),
        scope_employer: str = Form(""),
        valid_until: str = Form(""),
    ) -> RedirectResponse:
        if category in {"FACT", "PREFERENCE", "MOTIVATION"} and source in {"USER_CONFIRMED", "AI_GENERATED"} and question.strip() and value.strip():
            try:
                save_answer(
                    repository,
                    category=category,
                    question=question.strip(),
                    value=value.strip(),
                    source=source,
                    scope_type=scope_type.strip() or None,
                    scope_country=scope_country.strip(),
                    scope_employer=scope_employer.strip(),
                    valid_until=valid_until.strip() or None,
                )
            except ValueError as exc:
                return RedirectResponse("/answers?notice=" + quote("Could not save this answer. Check its value and scope, then try again."), status_code=303)
        return RedirectResponse("/answers", status_code=303)

    @app.get("/analytics", response_class=HTMLResponse)
    def analytics() -> HTMLResponse:
        metrics = repository.analytics()
        learning = repository.learning_summary()
        learning_rows = "".join(f"<tr><td>{escape(str(item['role_family']))}</td><td>{item['outcomes']}</td><td>{item['positive_outcomes']}</td><td>{repository.learning_adjustment(str(item['role_family'])):+d}</td></tr>" for item in learning) or "<tr><td colspan='4'>No confirmed outcomes yet.</td></tr>"
        return _page("Analytics", f"<section><p>Jobs discovered: {metrics['jobs_discovered']} · Applications: {metrics['applications']} · Interviews: {metrics['interviews']} · Offers: {metrics['offers']}</p><p>Interview rate: {metrics['interview_rate']}% · Response rate: {metrics['response_rate']}%</p><p>Metrics describe recorded outcomes, not hire probability.</p></section><section><h2>Role matching feedback</h2><p>Only candidate-confirmed employer responses update this small, explainable ranking adjustment. It does not change factual claims or hard requirements.</p><table><tr><th>Role family</th><th>Recorded outcomes</th><th>Interviews / assessments / offers</th><th>Score adjustment</th></tr>{learning_rows}</table></section>")

    @app.get("/agent", response_class=HTMLResponse)
    def agent() -> HTMLResponse:
        usage = repository.ai_usage_summary()
        usage_text = f"AI usage: {usage['requests']} requests · {usage['input_tokens']} input tokens · {usage['output_tokens']} output tokens · {usage['cached_tokens']} cached tokens"
        return _page("Agent & System Status", f"<section><table><tr><th>Component</th><th>Status</th></tr><tr><td>Core database</td><td>Online</td></tr><tr><td>Finnish templates</td><td>Valid</td></tr><tr><td>English templates</td><td>Valid</td></tr><tr><td>AI provider</td><td>Not configured — deterministic mode active</td></tr><tr><td>Browser agent</td><td>Not configured — manual action required</td></tr></table><p>{escape(usage_text)}</p><p class='notice'>Usage remains zero in default Minimal mode. Provider adapters may record approximate usage when their API exposes it.</p></section>", path="/agent")

    @app.get("/settings", response_class=HTMLResponse)
    def settings(notice: str = Query(default="")) -> HTMLResponse:
        csrf = str(app.state.local_action_token)
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
        dry_run = repository.setting("dry_run") != "false"
        ocr_provider = repository.setting("ocr_provider") or "environment"
        ocr_options = "".join(
            f"<option value='{value}'{' selected' if ocr_provider == value else ''}>{label}</option>"
            for value, label in (("environment", "Use local environment configuration"), ("none", "Off · manual text review"), ("tesseract", "Local Tesseract OCR"))
        )
        autopilot_authorized = repository.autopilot_authorized()
        email_send_autopilot_authorized = repository.email_send_autopilot_authorized()
        email_send_connection = repository.mail_send_connection()
        paused = repository.setting("automation_paused") == "true"
        send_email_ack_disabled = " disabled" if not email_send_connection else ""
        email_send_scope_copy = (
            "Separate email Autopilot is off. Connect and select the send account in Email settings first."
            if not email_send_connection else
            "Optional separate grant: Full Autopilot may create and send an encrypted application package only when the current verified posting contains an explicit email-application instruction with exactly one nearby address. It is limited to the selected default sender, same roles, sources, preferences and daily cap, expires with the 30-day Autopilot grant, and never sends follow-ups or accepts offers. Missing/ambiguous addresses, unsupported languages, facts, CVs or permissions hold that job without browser fallback. Revoke by unchecking this box, pausing automation, changing scope, or changing/disconnecting the selected account."
        )
        form = f"<form class='settings-form' method='post' action='/settings'><div class='settings-group'><h3>Application controls</h3><div class='settings-fields'><label>Application mode <select name='application_mode'><option value='review_everything'{' selected' if application_mode == 'review_everything' else ''}>Review Everything</option><option value='smart_approval'{' selected' if application_mode == 'smart_approval' else ''}>Smart Approval · review each exact package</option><option value='autopilot'{' selected' if application_mode == 'autopilot' else ''}>Full Autopilot · no per-job prompts</option></select></label><label>Daily application limit <input name='daily_limit' type='number' min='0' value='{escape(repository.setting('daily_limit') or '0')}'></label><label>Dry Run <select name='dry_run'><option value='true'{' selected' if dry_run else ''}>On · prepare only, no final submission</option><option value='false'{' selected' if not dry_run else ''}>Off · allow authorized external submissions</option></select></label><label>Emergency stop <select name='automation_paused'><option value='false'{' selected' if not paused else ''}>Running when worker is started</option><option value='true'{' selected' if paused else ''}>Paused · block new submissions</option></select></label><label>AI usage <select name='ai_usage_mode'><option value='minimal'{' selected' if ai_usage_mode == 'minimal' else ''}>Minimal</option><option value='balanced'{' selected' if ai_usage_mode == 'balanced' else ''}>Balanced</option><option value='quality'{' selected' if ai_usage_mode == 'quality' else ''}>Quality</option></select></label></div><p>Smart Approval shows the complete answers and CV checksum, then waits for approval of that exact package. Full Autopilot can submit within the separate 30-day grant, saved job preferences, and daily limit.</p><p>Next/Continue steps may save candidate information page by page; if a later page needs review, earlier entries may already be saved. Smart Approval and Dry Run stop before entering candidate data on multi-step forms.</p><label><input type='checkbox' name='autopilot_ack' value='yes'{' checked' if autopilot_authorized else ''}> I authorize Full Autopilot to submit eligible applications without per-job prompts for 30 days, within the saved preferences and daily limit, using confirmed information. Next/Continue steps may save candidate information page by page.</label><p>CAPTCHA, sign-in, unknown or conflicting required answers, high-risk/legal declarations, expired/unverified listings, and unsupported forms always wait for you. Changing candidate facts, answers, role/source preferences or the daily limit invalidates this grant. The localhost app manages its worker while open; install the optional browser extra and Chromium for form handling.</p><p>Availability: automation runs only while your machine, the app and your interactive user session are available. Existing queued work resumes on the next polling cycle after wake or network recovery, after authorization is checked again; discovery still follows source cooldowns. Uncertain submit or email attempts are never retried. SampoAgent does not install an operating-system login startup task.</p><h3>Separate email-send Autopilot</h3><p>{email_send_scope_copy}</p><label><input type='checkbox' name='email_send_autopilot_ack' value='yes'{' checked' if email_send_autopilot_authorized else ''}{send_email_ack_disabled}> I separately authorize automatic sending of eligible email application packages when the current verified posting explicitly gives one unambiguous application address, for up to 30 days. Ambiguous/missing address, sender, facts, CV or consent holds that job; no per-job browser/email fallback is attempted.</label></div><div class='settings-group'><h3>Scanned CV OCR</h3><p>OCR is optional and runs locally. OCR-derived text remains unconfirmed and retains its PDF page evidence. If no provider is available, SampoAgent will stop and ask you to use text entry.</p><label>OCR provider <select name='ocr_provider'>{ocr_options}</select></label></div><div class='settings-group'><h3>Job preferences &amp; search</h3><p>Comma-separated values are treated as alternatives. Radius is saved for future distance-aware matching; missing posting locations are not guessed.</p><div class='settings-fields'>{prefs_fields}</div></div><div class='settings-group'><h3>Explainable scoring configuration</h3><p>Enabled dimensions are reweighted automatically. A job must meet every enabled minimum.</p><div class='settings-fields'>{score_inputs}</div></div><button>Save settings</button></form>"
        disabled = ", ".join(f"{name.replace('_', ' ').title()} is disabled" for name, config in configs.items() if not config.enabled) or "No disabled dimensions"
        cache_form = "<form method='post' action='/settings/cache/clear'><button>Clear semantic cache</button></form>"
        message = f"<p class='notice' role='status'>{escape(notice)}</p>" if notice else ""
        message += "<p>Full Autopilot requires you to explicitly select at least one target occupation or career profile, or add an explicit search term. <a href='/careers'>Review career suggestions</a>; each queued job is checked against this scope.</p>"
        learning_sharing_enabled = repository.setting("codex_learning_summary_enabled") == "true"
        learning_sharing_form = (
            "<section class='settings-group'><h3>Codex learning summary</h3>"
            "<p>Off by default. If enabled, an explicitly invoked local command may expose only aggregate counts for candidate-confirmed interview, assessment, offer, rejection, and no-response outcomes after a five-outcome minimum. It excludes profile facts, names, employers, job titles, locations, free text, CV contents, paths, and checksums. The summary is shown to the active Codex task only when this command is run; it does not start a background task.</p>"
            f"<form method='post' action='/settings/codex-learning'><label><input type='checkbox' name='enabled' value='yes'{ ' checked' if learning_sharing_enabled else ''}> Allow the active Codex task to read this de-identified outcome summary</label><button>Save learning-summary preference</button></form>"
            f"<p role='status'>Codex learning-summary sharing is {'enabled' if learning_sharing_enabled else 'disabled'}.</p></section>"
        )
        data_lifecycle = (
            "<section class='settings-group'><h3>Local data backup, restore &amp; deletion</h3>"
            "<p>Your export contains the local database, CVs, generated and archived CVs, and application packages. It excludes browser login cookies. Mailbox token ciphertext remains encrypted in the database; keep the separate SAMPOAGENT_TOKEN_ENCRYPTION_KEY safe because it is not included. Restoring requires signing into employer sites again.</p>"
            "<p>Backups are encrypted before download. Backups are not deleted automatically after download; protect the passphrase separately, because it cannot be recovered if lost.</p>"
            "<form method='post' action='/settings/data/backup'><label>Backup passphrase <input type='password' name='passphrase' minlength='12' autocomplete='new-password' required></label><label>Confirm passphrase <input type='password' name='passphrase_confirmation' minlength='12' autocomplete='new-password' required></label><button>Download local backup / data export</button></form>"
            "<p>To restore, stop SampoAgent, then run: <code>sampoagent restore --database &lt;database&gt; --storage-dir &lt;application_data&gt; --archive &lt;backup.zip&gt; --confirm \"RESTORE LOCAL SAMPOAGENT DATA\"</code></p>"
            "<p>To permanently erase the selected local database and SampoAgent-managed files, stop SampoAgent and run: <code>sampoagent erase-local-data --database &lt;database&gt; --storage-dir &lt;application_data&gt; --confirm \"ERASE ALL LOCAL SAMPOAGENT DATA\"</code>. Unrelated files in the storage directory are left untouched.</p>"
            "</section>"
        )
        activity_details_count = repository.activity_details_needing_redaction()
        activity_count_description = "entry contains" if activity_details_count == 1 else "entries contain"
        activity_cleanup_form = (
            "<section class='settings-group'><h3>Older activity-log details</h3>"
            f"<p>{activity_details_count} older activity {activity_count_description} stored detail text. This action permanently replaces only those detail fields with a generic privacy note; event types and timestamps remain. It does not redact application timelines or other candidate records, does not alter downloaded backups, and cannot be undone.</p>"
            f"<form method='post' action='/settings/privacy/redact-activity'><input type='hidden' name='csrf_token' value='{escape(csrf)}'>"
            "<label>Type REDACT to permanently replace older detail text <input name='confirmation' autocomplete='off' required></label>"
            f"<button class='danger'{ ' disabled' if activity_details_count == 0 else ''}>Redact {activity_details_count} older activity details</button></form></section>"
        )
        response = _page("Settings", f"<section><p>Application mode: {escape(repository.setting('application_mode') or 'review_everything')}</p><p>Daily application limit: {escape(repository.setting('daily_limit') or '0')}</p><p>AI usage mode: {escape(repository.setting('ai_usage_mode') or 'minimal')}</p><p>Preferred locations: {escape(str(preferences.get('locations', 'Any')))}</p><p>Work type: {escape(str(preferences.get('work_type', 'any')))}</p><p>Minimum monthly salary: {escape(str(preferences.get('salary_minimum', 0)))}</p><p>{escape(disabled)}</p>{message}{form}{learning_sharing_form}{data_lifecycle}{activity_cleanup_form}<h3>Local data control</h3><p>Clears cached semantic interpretations only; it does not delete your profile, jobs, applications, or source documents.</p>{cache_form}</section>")
        _set_local_form_cookie(response, app)
        return response

    @app.post("/settings/privacy/redact-activity")
    def redact_legacy_activity_details(
        request: Request,
        confirmation: str = Form(...),
        csrf_token: str = Form(...),
    ) -> RedirectResponse:
        from fastapi import HTTPException

        if not _local_form_token_matches(request, csrf_token, app):
            raise HTTPException(status_code=403, detail="A valid local confirmation is required")
        if confirmation != "REDACT":
            return RedirectResponse(
                "/settings?notice=" + quote("Type REDACT exactly; no stored activity details were changed."),
                status_code=303,
            )
        redacted_count = repository.redact_activity_details()
        notice = (
            f"Redacted detail text from {redacted_count} older activity entries; event types and timestamps were preserved."
            if redacted_count else "No older activity detail text needed redaction."
        )
        return RedirectResponse("/settings?notice=" + quote(notice), status_code=303)

    @app.post("/settings/data/backup")
    def download_local_backup(
        passphrase: str = Form(...),
        passphrase_confirmation: str = Form(...),
    ) -> Response:
        from fastapi import HTTPException

        if passphrase != passphrase_confirmation:
            raise HTTPException(status_code=400, detail="Backup passphrases do not match")
        try:
            archive = encrypt_backup_archive(
                create_backup_archive(repository, app.state.storage_dir), passphrase
            )
        except (OSError, sqlite3.Error, ValueError) as error:
            raise HTTPException(status_code=503, detail="Could not create a verified local data backup") from error
        filename = "sampoagent-local-backup-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".sampobak"
        return Response(
            content=archive,
            media_type="application/vnd.sampoagent.backup",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
                "Pragma": "no-cache",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/settings/codex-learning")
    def save_codex_learning_setting(enabled: str = Form("no")) -> RedirectResponse:
        if enabled not in {"yes", "no"}:
            return RedirectResponse("/settings?notice=" + quote("Choose whether to enable the de-identified Codex learning summary; nothing was changed."), status_code=303)
        repository.set_setting("codex_learning_summary_enabled", "true" if enabled == "yes" else "false")
        state = "enabled" if enabled == "yes" else "disabled"
        return RedirectResponse("/settings?notice=" + quote(f"Codex learning-summary sharing {state}."), status_code=303)

    @app.post("/settings")
    def save_settings(
        application_mode: str = Form(...),
        daily_limit: int = Form(...),
        ai_usage_mode: str = Form(...),
        ocr_provider: str = Form("environment"),
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
        dry_run: str = Form("true"),
        autopilot_ack: str = Form("no"),
        email_send_autopilot_ack: str = Form("no"),
        automation_paused: str = Form("false"),
    ) -> RedirectResponse:
        if application_mode not in {"review_everything", "smart_approval", "autopilot"} or ai_usage_mode not in {"minimal", "balanced", "quality"} or ocr_provider not in {"environment", "none", "tesseract"} or dry_run not in {"true", "false"} or autopilot_ack not in {"yes", "no"} or email_send_autopilot_ack not in {"yes", "no"} or automation_paused not in {"true", "false"} or work_type not in {"any", "onsite", "hybrid", "remote"} or employment_type not in {"any", "full_time", "part_time", "temporary", "seasonal"} or schedule not in {"any", "day", "evening", "night", "weekend"} or include_public_sector not in {"yes", "no"} or include_recruitment_agencies not in {"yes", "no"} or daily_limit < 0 or salary_minimum < 0 or not 0 <= radius_km <= 500:
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
        repository.set_setting("ocr_provider", ocr_provider)
        repository.set_setting("automation_paused", automation_paused)
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
        full_auto_requested = application_mode == "autopilot" and autopilot_ack == "yes"
        live_mode_requested = application_mode == "smart_approval" or full_auto_requested
        email_send_notice = ""
        if dry_run == "false" and daily_limit <= 0:
            repository.revoke_autopilot("Daily limit removed")
            repository.set_setting("dry_run", "true")
            sync_automation_worker()
            return RedirectResponse("/settings?notice=" + quote("Set a positive daily limit before turning Dry Run off."), status_code=303)
        if application_mode == "autopilot" and dry_run == "false" and autopilot_ack != "yes":
            repository.revoke_autopilot("Full Autopilot authorization removed")
            repository.set_setting("dry_run", "true")
            sync_automation_worker()
            return RedirectResponse("/settings?notice=" + quote("Autopilot requires its authorization checkbox; Dry Run remains on."), status_code=303)
        if live_mode_requested and dry_run == "false":
            repository.set_setting("dry_run", "false")
            if full_auto_requested:
                try:
                    repository.grant_autopilot()
                except ValueError as error:
                    repository.revoke_autopilot("Autopilot grant could not be created")
                    repository.set_setting("dry_run", "true")
                    sync_automation_worker()
                    return RedirectResponse("/settings?notice=" + quote("Autopilot authorization could not be created. Check that a search target is active; Dry Run remains on."), status_code=303)
                if email_send_autopilot_ack == "yes":
                    try:
                        repository.grant_email_send_autopilot()
                    except ValueError:
                        repository.revoke_autopilot("Email Autopilot grant requirements were not met")
                        repository.set_setting("dry_run", "true")
                        sync_automation_worker()
                        return RedirectResponse("/settings?notice=" + quote("Email Autopilot requires a separate send-only email connection. No Autopilot grant was enabled."), status_code=303)
                else:
                    repository.connection.execute("UPDATE settings SET value='false' WHERE key='email_send_autopilot_authorized'")
                    repository.connection.execute("UPDATE settings SET value='' WHERE key IN ('email_send_autopilot_fingerprint','email_send_autopilot_expires_at')")
                    repository.connection.commit()
            else:
                repository.revoke_autopilot("Autopilot grant not selected")
                if email_send_autopilot_ack == "yes":
                    email_send_notice = " Email Autopilot was not enabled because Full Autopilot was not authorized."
        else:
            repository.revoke_autopilot("Automated submissions disabled")
            repository.set_setting("dry_run", "true")
            if email_send_autopilot_ack == "yes":
                email_send_notice = " Email Autopilot remains off while Dry Run or the emergency stop blocks submissions."
        sync_automation_worker()
        return RedirectResponse("/settings?notice=" + quote("Settings saved. CAPTCHA tasks are handled manually in the CAPTCHA Queue." + email_send_notice), status_code=303)

    @app.post("/settings/cache/clear")
    def clear_semantic_cache() -> RedirectResponse:
        repository.clear_cache()
        return RedirectResponse("/settings?notice=" + quote("Semantic cache cleared."), status_code=303)

    @app.get("/settings/email", response_class=HTMLResponse)
    def email_settings(request: Request, notice: str = Query(default="")) -> HTMLResponse:
        connection = repository.mailbox_connection()
        send_connection = repository.mail_send_connection()
        send_accounts = repository.mail_send_accounts()
        try:
            encrypt_token_payload({"setup_check": True})
            encrypted = True
        except EmailIntegrationError:
            encrypted = False
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
        csrf = str(app.state.local_action_token)
        send_accounts_markup = []
        for account in send_accounts:
            label = "Google-verified address" if account["address_status"] == "verified" else "Provider-reported address (not independently verified)"
            default = " · default sender" if send_connection and int(send_connection["id"]) == int(account["id"]) else ""
            send_accounts_markup.append(
                f"<article class='metric-card'><strong>{escape(str(account['provider']).title())}: {escape(str(account['sender_email']))}</strong>"
                f"<p>{label}{default}</p><form method='post' action='/email/send/disconnect/{int(account['id'])}'>"
                f"<input type='hidden' name='csrf_token' value='{csrf}'><button class='danger'>Disconnect this sender account</button></form></article>"
            )
        default_options = ("<option value='' selected disabled>Choose a default sender</option>" if not send_connection and send_accounts else "") + "".join(
            f"<option value='{int(account['id'])}'{' selected' if send_connection and int(send_connection['id']) == int(account['id']) else ''}>{escape(str(account['provider']).title())} · {escape(str(account['sender_email']))}</option>"
            for account in send_accounts
        )
        account_controls = (
            f"<form method='post' action='/email/send/default'><input type='hidden' name='csrf_token' value='{csrf}'><label>Default outgoing sender <select name='account_id' required>{default_options}</select></label><button>Save default sender</button></form>"
            f"<div class='metric-grid'>{''.join(send_accounts_markup)}</div>"
            "<p>The identity is fetched from the provider's fixed OpenID UserInfo endpoint. The Microsoft email claim is displayed as provider-reported; SampoAgent does not claim it is independently verified. These send permissions cannot read inbox content.</p>"
        ) if send_accounts else "<p>No identified send-only account is connected yet.</p>"
        legacy_notice = (
            "<p class='notice'>A legacy send token exists without a recorded provider identity. It is deliberately disabled for sending; reconnect the account below before use. The stored legacy token remains local until you disconnect all send accounts.</p>"
            if repository.legacy_mail_send_connection_needs_reconnect() else ""
        )
        send_buttons = []
        for provider, label in (("gmail", "Gmail"), ("microsoft", "Outlook")):
            try:
                OAuthConfig.from_environment(provider)
                configured = True
            except EmailIntegrationError:
                configured = False
            send_buttons.append(
                f"<form method='post' action='/email/send/connect/{provider}'><input type='hidden' name='csrf_token' value='{csrf}'>"
                f"<button {'disabled' if not (configured and encrypted) else ''}>Add {label} send-only account</button></form>"
            )
        send_connected = (
            f"{legacy_notice}{account_controls}<p>Outgoing email requires a separate OAuth permission. It is never added to the read-only inbox grant. Ready packages show their exact sender and recipient. Manual mode still requires review of each message; Full Autopilot sends only a unique address explicitly tied to an application-by-email instruction in the current verified listing.</p>"
            + "".join(send_buttons)
            + (f"<form method='post' action='/email/send/disconnect'><input type='hidden' name='csrf_token' value='{csrf}'><button class='danger'>Disconnect all send accounts and remove legacy send token</button></form>" if send_accounts or repository.legacy_mail_send_connection_needs_reconnect() else "")
        )
        setup = "<p class='notice'>Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, MICROSOFT_CLIENT_ID, MICROSOFT_CLIENT_SECRET and SAMPOAGENT_TOKEN_ENCRYPTION_KEY in your local environment. Register the exact callback URLs shown in .env.example. Secrets are never entered in this page.</p>"
        response = _page("Email", f"<section><h2>Read-only inbox connection</h2><p>Authorize only the inbox you want SampoAgent to check. This connection never sends, moves, or deletes email. Detected replies are suggestions until you confirm them.</p>{message}{setup}<div class='metric-grid'>{''.join(providers)}</div>{connected}</section><section><h2>Separate outgoing email permission</h2><p>This optional send-only OAuth grant is separate from inbox reading. Manual drafts are reviewed and confirmed per application. The separately enabled Full Autopilot grant may send only when an explicit application-by-email instruction in the fresh verified listing yields exactly one address; it uses the selected default sender. A provider acceptance is not delivery confirmation; uncertain sends are not retried.</p>{send_connected}<p><a href='/applications'>Review application email packages</a></p></section><section><h2>Review possible application replies</h2>{message_cards}</section>", path="/settings/email")
        _set_local_form_cookie(response, app)
        return response

    @app.post("/email/connect/{provider}")
    def connect_email(provider: str):
        try:
            config = OAuthConfig.from_environment(provider)
            encrypt_token_payload({"setup_check": True})
        except EmailIntegrationError as exc:
            return RedirectResponse("/settings/email?notice=" + quote("Email setup is not ready. Check the local provider settings and try again."), status_code=303)
        state = secrets.token_urlsafe(32)
        verifier, challenge = create_pkce_pair()
        app.state.email_oauth_states = {key: value for key, value in app.state.email_oauth_states.items() if float(value.get("expires_at", 0)) >= time.time()}
        app.state.email_oauth_states[state] = {"provider": provider, "purpose": "read", "verifier": verifier, "expires_at": time.time() + 600}
        response = RedirectResponse(authorization_url(config, state=state, challenge=challenge, purpose="read"), status_code=303)
        response.set_cookie("sampoagent_email_oauth_state", state, httponly=True, samesite="lax", secure=False, max_age=600, path=f"/email/callback/{provider}")
        return response

    @app.post("/email/send/connect/{provider}")
    def connect_email_send(provider: str, request: Request, csrf_token: str = Form(...)):
        if not _local_form_token_matches(request, csrf_token, app):
            return RedirectResponse("/settings/email?notice=" + quote("Refresh this local page before changing email permissions."), status_code=303)
        try:
            config = OAuthConfig.from_environment(provider)
            encrypt_token_payload({"setup_check": True})
            required_email_scope(provider, "send")
        except EmailIntegrationError:
            return RedirectResponse("/settings/email?notice=" + quote("Email send setup is not ready. Check the local provider settings and try again."), status_code=303)
        state = secrets.token_urlsafe(32)
        verifier, challenge = create_pkce_pair()
        app.state.email_oauth_states = {key: value for key, value in app.state.email_oauth_states.items() if float(value.get("expires_at", 0)) >= time.time()}
        app.state.email_oauth_states[state] = {"provider": provider, "purpose": "send", "verifier": verifier, "expires_at": time.time() + 600}
        response = RedirectResponse(authorization_url(config, state=state, challenge=challenge, purpose="send"), status_code=303)
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
                purpose = str(pending.get("purpose", "read"))
                if not tokens.get("refresh_token"):
                    raise EmailIntegrationError("Provider did not issue offline refresh access. Re-authorize the requested email permission.")
                tokens["expires_at"] = time.time() + float(tokens.get("expires_in", 3600))
                if purpose == "send":
                    granted_scopes = str(tokens.get("scope", ""))
                    if not {required_email_scope(provider, "send"), "openid", "email"}.issubset(set(granted_scopes.split())):
                        raise EmailIntegrationError("Provider did not grant the send and account identity permissions.")
                    identity = fetch_send_account_identity(provider, str(tokens["access_token"]))
                    expires_at = datetime.fromtimestamp(float(tokens["expires_at"]), timezone.utc).isoformat()
                    repository.save_mail_send_connection(
                        provider, encrypt_token_payload(tokens), granted_scopes,
                        subject=identity["subject"], sender_email=identity["sender_email"],
                        address_status=identity["address_status"], token_expires_at=expires_at,
                    )
                    response = RedirectResponse("/settings/email?notice=" + quote("Separate send account connected. Review its provider-reported address in Email settings."), status_code=303)
                else:
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
            return RedirectResponse("/settings/email?notice=" + quote("Mailbox could not be checked. Reconnect it if the problem continues."), status_code=303)
        return RedirectResponse("/settings/email?notice=" + quote(f"Inbox checked. {len(messages)} possible job replies are ready for review."), status_code=303)

    @app.post("/email/disconnect")
    def disconnect_email() -> RedirectResponse:
        repository.remove_mailbox_connection()
        return RedirectResponse("/settings/email?notice=" + quote("Mailbox disconnected and synced metadata removed."), status_code=303)

    @app.post("/email/send/disconnect")
    def disconnect_email_send(request: Request, csrf_token: str = Form(...)) -> RedirectResponse:
        if not _local_form_token_matches(request, csrf_token, app):
            return RedirectResponse("/settings/email?notice=" + quote("Refresh this local page before changing email permissions."), status_code=303)
        repository.remove_mail_send_connection()
        return RedirectResponse("/settings/email?notice=" + quote("All send accounts disconnected; legacy send credentials removed and email Autopilot consent revoked."), status_code=303)

    @app.post("/email/send/disconnect/{account_id}")
    def disconnect_email_send_account(account_id: int, request: Request, csrf_token: str = Form(...)) -> RedirectResponse:
        if not _local_form_token_matches(request, csrf_token, app):
            return RedirectResponse("/settings/email?notice=" + quote("Refresh this local page before changing email permissions."), status_code=303)
        removed = repository.remove_mail_send_account(account_id)
        message = "Sender account disconnected; related email Autopilot consent was revoked." if removed else "That sender account is no longer connected."
        return RedirectResponse("/settings/email?notice=" + quote(message), status_code=303)

    @app.post("/email/send/default")
    def set_email_send_default(request: Request, account_id: int = Form(...), csrf_token: str = Form(...)) -> RedirectResponse:
        if not _local_form_token_matches(request, csrf_token, app):
            return RedirectResponse("/settings/email?notice=" + quote("Refresh this local page before changing the default sender."), status_code=303)
        try:
            repository.set_default_mail_send_account(account_id)
            message = "Default sender updated. Re-authorize separate Email Autopilot before automatic email applications."
        except ValueError:
            message = "Choose a sender account that is still connected."
        return RedirectResponse("/settings/email?notice=" + quote(message), status_code=303)

    @app.post("/email/applications/{application_id}/draft")
    def create_application_email(
        application_id: int,
        request: Request,
        recipient: str = Form(...),
        confirm_recipient_from_posting: str = Form(""),
        csrf_token: str = Form(...),
    ) -> RedirectResponse:
        if not _local_form_token_matches(request, csrf_token, app):
            notice = "Refresh the Applications page before preparing an email draft."
        else:
            try:
                create_application_email_draft(
                    repository,
                    app.state.storage_dir,
                    application_id,
                    recipient=recipient,
                    confirm_recipient_from_posting=confirm_recipient_from_posting == "yes",
                )
                notice = "Encrypted local email draft prepared. It has not been sent; review the exact package in the outbox."
            except EmailOutboxError:
                notice = "Could not prepare the email draft. Review the application, recipient, archived CV, and email connection."
        return RedirectResponse("/applications?notice=" + quote(notice), status_code=303)

    @app.post("/email/applications/{application_id}/cancel")
    def cancel_application_email(application_id: int, request: Request, csrf_token: str = Form(...)) -> RedirectResponse:
        if not _local_form_token_matches(request, csrf_token, app):
            notice = "Refresh the Applications page before changing an email draft."
        else:
            cancelled = repository.cancel_email_outbox(application_id)
            notice = "Unsent encrypted draft discarded. You may prepare a corrected draft." if cancelled else "Only a not-yet-attempted draft can be discarded."
        return RedirectResponse("/applications?notice=" + quote(notice), status_code=303)

    @app.post("/email/applications/{application_id}/send")
    def send_application_email_route(
        application_id: int,
        request: Request,
        package_hash: str = Form(...),
        send_confirmation: str = Form(""),
        csrf_token: str = Form(...),
    ) -> RedirectResponse:
        if not _local_form_token_matches(request, csrf_token, app):
            notice = "Refresh the Applications page before sending. No email was sent."
        else:
            try:
                result = send_approved_application_email(
                    repository,
                    app.state.storage_dir,
                    application_id,
                    package_hash=package_hash,
                    confirmed=send_confirmation == "yes",
                    sender=send_email_message,
                )
                if result.state == "ACCEPTED":
                    notice = "Email provider accepted this one request. Delivery is not confirmed, and SampoAgent will not retry it."
                elif result.state == "UNKNOWN":
                    notice = "Email outcome is uncertain. SampoAgent will not retry; check the Sent folder or contact the employer before taking further action."
                else:
                    notice = "The provider rejected the email. This attempt will not be repeated automatically."
            except EmailOutboxError:
                notice = "Email was not sent. Review the package, sender connection, automation settings, and application status before retrying."
        return RedirectResponse("/applications?notice=" + quote(notice), status_code=303)

    @app.post("/email/messages/{message_id}/confirm")
    def confirm_email_response(message_id: int, application_id: int = Form(...), status: str = Form(...)) -> RedirectResponse:
        try:
            repository.confirm_mailbox_response(message_id, application_id, status)
        except ValueError as exc:
            return RedirectResponse("/settings/email?notice=" + quote("Could not confirm this message. Refresh and try again."), status_code=303)
        return RedirectResponse("/settings/email?notice=" + quote("Application tracker updated from the response you confirmed."), status_code=303)

    @app.get("/cvs", response_class=HTMLResponse)
    def cvs(job_id: int | None = Query(default=None), notice: str = Query(default="")) -> HTMLResponse:
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
        def cv_text_check_label(item: dict[str, object]) -> str:
            if not bool(item.get("text_check_performed")):
                return "Not checked"
            return f"{int(item.get('text_check_score') or 0)}%"

        archive_rows = "".join(f"<tr><td><a href='/cvs/archive/{item['id']}'>{escape(Path(str(item['path'])).name)}</a></td><td>{escape(str(item['strategy']))}</td><td>{escape(str(item['role_family']))}</td><td>{escape(str(item['language']))}</td><td>{item['fit_score']}%</td><td>{cv_text_check_label(item)}</td><td>{escape(str(item['checksum'])[:12])}</td></tr>" for item in repository.cv_archives()) or "<tr><td colspan='7'>No CVs archived yet.</td></tr>"
        notice_html = f"<p class='notice' role='status'>{escape(notice)}</p>" if notice else ""
        return _page("CVs", f"<section><p>Upload TXT, DOCX, or text-based PDF CVs. English and Finnish skill, language, licence, certificate, work and education sections are checked locally. Extracted claims stay unconfirmed until reviewed and include source spans where available. Scanned PDFs need an optional OCR provider or manual text entry; the app never invents OCR results.</p>{notice_html}<form method='post' action='/cvs/upload' enctype='multipart/form-data'><label>CV file <input name='file' type='file' accept='.txt,.docx,.pdf' required></label> <label>Language <select name='language'><option value='fi'>Finnish</option><option value='en'>English</option></select></label><label>Role family <select name='role_family'>{families}</select></label><button>Upload and extract</button></form></section><section><h3>Generate confirmed-fact CV</h3><form method='post' action='/cvs/generate'>{job_context}<label>Language <select name='language'>{language_options}</select></label><label>Role family <select name='role_family'>{families}</select></label><label>Target company (optional) <input name='company' value='{escape(str(selected_job['company'])) if selected_job else ''}'></label><label>Filename pattern <input name='filename_pattern' value='{{first}}_{{last}}_{{role}}_{{language}}.pdf'></label><p class='notice'>Available placeholders: first, last, fullname, role, company, language, version.</p><button>Generate PDF</button></form></section><section><h3>CV archive</h3><p>Job-specific CVs and uploaded CVs remain available here with a role/job evidence-fit score and content checksum. The CV text check measures presence of expected confirmed candidate text in the parsed PDF; it is not an ATS compatibility or hiring-success score.</p><table><tr><th>File</th><th>Method</th><th>Role</th><th>Language</th><th>Fit</th><th>CV text check</th><th>Checksum</th></tr>{archive_rows}</table></section><section><h3>Custom template metadata</h3><p>Metadata only: arbitrary DOCX layouts are not modified.</p>{template_form}<table><tr><th>Name</th><th>Language</th><th>Role family</th><th>Support</th></tr>{template_rows}</table></section>")

    @app.post("/cvs/templates")
    def register_cv_template(name: str = Form(...), language: str = Form(...), role_family: str = Form(...), notes: str = Form("")) -> RedirectResponse:
        if name.strip() and language in {"fi", "en"} and role_family in ROLE_FAMILIES:
            repository.add_cv_template(name=name, language=language, role_family=role_family, notes=notes)
        return RedirectResponse("/cvs", status_code=303)

    @app.post("/cvs/upload")
    async def upload_cv(file: UploadFile = File(...), language: str = Form("en"), role_family: str = Form("universal")) -> RedirectResponse:
        if not file.filename or Path(file.filename).suffix.lower() not in {".txt", ".docx", ".pdf"} or language not in {"fi", "en"} or role_family not in ROLE_FAMILIES:
            return RedirectResponse("/cvs", status_code=303)
        payload = await file.read()
        if len(payload) > 5 * 1024 * 1024:
            return RedirectResponse("/cvs", status_code=303)
        safe_name = Path(file.filename).name
        upload_dir = app.state.storage_dir / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        stored_path = upload_dir / f"{secrets.token_hex(6)}_{safe_name}"
        stored_path.write_bytes(payload)
        ocr_warning = None
        try:
            content = read_cv_file(stored_path, ocr_provider_name=repository.setting("ocr_provider") or "environment")
        except OCRProviderUnavailable:
            content = ""
            ocr_warning = "Local Tesseract is not installed or its OCR dependencies are unavailable. Enter the CV information manually or configure a local OCR provider."
        result = ingest_text_cv(content, source_id=safe_name, storage_dir=upload_dir)
        for fact in result.facts:
            evidence = None
            if fact.evidence:
                evidence = {
                    "page": fact.evidence.page,
                    "line": fact.evidence.line,
                    "start_char": fact.evidence.start_char,
                    "end_char": fact.evidence.end_char,
                    "excerpt": fact.evidence.excerpt,
                }
            repository.add_extracted_fact(fact_type=fact.type, value=fact.value, source_id=fact.source_id, confidence=fact.confidence, evidence=evidence)
        record_drafts = 0
        for record in result.records:
            evidence = {
                "page": record.evidence.page,
                "line": record.evidence.line,
                "start_char": record.evidence.start_char,
                "end_char": record.evidence.end_char,
                "excerpt": record.evidence.excerpt,
            }
            created = repository.add_extracted_candidate_record(record.record_type, {
                "title": record.title,
                "organization": record.organization,
                "location": record.location,
                "start_date": record.start_date,
                "end_date": record.end_date,
                "is_current": record.is_current,
                "details": record.details,
                "source_id": safe_name,
                "evidence": evidence,
            })
            record_drafts += int(created is not None)
        checksum = sha256(payload).hexdigest()
        repository.add_document(kind="uploaded_cv", path=str(stored_path), checksum=checksum)
        repository.archive_cv(path=str(stored_path), checksum=checksum, language=language, role_family=role_family, source_job_id=None, fit_score=0, strategy="uploaded")
        notice = ocr_warning or result.warning or f"CV saved locally. {len(result.facts)} draft claims are ready for review, plus {record_drafts} structured work/education records; none were confirmed automatically."
        return RedirectResponse("/profile?notice=" + quote(notice), status_code=303)

    @app.post("/cvs/generate")
    def generate_cv(
        language: str = Form(...),
        role_family: str = Form(...),
        filename_pattern: str = Form(""),
        company: str = Form(""),
        job_id: int | None = Form(None),
    ) -> HTMLResponse:
        profile = repository.profile()
        if not profile:
            return _page("CVs", "<section>Candidate profile is required before generating a CV.</section>")
        facts = repository.rows("facts")
        record_types = ("experience", "education", "certificate", "licence")
        records = {record_type: repository.candidate_records(record_type) for record_type in record_types}
        if language not in {"fi", "en"} or role_family not in ROLE_FAMILIES:
            return _page("CVs", "<section>Choose a supported CV language and role family.</section>")
        job = repository.job(job_id) if job_id is not None else None
        if job_id is not None and not job:
            return _page("CVs", "<section>Selected job was not found.</section>")
        version = secrets.token_hex(4)
        pattern = filename_pattern or "{first}_{last}_{role}_{language}_{version}.pdf"
        if "{version}" not in pattern:
            pattern = pattern.removesuffix(".pdf") + "_{version}.pdf"
        path = generate_cv_pdf(output_dir=app.state.storage_dir / "generated", language=language, role_family=role_family, candidate=profile, facts=facts, filename_pattern=pattern, company=company, records=records, target_title=str(job["title"]) if job else "", version=version)
        required = [profile["name"]]
        if profile.get("email"):
            required.append(profile["email"])
        required.extend(str(fact["value"]) for fact in facts if fact.get("confirmed") and fact.get("type") in {"skill", "language"})
        required.extend(str(record.get("title") or record.get("name") or "") for group in records.values() for record in group if record.get("title") or record.get("name"))
        report = check_pdf_text(path, required=required)
        checksum = sha256(path.read_bytes()).hexdigest()
        repository.add_document(kind="generated_cv", path=str(path), checksum=checksum)
        repository.archive_cv(path=str(path), checksum=checksum, language=language, role_family=role_family, source_job_id=job_id, fit_score=0, text_check_score=report.score, strategy="generated")
        download_path = "/cvs/generated/" + quote(path.name)
        return _page("CV Generated", f"<section><p>Generated: <a href='{escape(download_path)}'>{escape(path.name)}</a></p><p>CV text check: {report.score}% expected confirmed candidate text was found in the parsed PDF. This is not an ATS compatibility or hiring-success score.</p></section>", path="/cvs")

    @app.get("/cvs/generated/{filename}")
    def download_generated_cv(filename: str) -> FileResponse:
        safe_filename = Path(filename).name
        path = app.state.storage_dir / "generated" / safe_filename
        if safe_filename != filename or path.suffix.casefold() != ".pdf" or not path.is_file() or not any(str(item["path"]) == str(path) for item in repository.documents(kind="generated_cv")):
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Generated CV not found")
        return FileResponse(path, media_type="application/pdf", filename=safe_filename)

    @app.get("/cvs/archive/{archive_id}")
    def download_archived_cv(archive_id: int) -> FileResponse:
        from fastapi import HTTPException

        item = next((entry for entry in repository.cv_archives() if int(entry["id"]) == archive_id), None)
        if not item:
            raise HTTPException(status_code=404, detail="Archived CV not found")
        path = Path(str(item["path"])).resolve()
        root = app.state.storage_dir.resolve()
        if root not in path.parents or not path.is_file():
            raise HTTPException(status_code=404, detail="Archived CV not found")
        return FileResponse(path, media_type="application/pdf" if path.suffix.casefold() == ".pdf" else "application/octet-stream", filename=path.name)

    from sampoagent.app.onboarding import register_questionnaire
    from sampoagent.app.launch_review import register_launch_review

    register_questionnaire(app, repository, _page)
    register_launch_review(
        app,
        repository,
        _page,
        occupation_recommendations,
        lambda: run_discovery_preview(repository),
        sync_automation_worker,
    )

    @app.post("/onboarding/complete")
    def complete_onboarding(name: str = Form(...), locale: str = Form(...)) -> RedirectResponse:
        repository.save_profile(name, locale)
        return RedirectResponse("/", status_code=303)

    return app
