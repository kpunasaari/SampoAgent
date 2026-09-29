"""Versioned, reusable application preparation questions; never consent to submit."""

from dataclasses import dataclass
import re

VERSION = 1
ANSWER_STATES = frozenset({
    'CONFIRMED', 'DECLINED', 'UNKNOWN', 'CONFLICT', 'EXPIRED',
    'NEEDS_RECONFIRMATION', 'NON_REUSABLE', 'SUPERSEDED', 'DRAFT',
})
YES_NO = ('Yes', 'No', 'Unsure', 'Prefer not to answer')
# Stable identifiers, labels, optional controlled choices. Empty means unanswered.
SECTIONS = {
    'contact': ('Contact details', [
        ('full_name', 'Full legal name', ()),
        ('preferred_name', 'Preferred name', ()),
        ('email', 'Application email address', ()),
        ('phone', 'Phone number (include country only if needed)', ()),
        ('city', 'Current city and country', ()),
        ('address', 'Street address (optional)', ()),
        ('postal_code', 'Postal code (optional)', ()),
        ('contact_language', 'Preferred communication language', ()),
        ('contact_times', 'Suitable times and timezone for contact', ()),
    ]),
    'eligibility': ('Work eligibility', [
        ('work_country', 'Country these work-permission answers apply to', ()),
        ('work_permission', 'Do you currently have permission to work in that country?', YES_NO),
        ('permission_limits', 'Permission expiry or restrictions (no identity document numbers)', ()),
        ('sponsorship_now', 'Would you need employer sponsorship now in that country?', YES_NO),
        ('sponsorship_future', 'Would you need sponsorship in the future in that country?', YES_NO),
        ('professional_registration', 'Professional registrations: jurisdiction, status and expiry', ()),
    ]),
    'preferences': ('Job preferences', [
        ('target_roles', 'Which job titles or fields interest you?', ()),
        ('excluded_roles', 'Which roles or industries should be excluded?', ()),
        ('locations', 'Preferred work locations', ()),
        ('relocation', 'Are you willing to relocate?', YES_NO),
        ('relocation_limits', 'Relocation locations, timing and support needed', ()),
        ('workplace', 'On-site, hybrid or remote preferences', ()),
        ('contract', 'Permanent, fixed-term, temporary or freelance preferences', ()),
        ('hours', 'Full-time / part-time preference and weekly hours', ()),
        ('travel', 'Willingness to travel: frequency, distance and overnight stays', ()),
        ('commute', 'Maximum commute time and available transport', ()),
    ]),
    'availability': ('Availability and pay', [
        ('employment_status', 'Current employment status', ('Employed', 'Self-employed', 'Unemployed', 'Student', 'Other', 'Prefer not to answer')),
        ('start_date', 'Earliest available start date', ()),
        ('notice_period', 'Notice period or existing commitments', ()),
        ('shifts', 'Available days and shifts (evenings, nights, weekends)', ()),
        ('overtime', 'Overtime and on-call availability', ()),
        ('planned_absence', 'Known unavailable dates (no personal explanation needed)', ()),
        ('salary', 'Expected gross pay: range, currency and hourly/monthly/annual basis', ()),
        ('salary_flexibility', 'Is your pay expectation negotiable?', YES_NO),
        ('interview_availability', 'Interview availability and timezone', ()),
    ]),
    'experience': ('Employment and education', [
        ('employment_history', 'For each job: employer, role, location, start/end month and responsibilities', ()),
        ('experience_years', 'Years of experience by role, industry or skill', ()),
        ('achievements', 'Relevant achievements and your individual contribution', ()),
        ('volunteering', 'Volunteer, internship and other relevant experience', ()),
        ('education', 'For each qualification: school, subject, level, dates and completed/in-progress status', ()),
        ('training', 'Courses and vocational training', ()),
        ('certificates', 'Certificates and safety cards: name, issuer and expiry', ()),
        ('licences', 'Driving licence categories, restrictions and validity', ()),
        ('own_vehicle', 'Is a vehicle available for work?', YES_NO),
    ]),
    'skills': ('Skills across all fields', [
        ('languages', 'Languages and separate spoken/written proficiency levels', ()),
        ('technical_skills', 'Technical skills, software, tools and equipment', ()),
        ('practical_skills', 'Practical or manual skills learned at work or outside work', ()),
        ('people_skills', 'Customer service, communication, teaching or care skills', ()),
        ('management_skills', 'Leadership, planning, sales or business skills', ()),
        ('creative_skills', 'Creative, design, craft or media skills', ()),
        ('other_skills', 'Other abilities, hobbies and transferable skills in any field', ()),
        ('skill_evidence', 'For important skills: level, last used, experience and examples/evidence', ()),
        ('skills_for_work', 'Which additional abilities would you like to use professionally?', ()),
        ('learning_goals', 'Which roles or skills are you willing to train for?', ()),
    ]),
    'materials': ('Application material', [
        ('summary', 'Short professional introduction (draft)', ()),
        ('career_goals', 'Career goals and what matters to you in a job', ()),
        ('strengths', 'Strengths with concrete examples', ()),
        ('challenge', 'A challenging situation: your actions and outcome', ()),
        ('teamwork', 'A teamwork or conflict-resolution example', ()),
        ('motivation', 'General interests and motivation (tailor for each employer)', ()),
        ('portfolio', 'Portfolio or work sample links (only material you may share)', ()),
        ('professional_links', 'LinkedIn, GitHub or other professional links', ()),
        ('references_available', 'Can references be supplied if requested?', YES_NO),
        ('reference_contact', 'May a current employer be contacted?', ('Ask me for each application', 'No')),
    ]),
}

PER_APPLICATION = (
    'Why this employer and this particular role?',
    'Have you previously applied to or worked for this employer?',
    'How did you hear about this vacancy? Is there a named referral?',
    'Do you meet the specific required qualification, licence or clearance?',
    'Are you willing to undertake the specified assessment or background check?',
    'Do you need an adjustment to this recruitment process? (Optional; no diagnosis needed.)',
    'Optional demographic/equal-opportunity questions: answer or decline for this form.',
    'References: who may be contacted, and have they agreed to share their details?',
    'Employer privacy notice, retention/talent-pool permission and accuracy declaration.',
)


@dataclass(frozen=True)
class QuestionMetadata:
    """Stable semantics used to decide whether a saved answer can be reused."""

    question_id: str
    category: str
    value_type: str
    sensitivity: str
    scope_type: str
    supports_expiry: bool
    reusable: bool = True
    candidate_fact_type: str | None = None
    allowed_scope_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class AnswerConfirmation:
    """A user-confirmed questionnaire answer with its provenance and scope."""

    question_id: str
    category: str
    question: str
    value: str
    answer_state: str
    value_type: str
    sensitivity: str
    scope_type: str
    scope_country: str = ''
    scope_employer: str = ''
    valid_until: str | None = None
    source_ref: str = 'onboarding'


_MOTIVATION_QUESTIONS = {
    'summary', 'career_goals', 'strengths', 'challenge', 'teamwork', 'motivation'
}
_PREFERENCE_QUESTIONS = {
    'contact_language', 'contact_times', 'target_roles', 'excluded_roles', 'locations',
    'relocation', 'relocation_limits', 'workplace', 'contract', 'hours', 'travel',
    'commute', 'start_date', 'shifts', 'overtime', 'planned_absence', 'salary',
    'salary_flexibility', 'interview_availability', 'skills_for_work', 'learning_goals',
}
_COUNTRY_SCOPED_QUESTIONS = {
    'eligibility:work_permission',
    'eligibility:permission_limits',
    'eligibility:sponsorship_now',
    'eligibility:sponsorship_future',
    'eligibility:professional_registration',
}
_EMPLOYER_SCOPED_QUESTIONS = {
    'availability:salary',
    'availability:notice_period',
    'availability:start_date',
    'availability:shifts',
}
_EXPIRY_QUESTIONS = {
    'eligibility:work_permission',
    'eligibility:permission_limits',
    'eligibility:professional_registration',
    'experience:certificates',
    'experience:licences',
}
_REPEATABLE_QUESTIONS = {
    'employment_history', 'achievements', 'volunteering', 'education', 'training',
    'certificates', 'licences', 'languages', 'technical_skills', 'practical_skills',
    'people_skills', 'management_skills', 'creative_skills', 'other_skills',
    'skill_evidence', 'portfolio', 'professional_links',
}
_DATE_QUESTIONS = {'start_date', 'planned_absence'}
_CONTACT_LINK_QUESTIONS = {'email', 'phone', 'portfolio', 'professional_links'}
_CANDIDATE_FACT_QUESTIONS = {
    'skills:technical_skills': 'skill',
    'skills:practical_skills': 'skill',
    'skills:people_skills': 'skill',
    'skills:management_skills': 'skill',
    'skills:creative_skills': 'skill',
    'skills:other_skills': 'transferable_skill',
    'skills:languages': 'language',
    'experience:certificates': 'certificate',
    'experience:licences': 'licence',
}
_PERSONAL_QUESTIONS = {
    'full_name', 'email', 'phone', 'city', 'address', 'postal_code',
    'employment_status', 'reference_contact',
}
_HIGH_SENSITIVITY_QUESTIONS = {
    'work_permission', 'permission_limits', 'sponsorship_now', 'sponsorship_future',
    'professional_registration',
}


def _normalized_label(value: str) -> str:
    return ' '.join(re.findall(r"[^\W_]+", value.casefold(), flags=re.UNICODE))


# Stable IDs are language-independent; localized labels are aliases, not new
# answer scopes. Keep ambiguous employer-specific prompts out of this registry.
_QUESTION_LABEL_ALIASES = {
    'availability:salary': (
        'salary expectation', 'what is your salary expectation', 'expected salary', 'what is your expected salary', 'expected gross pay',
        'palkkatoiveesi', 'palkkatoive', 'mikä on palkkatoiveesi',
        'löneanspråk', 'vilket löneanspråk har du',
    ),
    'availability:start_date': (
        'when can you start at the earliest', 'when can you start', 'earliest available start date', 'milloin voit aloittaa aikaisintaan',
        'milloin voit aloittaa', 'när kan du tidigast börja', 'när kan du börja',
    ),
    'availability:notice_period': ('irtisanomisaika', 'uppsägningstid'),
    'availability:shifts': ('available days and shifts', 'käytettävissä olevat työvuorot', 'tillgängliga arbetsskift'),
    'eligibility:work_country': (
        'which country does your right to work apply to',
        'minkä maan osalta sinulla on työnteko-oikeus',
        'i vilket land gäller din rätt att arbeta',
    ),
    'eligibility:work_permission': (
        'do you have the right to work in finland',
        'do you have the right to work in this country',
        'onko sinulla oikeus työskennellä suomessa',
        'har du rätt att arbeta i finland',
        'do you currently have permission to work in that country',
    ),
    'preferences:relocation': ('are you willing to relocate', 'oletko valmis muuttamaan', 'är du villig att flytta'),
    'skills:languages': (
        'languages and separate spoken written proficiency levels',
        'mitä kieliä puhut ja kirjoitat', 'språk och separata nivåer för tal och skrift',
    ),
    'experience:licences': (
        'driving licence categories', 'ajokorttiluokat', 'körkortskategorier',
    ),
    'experience:education': (
        'education background', 'koulutustausta', 'utbildningsbakgrund',
    ),
    'experience:employment_history': (
        'employment history', 'työhistoria', 'työkokemus', 'arbetshistorik', 'arbetslivserfarenhet',
    ),
    'materials:references_available': (
        'references available', 'can references be supplied', 'suosittelijat saatavilla', 'referenser tillgängliga',
    ),
}

_FORM_LABEL_ALIASES = {
    'preferences:locations': (
        'preferred work location', 'preferred location', 'work location preference',
        'where would you like to work', 'toivottu työskentelypaikka', 'önskad arbetsplats',
    ),
    'preferences:contract': (
        'preferred employment type', 'employment type preference', 'contract type preference',
        'toivottu työsuhteen tyyppi', 'önskad anställningsform',
    ),
    'preferences:hours': (
        'preferred weekly hours', 'weekly hours', 'full time or part time preference',
        'toivotut viikkotyötunnit', 'önskad arbetstid per vecka',
    ),
    'preferences:relocation': _QUESTION_LABEL_ALIASES['preferences:relocation'],
    'preferences:travel': (
        'willing to travel', 'travel availability', 'halukkuus matkustaa työn vuoksi', 'kan du resa i arbetet',
    ),
    'preferences:workplace': (
        'workplace preference', 'preferred work arrangement', 'etä hybridi vai lähityö', 'distans hybrid eller på plats',
    ),
    'availability:employment_status': (
        'current employment status', 'are you currently employed', 'nykyinen työtilanne', 'nuvarande sysselsättning',
    ),
    'availability:start_date': _QUESTION_LABEL_ALIASES['availability:start_date'],
    'availability:notice_period': (
        'notice period', 'current notice period', 'irtisanomisaika', 'uppsägningstid',
    ),
    'availability:shifts': (
        'shift availability', 'available shifts', 'available days and shifts', 'which shifts can you work',
        'käytettävissä olevat työvuorot', 'mitkä työvuorot sopivat sinulle', 'työvuorot',
        'tillgängliga arbetsskift', 'vilka arbetsskift kan du arbeta',
    ),
    'availability:salary': (
        'salary expectation', 'expected gross pay', 'expected salary', 'expected hourly pay',
        'palkkatoive', 'palkkatoiveesi', 'löneanspråk', 'förväntad lön',
    ),
    'eligibility:work_country': _QUESTION_LABEL_ALIASES['eligibility:work_country'],
    'eligibility:work_permission': (
        'right to work', 'permission to work', 'work authorization', 'authorized to work', 'eligible to work',
        'työnteko-oikeus', 'oikeus työskennellä', 'työskentelyoikeus', 'rätt att arbeta', 'arbetstillstånd',
        *_QUESTION_LABEL_ALIASES['eligibility:work_permission'],
    ),
    'eligibility:sponsorship_now': (
        'need employer sponsorship now', 'require sponsorship now', 'sponsorship at present',
        'tarvitsetko työnantajan sponsorointia nyt', 'behöver du arbetsgivarsponsring nu',
    ),
    'eligibility:sponsorship_future': (
        'need employer sponsorship in the future', 'require future sponsorship',
        'tarvitsetko tulevaisuudessa työnantajan sponsorointia', 'behöver du sponsring i framtiden',
    ),
    'skills:languages': (
        'languages spoken', 'spoken and written languages', 'language proficiency',
        'what languages do you speak', 'mitä kieliä puhut', 'kielitaito',
        'språkkunskaper', 'vilka språk talar du',
    ),
    'experience:licences': (
        'driving licence categories', 'driving license categories',
        'ajokorttiluokat', 'körkortskategorier',
    ),
    'experience:education': (
        'education history', 'education background',
        'koulutustausta', 'utbildningsbakgrund',
    ),
    'experience:employment_history': (
        'employment history', 'work history', 'previous employment',
        'työhistoria', 'työkokemus', 'arbetshistorik', 'arbetslivserfarenhet',
    ),
    'experience:own_vehicle': (
        'vehicle available for work', 'own vehicle available', 'onko käytössäsi ajoneuvo', 'har du tillgång till bil',
    ),
    'materials:references_available': (
        'references available', 'can references be supplied', 'can you provide references',
        'suosittelijat saatavilla', 'voitko antaa suosittelijoita', 'referenser tillgängliga', 'kan du lämna referenser',
    ),
    'materials:reference_contact': (
        'may a current employer be contacted', 'contact my current employer',
        'saako nykyiseen työnantajaan ottaa yhteyttä', 'får vi kontakta nuvarande arbetsgivare',
    ),
}


def question_metadata(question_id: str) -> QuestionMetadata | None:
    """Describe a question without changing the compact section/field definitions."""
    try:
        section, identifier = question_id.split(':', 1)
    except ValueError:
        return None
    section_entry = SECTIONS.get(section)
    if section_entry is None:
        return None
    match = next((item for item in section_entry[1] if item[0] == identifier), None)
    if match is None:
        return None

    _, _, options = match
    if identifier in _PREFERENCE_QUESTIONS or section == 'preferences':
        category = 'PREFERENCE'
    elif section == 'materials' and identifier in _MOTIVATION_QUESTIONS:
        category = 'MOTIVATION'
    else:
        category = 'FACT'

    if options:
        value_type = 'choice'
    elif identifier in _DATE_QUESTIONS:
        value_type = 'date'
    elif identifier == 'salary':
        value_type = 'amount'
    elif identifier in _CONTACT_LINK_QUESTIONS:
        value_type = 'contact_link'
    elif identifier in _REPEATABLE_QUESTIONS:
        value_type = 'repeatable'
    else:
        value_type = 'scalar'

    if identifier in _HIGH_SENSITIVITY_QUESTIONS:
        sensitivity = 'HIGH'
    elif identifier in _PERSONAL_QUESTIONS:
        sensitivity = 'PERSONAL'
    else:
        sensitivity = 'NORMAL'

    if question_id in _COUNTRY_SCOPED_QUESTIONS:
        scope_type = 'COUNTRY'
    elif question_id == 'materials:reference_contact':
        scope_type = 'APPLICATION'
    else:
        scope_type = 'GLOBAL'

    allowed_scope_types = (scope_type,)
    if question_id in _EMPLOYER_SCOPED_QUESTIONS:
        allowed_scope_types = ('GLOBAL', 'EMPLOYER')

    return QuestionMetadata(
        question_id=question_id,
        category=category,
        value_type=value_type,
        sensitivity=sensitivity,
        scope_type=scope_type,
        supports_expiry=question_id in _EXPIRY_QUESTIONS,
        reusable=scope_type != 'APPLICATION',
        candidate_fact_type=_CANDIDATE_FACT_QUESTIONS.get(question_id),
        allowed_scope_types=allowed_scope_types,
    )


def question_id_for_label(label: str) -> str | None:
    """Find a stable question ID only when a label identifies exactly one prompt."""
    normalized = _normalized_label(label)
    matches = {
        f'{section}:{identifier}'
        for section, (_, questions) in SECTIONS.items()
        for identifier, question_label, _ in questions
        if _normalized_label(question_label) == normalized
    }
    matches.update(
        question_id
        for question_id, aliases in _QUESTION_LABEL_ALIASES.items()
        if any(_normalized_label(alias) == normalized for alias in aliases)
    )
    return next(iter(matches)) if len(matches) == 1 else None


def question_id_for_form_label(label: str) -> str | None:
    """Map a clear Finnish/Swedish/English ATS label to one stable prompt ID."""
    normalized = _normalized_label(label)
    matches = {
        question_id
        for question_id, aliases in _FORM_LABEL_ALIASES.items()
        if any(_normalized_label(alias) in normalized for alias in aliases)
    }
    if not matches:
        return question_id_for_label(label)
    return next(iter(matches)) if len(matches) == 1 else None


def answer_state_for_value(value: str) -> str:
    """Classify explicit non-answers so they can never be treated as factual yes/no."""
    normalized = _normalized_label(value)
    if normalized in {'unsure', 'not sure', 'unknown', 'uncertain', "i don't know"}:
        return 'UNKNOWN'
    if normalized in {
        'prefer not to answer', 'prefer not to say', 'decline',
        'i prefer not to answer', 'i prefer not to say',
    }:
        return 'DECLINED'
    return 'CONFIRMED'
