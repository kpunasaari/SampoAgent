"""Local draft questionnaire. Answers are not automatically verified candidate facts."""
from html import escape
import json
import re
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
from sampoagent.app.setup_flow import STEPS, pending, step_index


def register_questionnaire(app: FastAPI, repository: Repository, page: Callable) -> None:
    repository.connection.execute('CREATE TABLE IF NOT EXISTS questionnaire_drafts (section TEXT PRIMARY KEY, answers TEXT NOT NULL, version INTEGER NOT NULL)')
    repository.connection.commit()

    def saved() -> dict:
        return {row['section']: json.loads(row['answers']) for row in repository.connection.execute('SELECT section, answers FROM questionnaire_drafts')}

    @app.get('/landing', response_class=HTMLResponse)
    @app.get('/onboarding', response_class=HTMLResponse)
    def questionnaire(section: str | None = None, saved_ok: bool = False) -> HTMLResponse:
        guided = pending(repository)
        if section is None:
            section = STEPS[step_index(repository)] if guided else 'contact'
            if section == 'ready':
                return RedirectResponse('/onboarding/ready', status_code=303)
        if section not in SECTIONS and section != 'review':
            raise HTTPException(404, 'Unknown section')
        drafts = saved()
        total = sum(len(questions) for _, questions in SECTIONS.values())
        answered = sum(bool(value) for values in drafts.values() for value in values.values())
        nav = ''.join(f'<a href="/onboarding?section={key}" aria-current="{"step" if key == section else "false"}">{escape(title)}</a> ' for key, (title, _) in SECTIONS.items())
        if guided:
            nav = ''.join(
                f'<a href="/onboarding?section={key}">{i + 1}. {escape(title)}</a> '
                if i <= step_index(repository) else f'<span aria-disabled="true">{i + 1}. {escape(title)} · locked</span> '
                for i, (key, (title, _)) in enumerate(SECTIONS.items())
            )
        body = f'''<style>.question-nav{{display:flex;flex-wrap:wrap;gap:12px;margin:20px 0}}.question-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:20px}}.question-grid label{{display:flex;flex-direction:column;gap:8px}}.question-grid textarea{{width:100%;box-sizing:border-box;min-height:100px;background:#111827;color:#eee;border:1px solid #64748b;border-radius:8px;padding:12px;font:inherit}}.question-grid select{{width:100%}}</style>
        <section><h2>Build your application profile</h2><p>Answer once, review for each application. {answered} / {total} questions answered.</p><p>All questions are optional. Leave unknown or irrelevant answers blank; blank never means No. Drafts stay in this local database and are not automatically treated as verified facts or submission permission.</p><nav class="question-nav" aria-label="Profile sections">{nav}<a href="/onboarding?section=review">Review &amp; CV</a></nav></section>'''
        if saved_ok:
            body += '<p role="status">Draft saved on this device.</p>'
        if guided:
            body = body.replace('All questions are optional.', 'Name and email are required. Other questions may be left unanswered when unknown or not applicable; review each section before continuing.')
            if step_index(repository) < STEPS.index('review'):
                body = body.replace('<a href="/onboarding?section=review">Review &amp; CV</a>', '<span aria-disabled="true">8. Review &amp; CV · locked</span>')
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
            if guided:
                body += '<section><h3>Finish your profile review</h3><form method="post" action="/onboarding/review-complete"><label><input type="checkbox" name="reviewed" value="yes" required> I have reviewed my answers and CV information. Only individually confirmed facts may be used.</label><label><input type="checkbox" name="without_cv" value="yes"> Continue with a manually entered profile without uploading a CV.</label><button>Continue to job targets and working mode</button></form></section>'
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
                if guided and section == 'contact' and identifier in {'full_name', 'email'}:
                    input_type = 'email' if identifier == 'email' else 'text'
                    body += f'<input id="q-{identifier}" type="{input_type}" name="{identifier}" value="{escape(value, quote=True)}" maxlength="320" required>'
                elif options:
                    body += f'<select id="q-{identifier}" name="{identifier}"><option value="">Not answered / not applicable</option>'
                    body += ''.join(f'<option value="{escape(option)}"{" selected" if value == option else ""}>{escape(option)}</option>' for option in options) + '</select>'
                else:
                    body += f'<textarea id="q-{identifier}" name="{identifier}" maxlength="4000">{escape(value)}</textarea>'
                body += '</label>'
            keys = [*SECTIONS, 'review']
            following = keys[keys.index(section) + 1]
            if guided:
                body += '</div><label><input type="checkbox" name="section_reviewed" value="yes" required> I have reviewed this section; blank answers mean unknown or not applicable.</label><p><button type="submit">Save and continue</button></p></form></section>'
            else:
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
        guided = pending(repository)
        allowed_fields = set(fields) | ({'section_reviewed'} if guided else set())
        if set(form) - allowed_fields or len(form.multi_items()) != len(form):
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
        if guided:
            if form.get('section_reviewed') != 'yes':
                raise HTTPException(422, 'Review this section before continuing')
            if section == 'contact' and (not answers['full_name'] or len(answers['full_name']) > 320 or len(answers['email']) > 320 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', answers['email'])):
                raise HTTPException(422, 'Enter your name and a valid application email address')
        with repository.connection:
            repository.connection.execute('INSERT INTO questionnaire_drafts VALUES (?, ?, ?) ON CONFLICT(section) DO UPDATE SET answers=excluded.answers, version=excluded.version', (section, json.dumps(answers), VERSION))
        if guided:
            if section == 'contact':
                repository.save_profile(answers['full_name'], 'en', answers['email'])
            repository.set_setting('setup_step', str(STEPS.index(section) + 1))
            return RedirectResponse('/onboarding?section=' + STEPS[STEPS.index(section) + 1], status_code=303)
        return RedirectResponse(f'/onboarding?section={section}&saved_ok=true', status_code=303)

    @app.post('/onboarding/review-complete')
    async def finish_review(request: Request) -> RedirectResponse:
        form = await request.form()
        if not pending(repository) or step_index(repository) < STEPS.index('review'):
            raise HTTPException(409, 'Complete the questionnaire first')
        if set(form) - {'reviewed', 'without_cv'} or len(form.multi_items()) != len(form):
            raise HTTPException(422, 'Unknown or repeated review field')
        if form.get('reviewed') != 'yes':
            raise HTTPException(422, 'Review your profile before continuing')
        if not repository.documents(kind='uploaded_cv') and form.get('without_cv') != 'yes':
            raise HTTPException(422, 'Upload a CV or choose to continue with a manually entered profile')
        repository.set_setting('setup_step', str(STEPS.index('ready')))
        return RedirectResponse('/onboarding/ready', status_code=303)
