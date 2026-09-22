# Architecture

SampoAgent is a single-process FastAPI application using a local SQLite database. Its pure domain services keep deterministic logic independent from the UI: candidate ingestion, role recommendations, job normalization, scoring, CV generation, and application safety policy.

Country packs provide data and terminology only; Finland is implemented in `sampoagent/country_packs/finland`. Browser automation is behind `BrowserAgent`, so a Codex or Claude adapter can be added without coupling core application logic to either provider. AI providers remain optional and should receive only normalized, relevant context.

SQLite tables cover profile, facts, career profiles, sources, jobs, applications, settings, documents, audit activity, and semantic cache. The UI is server-rendered FastAPI HTML and requires no Node build process.
