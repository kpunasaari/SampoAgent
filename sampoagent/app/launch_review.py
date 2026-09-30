"""Guided, read-only setup summary and explicit automation controls."""

from collections.abc import Callable
from html import escape
import json
import re
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from sampoagent.careers.recommendations import OccupationRecommendation
from sampoagent.db.repository import Repository
from sampoagent.jobs.discovery import SearchPlan


_MODES = {"review_everything", "smart_approval", "autopilot"}
_WORK_TYPES = {"any", "onsite", "hybrid", "remote"}
_EMPLOYMENT_TYPES = {"any", "full_time", "part_time", "permanent", "temporary", "seasonal"}
_HOURS_TYPES = {"any", "full_time", "part_time"}
_SCHEDULES = {"any", "day", "evening", "night", "weekend"}
_SCOPE_FIELDS = {
    "locations",
    "locations_exclude",
    "work_type",
    "employment_type",
    "hours_type",
    "schedule",
    "search_terms_include",
    "search_terms_exclude",
}
_FORM_FIELDS = _SCOPE_FIELDS | {
    "action",
    "application_mode",
    "daily_limit",
    "live_ack",
    "autopilot_ack",
    "selected_role",
    "use_profile_preferences",
}


def _role_value(title_en: str, title_fi: str) -> str:
    return json.dumps([title_en, title_fi], ensure_ascii=False, separators=(",", ":"))


def _role_choices(raw_values: list[str], permitted: set[tuple[str, str]]) -> list[tuple[str, str]]:
    if len(raw_values) > 50:
        raise HTTPException(422, "Choose no more than 50 target roles")
    parsed: list[tuple[str, str]] = []
    for raw in raw_values:
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            raise HTTPException(422, "Refresh the review page and choose a listed role") from None
        if not isinstance(value, list) or len(value) != 2 or not all(isinstance(part, str) for part in value):
            raise HTTPException(422, "Refresh the review page and choose a listed role")
        pair = (value[0], value[1])
        if pair not in permitted:
            raise HTTPException(422, "Refresh the review page and choose a listed role")
        parsed.append(pair)
    if len(parsed) != len(set(parsed)):
        raise HTTPException(422, "Duplicate target role")
    return parsed


def register_launch_review(
    app: FastAPI,
    repository: Repository,
    page: Callable[..., HTMLResponse],
    recommendations: Callable[[], list[OccupationRecommendation]],
    search_plan: Callable[[], SearchPlan],
    sync_worker: Callable[[], None],
) -> None:
    @app.get("/onboarding/ready", response_class=HTMLResponse)
    def launch_review(notice: str = "") -> HTMLResponse:
        profile = repository.profile()
        facts = repository.rows("facts")
        confirmed = [item for item in facts if item.get("confirmed") and not item.get("rejected")]
        pending = [item for item in facts if not item.get("confirmed") and not item.get("rejected")]
        skills = sorted(
            {str(item["value"]) for item in confirmed if item.get("type") in {"skill", "transferable_skill"}},
            key=str.casefold,
        )
        cv_count = len(repository.documents(kind="uploaded_cv"))
        current_targets = repository.target_occupations()
        active_targets = {
            (str(item["title_en"]), str(item["title_fi"]))
            for item in current_targets
            if item["enabled"]
        }
        suggested = recommendations()
        options: dict[tuple[str, str], OccupationRecommendation | None] = {
            (item.title_en, item.title_fi): item for item in suggested
        }
        for item in current_targets:
            options.setdefault((str(item["title_en"]), str(item["title_fi"])), None)
        role_items = []
        for (title_en, title_fi), recommendation in sorted(options.items(), key=lambda pair: pair[0][0].casefold()):
            selected = " checked" if (title_en, title_fi) in active_targets else ""
            match = f" · {recommendation.score}% evidence match" if recommendation else " · saved target"
            evidence = (
                "<small>Supporting evidence: "
                + escape(", ".join(recommendation.supporting_facts))
                + "; review missing links: "
                + escape(", ".join(recommendation.missing_requirements) or "none identified")
                + "</small>"
                if recommendation
                else ""
            )
            role_items.append(
                "<label class='review-role'><input type='checkbox' name='selected_role' value='"
                + escape(_role_value(title_en, title_fi), quote=True)
                + f"'{selected}> <span><strong>{escape(title_en)} / {escape(title_fi)}</strong>"
                + escape(match)
                + evidence
                + "</span></label>"
            )
        role_markup = "".join(role_items) or "<p>Add or confirm skills in Profile to receive evidence-backed suggestions.</p>"

        saved_preferences = repository.preferences()
        preferences = dict(saved_preferences)
        if preferences.get("employment_type") in {"full_time", "part_time"} and preferences.get("hours_type", "any") == "any":
            preferences["hours_type"] = str(preferences["employment_type"])
            preferences["employment_type"] = "any"
        effective_preferences = repository.effective_search_preferences()
        fallback_labels = {
            "locations": "Preferred locations",
            "work_type": "Work setting",
            "employment_type": "Contract type",
            "hours_type": "Weekly hours",
        }
        fallback_summary = ", ".join(
            f"{label}: {escape(str(effective_preferences.get(key, '' if key == 'locations' else 'any')))}"
            for key, label in fallback_labels.items()
            if effective_preferences.get(key, "" if key == "locations" else "any") != saved_preferences.get(key, "" if key == "locations" else "any")
        )
        fallback_notice = (
            f"Confirmed profile answers currently fill unrestricted Settings fields: {fallback_summary}."
            if fallback_summary
            else "No confirmed profile answers are currently filling unrestricted Settings fields."
        )
        sources = [item for item in repository.rows("job_sources") if bool(item.get("enabled"))]
        plan = search_plan()
        mode = repository.setting("application_mode") or "review_everything"
        if mode not in _MODES:
            mode = "review_everything"
        dry_run = repository.setting("dry_run") != "false"
        grant_active = repository.autopilot_authorized()
        worker = repository.worker_status()
        worker_state = escape(str(worker.get("status") or "not_started"))
        mode_labels = {
            "review_everything": "Review Everything",
            "smart_approval": "Smart Approval",
            "autopilot": "Full Autopilot",
        }
        opt = lambda value, label, current: f"<option value='{value}'{' selected' if current == value else ''}>{label}</option>"
        select_modes = "".join(opt(value, label, mode) for value, label in mode_labels.items())
        select_work = "".join(
            opt(value, label, str(preferences.get("work_type", "any")))
            for value, label in (("any", "Any work setting"), ("onsite", "On-site"), ("hybrid", "Hybrid"), ("remote", "Remote"))
        )
        select_employment = "".join(
            opt(value, label, str(preferences.get("employment_type", "any")))
            for value, label in (("any", "Any contract type"), ("permanent", "Permanent"), ("temporary", "Fixed-term / temporary"), ("seasonal", "Seasonal"))
        )
        select_hours = "".join(
            opt(value, label, str(preferences.get("hours_type", "any")))
            for value, label in (("any", "Any weekly hours"), ("full_time", "Full-time"), ("part_time", "Part-time"))
        )
        select_schedule = "".join(
            opt(value, label, str(preferences.get("schedule", "any")))
            for value, label in (("any", "Any shift"), ("day", "Day"), ("evening", "Evening"), ("night", "Night"), ("weekend", "Weekend"))
        )
        active_profiles = [item for item in repository.rows("career_profiles") if bool(item.get("enabled"))]
        profile_summary = "Candidate profile created" if profile else "Candidate profile not created yet"
        cv_summary = f"{cv_count} uploaded CV{'s' if cv_count != 1 else ''}"
        skills_summary = ", ".join(escape(skill) for skill in skills[:24]) or "No confirmed skills yet"
        if len(skills) > 24:
            skills_summary += f" and {len(skills) - 24} more"
        source_names = ", ".join(escape(str(item["name"])) for item in sources[:12]) or "No enabled sources"
        if len(sources) > 12:
            source_names += f" and {len(sources) - 12} more"
        scope_text = ", ".join(escape(term) for term in plan.terms[:8]) or "No explicit role/search phrase yet"
        query_count = len(plan.queries)
        quota_value = repository.setting("daily_limit") or "0"
        quota_remaining = max(0, int(quota_value) - repository.submissions_reserved_today()) if quota_value.isdigit() else 0
        grant_status = "Active · expires within 30 days" if grant_active else "Off"
        mode_status = "Dry Run: on · no final submissions" if dry_run else "Dry Run: off · live mode is enabled"
        current_notice = f"<p class='notice' role='status'>{escape(notice)}</p>" if notice else ""
        stop_rules = (
            "<ul><li>CAPTCHA or identity/sign-in challenge: pause that application for you.</li>"
            "<li>Unknown or conflicting required answer, legal declaration, or sensitive question: stop for review.</li>"
            "<li>Expired/unverified listing, unsupported form, changed scope, or changed CV upload: do not submit.</li>"
            "<li>Daily quota reached, emergency stop active, or local app/session unavailable: no further worker activity.</li></ul>"
        )
        disabled_note = "" if profile else "<p class='notice'>Create a candidate profile before enabling a live mode. Review mode remains available.</p>"
        body = f"""
<style>
.launch-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:1rem}}
.review-role{{display:flex;gap:.7rem;align-items:flex-start;padding:.8rem;border:1px solid var(--border);border-radius:12px;margin:.5rem 0}}
.review-role input{{margin-top:.35rem}}.review-role small{{display:block;color:var(--muted);margin-top:.25rem}}
.launch-fields{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:1rem}}
</style>
<section class='dashboard-hero'><p>Guided setup · review before enabling</p><h2>Review your career search and application permissions</h2>
<p>This page summarizes local CV evidence, suggested roles, search scope, quota, and stop rules. Suggestions never become targets automatically. Loading this page does not start a search or worker.</p>{current_notice}</section>
<div class='launch-grid'>
<section><h3>Profile and CV evidence</h3><p>{escape(profile_summary)} · {escape(cv_summary)}</p><p>{len(confirmed)} confirmed facts · {len(pending)} unreviewed CV claims</p><p>Confirmed candidate skills: {skills_summary}</p><p><a href='/profile'>Review fact sources and CV evidence</a> · <a href='/cvs'>Manage CV files</a></p></section>
<section><h3>Current search scope</h3><p>Search phrases: {scope_text}</p><p>{query_count} generated source/search links across {len(sources)} enabled sources.</p><p>Enabled sources: {source_names}</p><p>Active career profiles: {len(active_profiles)} · <a href='/sources'>Manage job sources</a></p></section>
<section><h3>Effective permission and quota</h3><p>Selected mode: {escape(mode_labels[mode])}</p><p>{escape(mode_status)}</p><p>Full Autopilot authorization: {grant_status}</p><p>Daily application limit: {escape(quota_value)} · submissions reserved today: {repository.submissions_reserved_today()} · remaining: {quota_remaining}</p><p>Worker status: {worker_state}{' · emergency stop is active' if repository.setting('automation_paused') == 'true' else ''}</p></section>
</div>
<form method='post' action='/onboarding/ready' class='settings-form'>
<section><h3>Choose target occupations</h3><p>Each suggestion includes its matching evidence. Confirmed professional or transferable skills can support recommendations, but do not claim a qualification. Targets you select are the only suggested occupations that can seed role searches.</p>{role_markup}</section>
<section><h3>Search preferences</h3><p>Only selected targets and explicit phrases create search intent. Location and work settings narrow results; enabled sources stay separately manageable.</p><p>{fallback_notice} Only clear, exact, confirmed global preferences are used. Ambiguous prose, job-specific pay/shifts, and preferred roles are not promoted into search filters.</p>
<div class='launch-fields'>
<label>Preferred locations <input name='locations' value='{escape(str(preferences.get('locations','')),quote=True)}' maxlength='2000' placeholder='Helsinki, Vantaa'></label>
<label>Exclude locations <input name='locations_exclude' value='{escape(str(preferences.get('locations_exclude','')),quote=True)}' maxlength='2000'></label>
<label>Work setting <select name='work_type'>{select_work}</select></label>
<label>Employment type <select name='employment_type'>{select_employment}</select></label>
<label>Weekly hours <select name='hours_type'>{select_hours}</select></label>
<label>Shift <select name='schedule'>{select_schedule}</select></label>
<label>Extra search phrases to include <input name='search_terms_include' value='{escape(str(preferences.get('search_terms_include','')),quote=True)}' maxlength='2000'></label>
<label>Search phrases to exclude <input name='search_terms_exclude' value='{escape(str(preferences.get('search_terms_exclude','')),quote=True)}' maxlength='2000'></label>
<label>Use confirmed questionnaire preferences when these filters are unrestricted? <select name='use_profile_preferences'>{opt('yes', 'Yes · use clear profile answers', 'yes' if repository.setting('use_profile_preferences') != 'false' else 'no')}{opt('no', 'No · use Settings only', 'yes' if repository.setting('use_profile_preferences') != 'false' else 'no')}</select></label>
<label>Application mode <select name='application_mode'>{select_modes}</select></label>
<label>Daily application limit <input name='daily_limit' type='number' min='0' max='999999' value='{escape(quota_value,quote=True)}' required></label>
</div><p>0 means no automated application quota is configured; a positive quota is required for live modes. The current selected mode is not itself permission to submit.</p></section>
<section><h3>Permission choices</h3><p>Review Everything prepares work only. Smart Approval prepares eligible applications and waits for your approval of each exact package. Full Autopilot can submit eligible applications without per-job prompts for at most 30 days, within the saved roles, preferences, confirmed facts and daily quota.</p>
{disabled_note}
<label><input type='checkbox' name='live_ack' value='yes'> I explicitly choose to enable the selected live mode now. I understand that this may allow external application activity within the limits above.</label>
<label><input type='checkbox' name='autopilot_ack' value='yes'> For Full Autopilot only: I authorize submission without per-job prompts for up to 30 days using confirmed information and the exact scope and daily limit shown above. Next/Continue steps may save candidate information page by page; if a later step needs review, earlier entries may already be saved. A current grant is shown above but must be actively selected again to renew it.</label>
<h4>Automatic stop rules</h4>{stop_rules}
<p>CAPTCHA and challenges always wait for you one at a time. Full Autopilot stays off until its explicit grant is current. The worker runs only while the local SampoAgent process and interactive browser session are available.</p>
<button name='action' value='save_review'>Save review only · keep Dry Run on</button>
<button name='action' value='enable_mode' class='danger'>Enable selected live mode</button>
</section></form>
<section><h3>Next steps</h3><p><a href='/onboarding?section=review'>Finish profile questionnaire</a> · <a href='/profile'>Review candidate facts</a> · <a href='/careers'>Detailed career suggestions</a> · <a href='/jobs'>Review jobs and explicitly start a search</a> · <a href='/settings'>Advanced settings</a></p></section>
"""
        return page("Guided Search Review", body, path="/onboarding/ready")

    @app.post("/onboarding/ready")
    async def save_launch_review(request: Request) -> RedirectResponse:
        form = await request.form(max_fields=100, max_part_size=16384)
        items = list(form.multi_items())
        names = [name for name, _ in items]
        if set(names) - _FORM_FIELDS or any(names.count(name) > 1 for name in set(names) - {"selected_role"}):
            raise HTTPException(422, "Unknown or repeated setup field")
        action = form.get("action", "")
        # The two submit buttons override the default hidden save action.
        if action not in {"save_review", "enable_mode"}:
            raise HTTPException(422, "Choose whether to save the review or enable its selected mode")
        mode = form.get("application_mode", "")
        if not isinstance(mode, str) or mode not in _MODES:
            raise HTTPException(422, "Choose a supported application mode")
        raw_quota = form.get("daily_limit", "")
        if not isinstance(raw_quota, str) or not re.fullmatch(r"\d{1,6}", raw_quota):
            raise HTTPException(422, "Daily limit must be a whole number from 0 to 999999")
        daily_limit = int(raw_quota)
        scope: dict[str, str] = {}
        for key in _SCOPE_FIELDS:
            raw = form.get(key, "any" if key == "hours_type" else "")
            if not isinstance(raw, str) or len(raw) > 2000:
                raise HTTPException(422, "Search preferences must be text of at most 2000 characters")
            scope[key] = raw.strip()
        if scope["work_type"] not in _WORK_TYPES or scope["employment_type"] not in _EMPLOYMENT_TYPES or scope["schedule"] not in _SCHEDULES:
            raise HTTPException(422, "Choose a supported work setting, employment type, and shift")
        if scope["hours_type"] not in _HOURS_TYPES:
            raise HTTPException(422, "Choose a supported weekly-hours preference")
        use_profile_preferences = form.get(
            "use_profile_preferences",
            "no" if repository.setting("use_profile_preferences") == "false" else "yes",
        )
        if use_profile_preferences not in {"yes", "no"}:
            raise HTTPException(422, "Choose whether confirmed profile preferences may be used")
        live_ack = form.get("live_ack", "no")
        autopilot_ack = form.get("autopilot_ack", "no")
        if live_ack not in {"yes", "no"} or autopilot_ack not in {"yes", "no"}:
            raise HTTPException(422, "Refresh the review page and confirm the permission choices")

        suggestions = {(item.title_en, item.title_fi) for item in recommendations()}
        saved_targets = {
            (str(item["title_en"]), str(item["title_fi"]))
            for item in repository.target_occupations()
        }
        chosen_roles = _role_choices(
            [str(value) for value in form.getlist("selected_role")],
            suggestions | saved_targets,
        )

        if action == "enable_mode":
            if mode == "review_everything" or live_ack != "yes":
                raise HTTPException(422, "Select Smart Approval or Full Autopilot and explicitly confirm live mode")
            if daily_limit <= 0:
                raise HTTPException(422, "Set a positive daily limit before enabling a live mode")
            if not repository.profile():
                raise HTTPException(422, "Create a candidate profile before enabling a live mode")
            current_profiles = any(bool(item.get("enabled")) for item in repository.rows("career_profiles"))
            explicit_text = bool(scope["search_terms_include"].strip())
            if not chosen_roles and not current_profiles and not explicit_text:
                raise HTTPException(422, "Choose at least one target role, active career profile, or explicit search phrase")
            if mode == "autopilot" and autopilot_ack != "yes":
                raise HTTPException(422, "Full Autopilot requires its separate 30-day authorization")

        repository.replace_enabled_target_occupations(chosen_roles)
        preferences = repository.preferences()
        preferences.update(scope)
        repository.save_preferences(preferences)
        repository.set_setting("use_profile_preferences", "true" if use_profile_preferences == "yes" else "false")
        repository.set_setting("application_mode", mode)
        repository.set_setting("daily_limit", str(daily_limit))

        if action == "save_review":
            repository.set_setting("dry_run", "true")
            repository.revoke_autopilot("Guided review saved without live authorization")
            sync_worker()
            notice = "Review scope saved. Dry Run remains on; no search was started."
        elif mode == "smart_approval":
            repository.set_setting("dry_run", "false")
            repository.revoke_autopilot("Smart Approval selected instead of Full Autopilot")
            sync_worker()
            notice = "Smart Approval enabled. Each exact application package still waits for your review."
        else:
            repository.set_setting("dry_run", "false")
            try:
                repository.grant_autopilot(days=30)
            except ValueError:
                repository.set_setting("dry_run", "true")
                repository.revoke_autopilot("Guided Autopilot authorization could not be created")
                sync_worker()
                raise HTTPException(422, "Could not authorize Full Autopilot for this saved scope; Dry Run remains on") from None
            sync_worker()
            notice = "Full Autopilot authorized for up to 30 days within the saved roles, preferences, and daily limit."
        return RedirectResponse("/onboarding/ready?notice=" + quote(notice), status_code=303)
