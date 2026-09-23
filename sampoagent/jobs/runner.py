"""Bounded job discovery orchestration with an auditable per-source report."""

from dataclasses import dataclass
import re
from typing import Any

from sampoagent.jobs.adapters import (
    JobMarketFinlandAdapter,
    JsonFeedAdapter,
    RssAtomAdapter,
    ScraplingAdapter,
    SourceAdapterError,
)
from sampoagent.jobs.discovery import SearchPlan, build_search_plan, eligible_sources
from sampoagent.jobs.service import verification_state
from sampoagent.jobs.matching import matches_preferences

MAX_AUTOMATIC_SOURCES = 8


@dataclass(frozen=True)
class DiscoveryReport:
    run_id: int
    plan: SearchPlan
    jobs_found: int
    imported_count: int
    duplicates_count: int
    status: str


def _term_matches(term: str, text: str) -> bool:
    phrase = " ".join(re.findall(r"[^\W_]+", term.casefold()))
    if not phrase:
        return False
    if phrase in text:
        return True
    words = set(phrase.split())
    matched = sum(word in text for word in words)
    return matched >= 1 and matched / len(words) >= 0.6


def is_relevant_job(job: Any, *, plan: SearchPlan, preferences: dict[str, object]) -> bool:
    """Keep feed/API imports tied to explicit profile terms and saved filters."""
    normalized = {
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "description": job.description,
    }
    if not plan.terms or not matches_preferences(job=normalized, preferences=preferences):
        return False
    text = f"{job.title}\n{job.description}".casefold()
    return any(_term_matches(term, text) for term in plan.terms)


def _supported_adapter(capability: str, *, rss_adapter: Any, json_adapter: Any, api_adapter: Any, scrapling_adapter: Any) -> Any | None:
    value = capability.casefold()
    if "browser search only" in value:
        return None
    if "job market finland api" in value:
        return api_adapter
    if "scrapling public page" in value:
        return scrapling_adapter
    if "json" in value and "feed" in value:
        return json_adapter
    if "rss" in value or "atom" in value:
        return rss_adapter
    return None


def run_discovery(
    repository: Any,
    *,
    rss_adapter: Any | None = None,
    json_adapter: Any | None = None,
    api_adapter: Any | None = None,
    scrapling_adapter: Any | None = None,
    max_queries: int = 120,
) -> DiscoveryReport:
    """Run only explicitly enabled, supported sources and persist outcomes.

    Browser-only sources contribute personalized search links to the plan but
    are never fetched. Adapter errors are reduced to safe user-facing messages.
    """
    rss_adapter = rss_adapter or RssAtomAdapter()
    json_adapter = json_adapter or JsonFeedAdapter()
    scrapling_adapter = scrapling_adapter or ScraplingAdapter()
    all_sources = repository.rows("job_sources")
    facts = repository.rows("facts")
    record_types = ("experience", "education", "certificate", "licence", "language", "availability")
    candidate_records = {record_type: repository.candidate_records(record_type) for record_type in record_types}
    plan = build_search_plan(
        facts=facts,
        candidate_records=candidate_records,
        targets=repository.target_occupations(),
        career_profiles=repository.rows("career_profiles"),
        preferences=repository.preferences(),
        sources=all_sources,
        max_queries=max_queries,
    )
    run_id = repository.create_discovery_run(query_count=len(plan.queries))
    total_found = imported = duplicates = 0
    had_error = False
    automatic_sources_checked = 0
    for source in eligible_sources(all_sources, repository.preferences()):
        source_id = int(source["id"])
        name = str(source.get("name", "Job source"))
        url = str(source.get("url", ""))
        capability = str(source.get("capability", "Browser search only"))
        adapter = _supported_adapter(capability, rss_adapter=rss_adapter, json_adapter=json_adapter, api_adapter=api_adapter, scrapling_adapter=scrapling_adapter)
        found = imported_here = duplicates_here = 0
        if "browser search only" in capability.casefold():
            status = "browser_only"
            message = "Open the personalized search links; this source was not automatically fetched."
        else:
            if adapter is None and "job market finland api" in capability.casefold() and api_adapter is None:
                try:
                    adapter = JobMarketFinlandAdapter.from_environment()
                except SourceAdapterError as exc:
                    status = "not_configured"
                    message = str(exc)
                    had_error = True
            if adapter is None and status != "not_configured":
                status = "not_configured"
                message = "This source needs an explicitly configured supported feed or official API adapter."
                had_error = True
            if adapter is not None and automatic_sources_checked >= MAX_AUTOMATIC_SOURCES:
                status = "skipped_limit"
                message = f"This run checks at most {MAX_AUTOMATIC_SOURCES} automatic sources. Enable fewer feeds or run another search."
                adapter = None
            elif adapter is not None:
                automatic_sources_checked += 1
        if adapter is not None:
            try:
                jobs = adapter.search(source, plan, timeout_seconds=4.0, max_bytes=2_000_000)
                jobs = [job for job in jobs if is_relevant_job(job, plan=plan, preferences=repository.preferences())]
                found = len(jobs)
                for job in jobs:
                    verification = verification_state(
                        deadline=getattr(job, "deadline", None),
                        employer=job.company,
                        application_url=job.application_url,
                    )
                    inserted = repository.add_job(job, verification)
                    if inserted is None:
                        duplicates_here += 1
                    else:
                        imported_here += 1
                status = "imported" if imported_here else ("duplicates" if duplicates_here else "no_results")
                message = "Supported source checked successfully. Review the imported jobs before applying."
            except (SourceAdapterError, ValueError) as exc:
                status = "failed"
                message = str(exc)
                had_error = True
            except Exception:
                status = "failed"
                message = "Source check failed unexpectedly; no credentials or request details were shown."
                had_error = True
        total_found += found
        imported += imported_here
        duplicates += duplicates_here
        repository.record_discovery_source_result(
            run_id,
            source_id=source_id,
            source_name=name,
            source_url=url,
            capability=capability,
            status=status,
            jobs_found=found,
            imported_count=imported_here,
            duplicates_count=duplicates_here,
            message=message,
        )
    status = "partial" if had_error else "completed"
    summary = f"{total_found} found, {imported} imported, {duplicates} duplicates; {len(plan.queries)} personalized search links."
    repository.complete_discovery_run(
        run_id,
        status=status,
        jobs_found=total_found,
        imported_count=imported,
        duplicates_count=duplicates,
        summary=summary,
    )
    return DiscoveryReport(run_id, plan, total_found, imported, duplicates, status)
