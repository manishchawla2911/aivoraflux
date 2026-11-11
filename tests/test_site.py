"""Tests for the public marketing site (dashboard/site.py)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from core.state import Lead, get_engine, init_db
from dashboard.main import app
from dashboard.site_content import BUDGET_OPTIONS, SERVICES

VALID = {
    "full_name": "Jane Doe",
    "email": "jane@example.com",
    "company": "Acme Trading",
    "country": "United Arab Emirates",
    "industry": "E-commerce & Retail",
    "service": SERVICES[1]["name"],
    "budget": "$2,000–$5,000",
    "message": "We want an agent that triages our supplier emails.",
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'site.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("EMAIL_BACKEND", "stub")
    monkeypatch.delenv("CONTACT_INBOX_EMAIL", raising=False)
    monkeypatch.delenv("SMTP_USER", raising=False)
    import core.state as state_mod
    state_mod._engine = None
    init_db(db_url)
    return TestClient(app)


def _leads():
    with Session(get_engine()) as s:
        return s.exec(select(Lead)).all()


@pytest.mark.parametrize("path", [
    "/", "/services", "/industries", "/work", "/about", "/engagement-models", "/faqs", "/contact",
    *[f"/services/{s['slug']}" for s in SERVICES],
])
def test_pages_render_with_seo_tags(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert '<link rel="canonical"' in r.text
    assert 'property="og:title"' in r.text
    assert '<meta name="description"' in r.text
    assert "application/ld+json" in r.text


def test_homepage_copy(client):
    r = client.get("/")
    assert "Intelligence." in r.text and "Next." in r.text
    assert "Let's Build Something" in r.text
    assert "Into Reality." in r.text
    assert "Start a Conversation" in r.text
    assert 'role="tablist"' in r.text  # interactive industries


def test_unknown_service_404(client):
    assert client.get("/services/does-not-exist").status_code == 404


def test_legacy_redirects(client):
    for old, new in [("/case-studies", "/work"), ("/pricing", "/engagement-models")]:
        r = client.get(old, follow_redirects=False)
        assert r.status_code == 301
        assert r.headers["location"] == new


def test_sitemap_and_robots(client, monkeypatch):
    monkeypatch.setenv("SITE_URL", "https://example.test")
    sm = client.get("/sitemap.xml")
    assert sm.status_code == 200
    assert "https://example.test/services/ai-agent-development" in sm.text
    robots = client.get("/robots.txt").text
    assert "Sitemap: https://example.test/sitemap.xml" in robots
    assert "Disallow: /dashboard" in robots


def test_booking_and_whatsapp_config(client, monkeypatch):
    monkeypatch.setenv("BOOKING_URL", "https://cal.example/aivoraflux")
    monkeypatch.setenv("WHATSAPP_NUMBER", "+971 50 000 0000")
    r = client.get("/contact")
    assert "https://cal.example/aivoraflux" in r.text
    assert "https://wa.me/971500000000" in r.text


def test_contact_form_lists_budget_options(client):
    r = client.get("/contact")
    for b in BUDGET_OPTIONS:
        assert b in r.text


def test_contact_valid_submission_stores_lead(client):
    r = client.post("/contact", data=VALID, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/contact?submitted=1")
    leads = _leads()
    assert len(leads) == 1
    assert leads[0].email == "jane@example.com"
    assert leads[0].budget == "$2,000–$5,000"


def test_contact_emails_inbox_when_configured(client, monkeypatch):
    sent = {}

    def fake_send(to, subject, body, **kw):
        sent.update(to=to, subject=subject, body=body)
        return True

    monkeypatch.setenv("CONTACT_INBOX_EMAIL", "inbox@example.com")
    monkeypatch.setattr("dashboard.site.send_email", fake_send)
    client.post("/contact", data=VALID, follow_redirects=False)
    assert sent["to"] == "inbox@example.com"
    assert "Acme Trading" in sent["body"]
    assert _leads()[0].email_sent is True


@pytest.mark.parametrize("field,value", [
    ("email", "not-an-email"),
    ("full_name", ""),
    ("budget", "$1,000,000"),
    ("service", "Hacking"),
    ("message", "too short"),
])
def test_contact_validation_errors(client, field, value):
    r = client.post("/contact", data={**VALID, field: value}, follow_redirects=False)
    assert r.status_code == 422
    assert "has-error" in r.text
    assert _leads() == []


def test_contact_honeypot_discards(client):
    r = client.post("/contact", data={**VALID, "website": "http://spam"}, follow_redirects=False)
    assert r.status_code == 303
    assert _leads() == []


def test_service_query_preselects(client):
    name = SERVICES[2]["name"]
    r = client.get("/contact", params={"service": name})
    assert f'<option value="{name}" selected>' in r.text


def test_linkedin_link_only_when_configured(client, monkeypatch):
    assert "linkedin.com" not in client.get("/").text
    monkeypatch.setenv("LINKEDIN_URL", "https://www.linkedin.com/company/example")
    assert "https://www.linkedin.com/company/example" in client.get("/").text


def test_service_pages_have_ai_answer_block(client):
    from dashboard.site_content import DEFINITIONS
    for s in SERVICES:
        r = client.get(f"/services/{s['slug']}")
        from html import escape
        assert escape(f"What is {s['name']}?", quote=False) in r.text
        assert escape(DEFINITIONS[s["slug"]][:40], quote=False) in r.text
        assert "Last updated" in r.text


def test_llms_txt_lists_every_service(client):
    r = client.get("/llms.txt")
    assert r.status_code == 200
    assert r.text.startswith("# Aivoraflux")
    for s in SERVICES:
        assert f"/services/{s['slug']}" in r.text


def test_security_headers_public_and_internal(client):
    r = client.get("/")
    assert r.headers["strict-transport-security"].startswith("max-age=")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    internal = client.get("/dashboard")
    assert internal.headers["x-frame-options"] == "DENY"
    assert "content-security-policy" not in internal.headers


def test_titles_and_meta_description_lengths(client):
    import re
    from dashboard.site_content import INSIGHTS
    for path in ["/", "/about", "/contact", "/work", "/services", "/industries", "/faqs", "/insights", *[f"/services/{s['slug']}" for s in SERVICES], *[f"/insights/{a['slug']}" for a in INSIGHTS]]:
        html = client.get(path).text
        title = re.search(r"<title>(.*?)</title>", html).group(1)
        desc = re.search(r'<meta name="description" content="(.*?)">', html).group(1)
        assert 30 <= len(title.replace("&amp;", "&")) <= 70, (path, title)
        assert len(desc.replace("&amp;", "&")) <= 160, (path, len(desc))


def test_insights_hub_and_articles(client):
    from dashboard.site_content import INSIGHTS
    hub = client.get("/insights")
    assert hub.status_code == 200
    for a in INSIGHTS:
        r = client.get(f"/insights/{a['slug']}")
        assert r.status_code == 200
        assert '"@type": "Article"' in r.text
        assert '"@type": "FAQPage"' in r.text
        assert "Aivoraflux engineering team" in r.text
        assert f"/insights/{a['slug']}" in hub.text
    assert client.get("/insights/nope").status_code == 404


def test_seo_files_include_insights(client):
    from dashboard.site_content import INSIGHTS
    sm = client.get("/sitemap.xml").text
    llms = client.get("/llms.txt").text
    for a in INSIGHTS:
        assert f"/insights/{a['slug']}" in sm
        assert f"/insights/{a['slug']}" in llms
    assert "Content-Signal: search=yes, ai-input=yes, ai-train=no" in client.get("/robots.txt").text


def test_founder_hidden_until_filled(client):
    assert "Who you'll work with" not in client.get("/about").text


def test_pages_expanded(client):
    assert "Common questions" in client.get("/contact").text
    assert "Energy, EPC and oil and gas projects" in client.get("/industries").text
