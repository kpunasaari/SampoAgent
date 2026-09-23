import pytest
from fastapi.testclient import TestClient

from sampoagent.app.main import create_app
from sampoagent.db.repository import Repository
from sampoagent.jobs.service import normalize_job


def test_source_crud_and_delete_preserves_imported_job_snapshot():
    repository = Repository(":memory:")
    repository.initialize()
    source_id = repository.add_source(
        name="Employer",
        url="https://example.org/careers",
        country="Finland",
        source_type="employer career site",
        notes="Careers feed",
        capability="RSS/Atom feed",
        listing_selector=".job-card",
    )
    job = normalize_job(title="Cleaner", company="Example Oy", location="Vantaa", description="Work", application_url="https://example.org/jobs/1")
    from dataclasses import replace

    repository.add_job(replace(job, source_id=source_id, source_name="Employer", source_url="https://example.org/careers"), "PARTIALLY_VERIFIED")
    repository.update_source(source_id, name="Employer Finland", url="https://example.org/jobs.xml", country="Finland", source_type="employer career site", notes="Updated", capability="JSON Feed", listing_selector="article.position")

    assert repository.source(source_id)["capability"] == "JSON Feed"
    assert repository.source(source_id)["name"] == "Employer Finland"
    assert repository.source(source_id)["listing_selector"] == "article.position"
    repository.delete_source(source_id)
    assert repository.source(source_id) is None
    assert repository.job(1)["source_name"] == "Employer"
    assert repository.job(1)["source_url"] == "https://example.org/careers"


@pytest.mark.parametrize("url", ["http://example.org", "https://user:pass@example.org", "file:///tmp/feed", "https://"])
def test_source_crud_rejects_unsafe_or_unsupported_source_definitions(url):
    repository = Repository(":memory:")
    repository.initialize()
    with pytest.raises(ValueError):
        repository.add_source(name="Bad", url=url, country="Finland", source_type="job board")

    with pytest.raises(ValueError):
        repository.add_source(name="Bad capability", url="https://example.org", country="Finland", source_type="job board", capability="Cloudflare bypass")


def test_source_ui_adds_edits_toggles_and_removes_sources(tmp_path):
    app = create_app(database_path=tmp_path / "sources-ui.db")
    client = TestClient(app)

    add = client.post("/sources", data={"name": "New board", "url": "https://example.org/jobs", "country": "Finland", "source_type": "job board", "capability": "Scrapling public page", "listing_selector": "li.role-card"}, follow_redirects=False)
    assert add.status_code == 303
    source_id = app.state.repository.rows("job_sources")[0]["id"]
    assert app.state.repository.source(source_id)["capability"] == "Scrapling public page"
    assert app.state.repository.source(source_id)["listing_selector"] == "li.role-card"
    page = client.get("/sources")
    assert "Edit source" in page.text
    assert "How to search" in page.text
    assert "Remove source" in page.text
    assert "Scrapling public page" in page.text
    assert "Job card CSS selector" in page.text

    update = client.post(f"/sources/{source_id}/edit", data={"name": "Updated", "url": "https://example.org/feed.xml", "country": "Finland", "source_type": "job board", "capability": "JSON Feed", "listing_selector": "article.position", "notes": "JSON feed"}, follow_redirects=False)
    assert update.status_code == 303
    assert app.state.repository.source(source_id)["capability"] == "JSON Feed"
    assert app.state.repository.source(source_id)["listing_selector"] == "article.position"

    client.post(f"/sources/{source_id}/toggle")
    assert app.state.repository.source(source_id)["enabled"] == 0
    client.post(f"/sources/{source_id}/delete")
    assert app.state.repository.source(source_id) is None
