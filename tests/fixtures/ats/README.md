# Synthetic ATS-shaped form fixtures

These three directories are invented local test cases named after ATS/form
families listed in the implementation plan. The HTML is authored for SampoAgent
tests; it was not copied from, fetched from, or validated against Laura,
ReachMee, Likeit, or any employer. The directory names do not assert vendor
compatibility.

`test_ats_staging_fixtures.py` loads each file in real local Chromium through a
route fulfilled from disk. `test_synthetic_employer_e2e_posts_exact_cv_once_and_records_same_origin_receipt`
uses the Laura-shaped form against an ephemeral loopback HTTPS fake employer,
through the pinned proxy. No remote site, candidate record, account, or real
application is used. A real ATS support claim still requires an employer- or
user-controlled staging account and a separately reviewed end-to-end test.
