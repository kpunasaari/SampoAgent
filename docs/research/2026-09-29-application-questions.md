# Application questionnaire research — 29 September 2026

This is a qualitative sample, not a census of all employers. No fixed questionnaire
can cover every vacancy. Employer-specific questions must remain discoverable at
application time. No candidate personal data was used in the catalogue.

## Sources and findings

- [Workable application questions](https://resources.workable.com/application-form-interview-questions): availability, shifts, pay, work eligibility, relocation, travel, skills, languages and motivation.
- [Workable form customization](https://help.workable.com/hc/en-us/articles/115012231948-Customizing-the-application-form): employers configure required, optional and custom fields. A universal profile must allow unanswered fields.
- [Kuntarekry form variations](https://kuntarekry.fi/fi/tyoelamauutiset/toita-hakemassa/erilaisia-hakulomakkeita/): reusable candidate information and employer-specific forms/attachments coexist.
- [Live Lever application sample](https://jobs.lever.co/deltasands/e7531f40-3fac-48fc-a98e-da6f4d552e83/apply): contact, professional links, authorization, sponsorship, availability, pay, relocation and employer-specific screening. Vacancy URLs may expire.
- [Greenhouse demographic questions](https://support.greenhouse.io/hc/en-us/articles/360004588971-Custom-demographic-questions): demographic questions vary and support declining to answer. They are not mandatory occupational-profile inputs.

## Implementation decisions

63 reusable prompts in seven sections: contact (9), eligibility (6), preferences
(10), availability/pay (9), experience/education (9), skills (10), materials (10).
Nine additional per-application prompts cover employer motivation, prior contact,
referrals, qualifications, checks, accommodations, demographics, references and
privacy/accuracy declarations. These are intentionally not blanket consents.

All stored answers are local drafts, not verified facts. Blank is distinct from No.
Work authorization requires a named country. Skills include technical, practical,
interpersonal, business, creative and unrestricted other abilities, with evidence
and interest in using them professionally. No claim is made that this implements
a complete occupation taxonomy or automated skill-to-job matching.

Identity document numbers, banking data, diagnoses and third-party reference
contact details are not requested in the universal questionnaire. Employer-specific
consents, sensitive questions and checks are deferred to the actual application.

The existing CV upload and fact-review pages remain the next steps. This change
does not improve CV parsing, activate automatic submission or automatically copy
unreviewed questionnaire answers into the application answer bank.
