# SampoAgent dark modern UI design

## Purpose

Replace the current dense, table-first local interface with a clear dark-mode
experience for non-technical job seekers. Preserve every existing route,
form action, safety rule, and local-first constraint. This is a visual and
information-hierarchy redesign, not a product-scope change.

## Design principles

- Keep the user oriented: a persistent sidebar, page title, concise context,
  and one visible primary action per screen.
- Make safety understandable: Dry Run, review-required facts, high-risk
  questions, and hard blockers are shown as readable status badges.
- Prefer cards and grouped sections over uninterrupted tables. Keep tables for
  dense records but make them responsive and scannable.
- Use dark surfaces with high contrast and limited semantic color: violet-blue
  for primary actions, green for success, amber for review, and red for risk.
- Remain accessible: visible focus states, sufficient contrast, semantic
  labels, non-color status text, keyboard-operable controls, and responsive
  layouts.

## Shared shell

`_page()` becomes the single visual system boundary. It will render:

1. An application shell with a branded sidebar, product tagline, navigation
   labels, and an active-route state.
2. A content region with a small eyebrow, page title, and optional lead text.
3. Shared CSS variables, typography, responsive breakpoints, buttons, forms,
   panels, status badges, tables, empty states, and metric cards.

The implementation stays server-rendered FastAPI and requires no client-side
framework or new UI dependency.

## Page hierarchy

### Dashboard

The dashboard becomes an operational home: a hero panel communicates the
current safety mode, four metric cards show jobs, recommendations, queue, and
daily limit, and two lower panels show next steps and recent activity.

### Profile and CVs

Profile identity, skill input, structured history, and provenance review are
split into clear panels. CV upload/generation appears as two primary workflow
cards; template metadata remains secondary. Existing confirmation actions stay
explicit and destructive actions stay visually distinct.

### Careers, Jobs, Sources, and Queue

Career recommendations and explicit targets use readable status badges and
compact action controls. Jobs retain scoring and hard-block evidence while
surface-level score and verification are visually prioritized. Sources are
catalogued as capability cards/tables. Queue actions retain the current hard
requirement, duplicate, low-score override, and CV-selection gates.

### Applications, Answers, Analytics, Agent, and Settings

Application timelines, evidence, and status controls gain grouped records and
semantic badges. Answer risk labels are prominent. Analytics prioritizes key
outcomes. Agent status uses a health-card treatment. Settings groups application
controls, preferences, scoring, and local-data actions into clearly bounded
sections.

## Interaction and state

No route contracts or form field names change. Shared classes are applied to
existing semantic HTML. The active nav item is calculated from the current
request path. Server redirects, empty states, notices, and validation behavior
remain unchanged. Small responsive CSS rules turn wide tables into horizontally
scrollable, touch-safe regions rather than hiding information.

## Error handling and safety

Existing user-visible notices remain role=status. Destructive actions retain
their server-side behavior and receive danger styling only. The redesign must
not make automated submission appear enabled: Dry Run and manual-review states
are always expressed in text as well as color.

## Verification

- Add focused tests for the shared shell: active navigation, status text, and
  meaningful dashboard content.
- Run the full pytest suite and Python compilation.
- Launch the local app and inspect dashboard, profile, careers, CVs, jobs,
  queue, applications, and settings at desktop and mobile widths.
- Confirm all existing form actions and generated CV download links remain
  available after visual changes.
