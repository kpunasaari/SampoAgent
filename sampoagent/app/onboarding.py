"""Local draft questionnaire. Answers are not automatically verified candidate facts."""
from html import escape
import json
from collections.abc import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from sampoagent.candidate.questions import (
    AnswerConfirmation,
    PER_APPLICATION,
    SECTIONS,
    VERSION,
    answer_state_for_value,
    question_metadata,
)
from sampoagent.db.repository import Repository


def register_questionnaire(app: FastAPI, repository: Repository, page: Callable) -> None:
    repository.connection.execute('CREATE TABLE IF NOT EXISTS questionnaire_drafts (section TEXT PRIMARY KEY, answers TEXT NOT NULL, version INTEGER NOT NULL)')
    repository.connection.commit()

    def saved() -> dict:
        return {row['section']: json.loads(row['answers']) for row in repository.connection.execute('SELECT section, answers FROM questionnaire_drafts')}

    @app.get('/landing', response_class=HTMLResponse)
    @app.get('/onboarding', response_class=HTMLResponse)
    def questionnaire(section: str = 'contact', saved_ok: bool = False) -> HTMLResponse:
        if section not in SECTIONS and section != 'review':
            raise HTTPException(404, 'Unknown section')
        drafts = saved()
        total = sum(len(questions) for _, questions in SECTIONS.values())
        answered = sum(bool(value) for values in drafts.values() for value in values.values())
        nav = ''.join(f'<a href="/onboarding?section={key}" aria-current="{"step" if key == section else "false"}">{escape(title)}</a> ' for key, (title, _) in SECTIONS.items())
        body = f'''<style>.question-nav{{display:flex;flex-wrap:wrap;gap:12px;margin:20px 0}}.question-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:20px}}.question-grid label{{display:flex;flex-direction:column;gap:8px}}.question-grid textarea{{width:100%;box-sizing:border-box;min-height:100px;background:#111827;color:#eee;border:1px solid #64748b;border-radius:8px;padding:12px;font:inherit}}.question-grid select{{width:100%}}</style>
        <section><h2>Build your application profile</h2><p>Answer once, review for each application. {answered} / {total} questions answered.</p><p>All questions are optional. Leave unknown or irrelevant answers blank; blank never means No. Drafts stay in this local database and are not automatically treated as verified facts or submission permission.</p><nav class="question-nav" aria-label="Profile sections">{nav}<a href="/onboarding?section=review">Review &amp; CV</a></nav></section>'''
        if saved_ok:
            body += '<p role="status">Draft saved on this device.</p>'
        if section == 'review':
            body += '<form method="post" action="/onboarding/confirm"><section><h3>Confirm reusable answers</h3><p>Check only answers you have reviewed and confirm as accurate. They will enter the answer bank; legal, health, criminal-history, security and other high-risk declarations still stop for review.</p>'
            for key, (title, questions) in SECTIONS.items():
                body += f'<section><h3>{escape(title)}</h3><dl>'
                for identifier, label, _ in questions:
                    value = drafts.get(key, {}).get(identifier, '')
                    if value:
                        question_id = f'{key}:{identifier}'
                        metadata = question_metadata(question_id)
                        body += f'<dt>{escape(label)}</dt><dd style="white-space:pre-wrap">{escape(value)}</dd>'
                        if metadata is None or not metadata.reusable:
                            body += '<p>This answer is application-specific and will not be reused automatically.</p>'
                            continue
                        body += f'<label><input type="checkbox" name="confirmed" value="{question_id}"> Confirm this answer for reuse</label>'
                        if metadata.scope_type == 'COUNTRY':
                            country = str(drafts.get('eligibility', {}).get('work_country', ''))
                            body += f'<label>Applies to country <input name="scope_country:{question_id}" value="{escape(country, quote=True)}" autocomplete="country-name"></label>'
                        if metadata.supports_expiry:
                            body += f'<label>Valid until (optional) <input type="date" name="valid_until:{question_id}"></label>'
                body += f'</dl><a href="/onboarding?section={key}">Edit this section</a></section>'
            body += '<button type="submit">Confirm selected answers</button></section></form>'
            body += '<section><h3>Questions to answer for each application</h3><p>These depend on the vacancy and cannot be safely pre-approved here.</p><ul>' + ''.join(f'<li>{escape(item)}</li>' for item in PER_APPLICATION) + '</ul></section>'
            body += '<section><h3>CV analysis and job scope</h3><p>Upload a text-readable PDF, DOCX or TXT. SampoAgent extracts candidate claims locally and keeps them unconfirmed until you review their source evidence. Scanned PDFs need OCR support or manual text entry.</p><form method="post" action="/cvs/upload" enctype="multipart/form-data"><label>CV file <input type="file" name="file" accept=".pdf,.docx,.txt" required></label><label>CV language <select name="language"><option value="fi">Finnish</option><option value="en" selected>English</option></select></label><input type="hidden" name="role_family" value="universal"><button>Upload and analyze CV</button></form><p>After upload: <a href="/profile">review candidate facts</a>, then use the <a href="/onboarding/ready">guided search and permissions review</a> to inspect evidence-backed <a href="/careers">career suggestions</a>, choose locations and work preferences, review quota and stop rules, and select a mode. Suggestions never start a search until you activate a target role. Loading the review does not start a search or worker; a live mode requires a separate explicit action. Uploading a CV or completing this questionnaire does not enable automatic submission.</p><a href="/cvs">Manage CVs</a></section>'
            if not repository.profile():
                name = escape(drafts.get('contact', {}).get('full_name', ''), quote=True)
                body += f'<section><h3>Create candidate profile</h3><form method="post" action="/onboarding/complete"><label>Name <input name="name" value="{name}" required></label><label>Application language <select name="locale"><option value="en">English</option><option value="fi">Finnish</option></select></label><button>Create profile in Dry Run mode</button></form><p>Other answers remain drafts for review.</p></section>'
        else:
            title, questions = SECTIONS[section]
            body += f'<section><h3>{escape(title)}</h3><form method="post" action="/onboarding/questions/{section}"><div class="question-grid">'
            if section == 'skills':
                body += '<p>List several skills with commas, semicolons or separate lines. Transferable abilities and hobbies can suggest adjacent occupations, but are kept distinct from confirmed professional experience in CVs.</p>'
            if section == 'eligibility':
                body += '<p>Answers apply only to the named country. Recheck validity before each application. Do not enter passport or identity numbers.</p>'
            for identifier, label, options in questions:
                value = drafts.get(section, {}).get(identifier, '')
                body += f'<label for="q-{identifier}">{escape(label)}'
                if options:
                    body += f'<select id="q-{identifier}" name="{identifier}"><option value="">Not answered / not applicable</option>'
                    body += ''.join(f'<option value="{escape(option)}"{" selected" if value == option else ""}>{escape(option)}</option>' for option in options) + '</select>'
                else:
                    body += f'<textarea id="q-{identifier}" name="{identifier}" maxlength="4000">{escape(value)}</textarea>'
                body += '</label>'
            keys = [*SECTIONS, 'review']
            following = keys[keys.index(section) + 1]
            body += f'</div><p><button type="submit">Save this section</button> <a href="/onboarding?section={following}">Next section / skip</a></p></form></section>'
        return page('Application Profile', body, path='/onboarding')

    @app.post('/onboarding/confirm')
    async def confirm_answers(request: Request) -> RedirectResponse:
        form = await request.form(max_fields=200, max_part_size=65536)
        selected = [str(value) for value in form.getlist('confirmed')]
        if len(selected) != len(set(selected)) or len(selected) > 90:
            raise HTTPException(422, 'Duplicate or excessive confirmations')
        drafts = saved()
        known: dict[str, AnswerConfirmation] = {}
        allowed_fields = {'confirmed'}
        for section_name, (_, questions) in SECTIONS.items():
            for question_id, label, _ in questions:
                value = str(drafts.get(section_name, {}).get(question_id, '')).strip()
                if not value:
                    continue
                stable_id = f'{section_name}:{question_id}'
                metadata = question_metadata(stable_id)
                if metadata is None or not metadata.reusable:
                    continue
                country_field = f'scope_country:{stable_id}'
                expiry_field = f'valid_until:{stable_id}'
                if metadata.scope_type == 'COUNTRY':
                    allowed_fields.add(country_field)
                if metadata.supports_expiry:
                    allowed_fields.add(expiry_field)
                country = str(form.get(country_field, '')).strip() if metadata.scope_type == 'COUNTRY' else ''
                if metadata.scope_type == 'COUNTRY' and not country:
                    country = str(drafts.get('eligibility', {}).get('work_country', '')).strip()
                valid_until = str(form.get(expiry_field, '')).strip() or None if metadata.supports_expiry else None
                known[stable_id] = AnswerConfirmation(
                    question_id=stable_id,
                    category=metadata.category,
                    question=label,
                    value=value,
                    answer_state=answer_state_for_value(value),
                    value_type=metadata.value_type,
                    sensitivity=metadata.sensitivity,
                    scope_type=metadata.scope_type,
                    scope_country=country,
                    valid_until=valid_until,
                )
        if set(form) - allowed_fields:
            raise HTTPException(422, 'Unknown confirmation field')
        if any(key not in known for key in selected):
            raise HTTPException(422, 'Only non-empty saved answers can be confirmed')
        try:
            repository.confirm_onboarding_answers([known[key] for key in selected])
        except ValueError as exc:
            raise HTTPException(422, 'Could not save these answers. Refresh the questionnaire and try again.') from None
        return RedirectResponse('/onboarding?section=review&saved_ok=true', status_code=303)

    @app.post('/onboarding/questions/{section}')
    async def save(section: str, request: Request) -> RedirectResponse:
        if section not in SECTIONS:
            raise HTTPException(404, 'Unknown section')
        form = await request.form(max_fields=100, max_part_size=65536)
        fields = {key: options for key, _, options in SECTIONS[section][1]}
        if set(form) - set(fields) or len(form.multi_items()) != len(form):
            raise HTTPException(422, 'Unknown or repeated question')
        answers = {}
        for key, options in fields.items():
            value = form.get(key, '')
            if not isinstance(value, str) or len(value) > 4000:
                raise HTTPException(422, 'Answer must be text of at most 4000 characters')
            value = value.strip()
            if options and value and value not in options:
                raise HTTPException(422, 'Invalid answer option')
            answers[key] = value
        if section == 'eligibility' and not answers['work_country'] and any(answers[key] for key in ('work_permission', 'sponsorship_now', 'sponsorship_future')):
            raise HTTPException(422, 'Specify the country for work eligibility answers')
        with repository.connection:
            repository.connection.execute('INSERT INTO questionnaire_drafts VALUES (?, ?, ?) ON CONFLICT(section) DO UPDATE SET answers=excluded.answers, version=excluded.version', (section, json.dumps(answers), VERSION))
        return RedirectResponse(f'/onboarding?section={section}&saved_ok=true', status_code=303)
