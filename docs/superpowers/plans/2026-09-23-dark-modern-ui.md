# Dark Modern UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver an accessible, responsive, dark-mode visual system that makes SampoAgent's existing workflows clear for non-technical job seekers.

**Architecture:** Keep FastAPI server rendering and all route/form contracts. Expand the shared `_page()` shell into the design-system boundary, then add small presentation helpers for semantic badges and dashboard cards. Route content retains its existing business logic while receiving consistent panels, responsive tables, and action hierarchy.

**Tech Stack:** Python 3.11+, FastAPI, server-rendered HTML, CSS custom properties, pytest, FastAPI TestClient.

**Spec:** `docs/superpowers/specs/2026-09-22-dark-modern-ui-design.md`

## Global Constraints

- Preserve every existing route, form action, field name, safety gate, and local-first behavior.
- Do not add a Node, React, or new UI dependency.
- Use the dark palette: charcoal/navy surfaces, violet-blue primary action, green success, amber review, red risk.
- Keep status meaning available as text; color alone must never communicate safety state.
- Keep all controls keyboard accessible and layouts usable at narrow widths.

## Review Focus

- A narrow viewport keeps navigation available and data tables scroll without clipping actions; test the responsive classes on the shell task.
- An active route marks only its corresponding sidebar item; test dashboard and nested query routes on the shell task.
- Dry Run and high-risk/review states retain explicit text after badge styling; test dashboard and answer-bank rendering on the workflow task.
- Existing POST forms preserve action URLs and input names; test one representative form each for profile, queue, and settings on the workflow task.
- Generated-CV download links remain visible inside the redesigned CV page; test the CV route with a registered generated document on the workflow task.

---

### Task 1: Shared dark application shell

**Files:**
- Modify: `sampoagent/app/main.py:24-30`
- Create: `tests/test_ui_shell.py`

**Interfaces:**
- Consumes: `NAVIGATION: list[tuple[str, str]]`, `_page(title: str, body: str) -> HTMLResponse`.
- Produces: `_page(title: str, body: str, *, path: str = "/", eyebrow: str = "SampoAgent") -> HTMLResponse` and shared CSS classes `.app-shell`, `.sidebar`, `.nav-link`, `.page-header`, `.panel`, `.status`, `.table-wrap`.

- [ ] **Step 1: Write the failing shell tests**

```python
def test_shell_marks_the_current_navigation_item() -> None:
    client = TestClient(create_app(database_path=":memory:"))
    page = client.get("/profile")
    assert 'class="nav-link active"' in page.text
    assert 'aria-current="page">Profile' in page.text


def test_shell_exposes_responsive_and_accessible_design_tokens() -> None:
    client = TestClient(create_app(database_path=":memory:"))
    page = client.get("/")
    assert "--surface-0" in page.text
    assert "@media (max-width: 760px)" in page.text
    assert "Skip to content" in page.text
```

- [ ] **Step 2: Run shell tests and verify failure**

Run: `py -m pytest -q tests/test_ui_shell.py`

Expected: FAIL because the old header-only shell has no active navigation or design tokens.

- [ ] **Step 3: Implement the shell and shared CSS**

```python
def _page(title: str, body: str, *, path: str = "/", eyebrow: str = "SampoAgent") -> HTMLResponse:
    nav = "".join(
        f'<a class="nav-link{ " active" if href == path else ""}" '
        f'aria-current="{"page" if href == path else "false"}" href="{href}">{label}</a>'
        for label, href in NAVIGATION
    )
    return HTMLResponse(f"""<!doctype html>...<aside class="sidebar">{nav}</aside>
    <main id="content"><header class="page-header"><p class="eyebrow">{escape(eyebrow)}</p>
    <h1>{escape(title)}</h1></header>{body}</main>...""")
```

Define the palette, focus rings, buttons, form controls, panels, table wrapping, badges, and the 760px breakpoint in the inline shared style block. Keep route behavior untouched.

- [ ] **Step 4: Pass the current path to page renders**

Update each existing `_page(...)` call with its route path. For `GET /cvs`, use `path="/cvs"` even when `job_id` is present.

- [ ] **Step 5: Run the shell tests**

Run: `py -m pytest -q tests/test_ui_shell.py`

Expected: PASS.

- [ ] **Step 6: Commit the shell**

```powershell
git add sampoagent/app/main.py tests/test_ui_shell.py
git commit -m "Add dark responsive application shell"
```

### Task 2: Dashboard information hierarchy

**Files:**
- Modify: `sampoagent/app/main.py:46-50`
- Modify: `tests/test_ui_shell.py`

**Interfaces:**
- Consumes: `repository.count`, `repository.applications_today`, `repository.setting`, `repository.recent_activity`.
- Produces: dashboard-specific `.dashboard-hero`, `.metric-grid`, `.metric-card`, and `.next-step` markup.

- [ ] **Step 1: Write the failing dashboard tests**

```python
def test_dashboard_shows_safe_mode_and_actionable_metric_cards() -> None:
    client = TestClient(create_app(database_path=":memory:"))
    page = client.get("/")
    assert "Dry Run is on" in page.text
    assert 'class="metric-grid"' in page.text
    assert "Jobs found" in page.text
    assert "What to do next" in page.text
```

- [ ] **Step 2: Run the dashboard test and verify failure**

Run: `py -m pytest -q tests/test_ui_shell.py::test_dashboard_shows_safe_mode_and_actionable_metric_cards`

Expected: FAIL because the legacy dashboard has no hero or metric grid.

- [ ] **Step 3: Implement dashboard composition**

```python
cards = "".join(
    f'<article class="metric-card"><span>{escape(label)}</span><strong>{escape(str(value))}</strong></article>'
    for label, value in metrics
)
return _page("Dashboard", f"""
<section class="dashboard-hero"><span class="status status-review">Dry Run is on</span>
<h2>Work safely, one clear step at a time.</h2><p>...</p></section>
<section class="metric-grid">{cards}</section>
<div class="content-grid"><section class="panel">...</section><section class="panel">...</section></div>
""", path="/")
```

Use current repository values; do not manufacture new metrics or alter the safety behavior.

- [ ] **Step 4: Run dashboard and full tests**

Run: `py -m pytest -q tests/test_ui_shell.py && py -m pytest -q`

Expected: PASS.

- [ ] **Step 5: Commit dashboard hierarchy**

```powershell
git add sampoagent/app/main.py tests/test_ui_shell.py
git commit -m "Modernize dashboard hierarchy"
```

### Task 3: Workflow pages and semantic status treatment

**Files:**
- Modify: `sampoagent/app/main.py:52-400`
- Create: `tests/test_ui_workflows.py`

**Interfaces:**
- Consumes: existing profile, careers, jobs, sources, queue, applications, answers, analytics, agent, and settings route functions.
- Produces: `.panel`, `.panel-heading`, `.status-success`, `.status-review`, `.status-risk`, `.action-row`, `.table-wrap`, and `.empty-state` presentation markup without changed form contracts.

- [ ] **Step 1: Write failing workflow-preservation tests**

```python
def test_profile_and_queue_keep_their_existing_form_contracts() -> None:
    client = TestClient(create_app(database_path=":memory:"))
    profile = client.get("/profile")
    queue = client.get("/queue")
    assert 'action="/profile/skills"' in profile.text
    assert 'name="skill"' in profile.text
    assert 'name="cv_path"' in queue.text
    assert 'class="table-wrap"' in profile.text


def test_answers_keep_explicit_risk_text() -> None:
    client = TestClient(create_app(database_path=":memory:"))
    page = client.get("/answers")
    assert "High-risk questions always require your intervention" in page.text
    assert "status-risk" in page.text
```

- [ ] **Step 2: Run workflow tests and verify failure**

Run: `py -m pytest -q tests/test_ui_workflows.py`

Expected: FAIL because legacy page markup has no semantic UI classes.

- [ ] **Step 3: Apply panels and status badges route by route**

Wrap each logical section in `class="panel"`; use `class="table-wrap"` around every table. Add text-bearing status badges:

```python
def _status(text: str, tone: str) -> str:
    return f'<span class="status status-{tone}">{escape(text)}</span>'
```

Map confirmed/active/success to `success`, review-needed/partial/queued to `review`, and rejected/failed/hard-blocked to `risk`. Preserve every existing status word in badge text. Put related forms in `class="action-row"` and use `button secondary` for non-primary actions and `button danger` for delete/reject controls.

- [ ] **Step 4: Preserve CV generation and download visibility**

Add this test in `tests/test_ui_workflows.py`:

```python
def test_cv_page_keeps_generation_and_download_workflow_visible(tmp_path: Path) -> None:
    app = create_app(database_path=tmp_path / "agent.db")
    client = TestClient(app)
    page = client.get("/cvs?job_id=2")
    assert "Generate confirmed-fact CV" in page.text
    assert 'action="/cvs/generate"' in page.text
    assert "Generate for job:" in page.text
```

- [ ] **Step 5: Run workflow and full tests**

Run: `py -m pytest -q tests/test_ui_workflows.py && py -m pytest -q`

Expected: PASS.

- [ ] **Step 6: Commit workflow page styling**

```powershell
git add sampoagent/app/main.py tests/test_ui_workflows.py
git commit -m "Restyle job-seeker workflows"
```

### Task 4: Local browser quality assurance and documentation

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: the running command `py -m sampoagent run --database ui-qa.db --port 8780` and all UI routes.
- Produces: documented modern-UI behavior and manually verified screenshots/state.

- [ ] **Step 1: Add a UI test note to README**

Add a brief “Interface” section after “Current V1 scope”:

```markdown
## Interface

SampoAgent uses a responsive dark interface with a persistent sidebar, explicit
safety states, keyboard-visible focus, and local server-rendered forms. It
works without a Node or browser build step.
```

- [ ] **Step 2: Add a changelog entry**

Add an Unreleased entry that names the dark responsive shell, dashboard hierarchy, semantic status badges, responsive tables, and unchanged local-first safety workflow.

- [ ] **Step 3: Run automated verification**

Run: `py -m pytest -q; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; py -m compileall -q sampoagent; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }`

Expected: all tests pass and compilation exits zero.

- [ ] **Step 4: Run browser verification**

Run: `py -m sampoagent run --database ui-qa.db --port 8780`

Inspect `/`, `/profile`, `/careers`, `/cvs?job_id=2`, `/jobs`, `/queue`, `/applications`, and `/settings` using the local browser. Confirm side navigation, active route styling, button focus, readable badges, table overflow behavior, and the Finnish job language selection.

- [ ] **Step 5: Commit documents**

```powershell
git add README.md CHANGELOG.md
git commit -m "Document modern local interface"
```
