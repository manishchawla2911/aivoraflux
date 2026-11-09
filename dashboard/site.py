"""Public marketing website for aivoraflux.com.

Routes
    GET  /                       home
    GET  /services               services overview
    GET  /services/{slug}        service detail
    GET  /industries             industries and use cases
    GET  /work                   products, platforms and illustrative capabilities
    GET  /about                  about
    GET  /engagement-models      how engagements are structured
    GET  /faqs                   FAQs
    GET  /contact                enquiry form, booking, email, WhatsApp
    POST /contact                validate, store a Lead, notify the inbox
    GET  /case-studies, /pricing 301 to /work and /engagement-models
    GET  /sitemap.xml, /robots.txt

Configuration (environment)
    SITE_URL              canonical origin, default https://aivoraflux.com
    BOOKING_URL           Calendly / Cal.com link for "Book a Strategy Call"
    PUBLIC_CONTACT_EMAIL  address shown on the site, default admin@aivoraflux.com
    WHATSAPP_NUMBER       international digits only, e.g. 971500000000 (optional)
    CONTACT_INBOX_EMAIL   where enquiries are emailed (falls back to SMTP_USER)
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from core.email_sender import send_email
from core.state import Lead, get_engine
from dashboard import site_content as C

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
router = APIRouter()

ASSET_VERSION = "32"
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
NAV = [
    ("/", "Home"),
    ("/services", "Services"),
    ("/industries", "Industries"),
    ("/work", "Our Work"),
    ("/about", "About Us"),
    ("/contact", "Contact"),
]
STATIC_PATHS = ["/", "/services", "/industries", "/work", "/about", "/engagement-models", "/faqs", "/contact"]


def _site_url() -> str:
    return os.getenv("SITE_URL", "https://aivoraflux.com").strip().rstrip("/")


def _site_context() -> dict:
    url = _site_url()
    email = os.getenv("PUBLIC_CONTACT_EMAIL", "admin@aivoraflux.com").strip()
    whatsapp = re.sub(r"\D", "", os.getenv("WHATSAPP_NUMBER", ""))
    booking = os.getenv("BOOKING_URL", "").strip()
    linkedin = os.getenv("LINKEDIN_URL", "").strip()
    return {
        "url": url,
        "default_description": C.DEFAULT_DESCRIPTION,
        "asset_version": ASSET_VERSION,
        "nav": NAV,
        "booking_url": booking or "/contact#enquiry",
        "booking_external": bool(booking),
        "services": C.SERVICES,
        "email": email,
        "whatsapp": whatsapp,
        "linkedin": linkedin if linkedin.startswith("https://") else "",
        "year": date.today().year,
        "org_schema": {
            "@context": "https://schema.org",
            "@type": "Organization",
            "name": C.SITE_NAME,
            "url": url,
            "logo": f"{url}/static/logo.png",
            "description": C.DEFAULT_DESCRIPTION,
            "email": email,
            "areaServed": ["US", "AE", "GB", "IN"],
            "knowsAbout": [s["name"] for s in C.SERVICES],
            **({"sameAs": [linkedin]} if linkedin.startswith("https://") else {}),
        },
    }


def render(request: Request, template: str, ctx: dict | None = None, status_code: int = 200) -> HTMLResponse:
    data = {
        "site": _site_context(),
        "process": C.PROCESS,
        "differentiators": C.DIFFERENTIATORS,
        "industries": C.INDUSTRIES,
        "projects": C.PROJECTS,
        "home_industries": [i for i in C.INDUSTRIES if i[0] in C.HOME_INDUSTRIES],
    }
    data.update(ctx or {})
    return templates.TemplateResponse(request, f"site/{template}", data, status_code=status_code)


# ── Pages ─────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    return render(request, "home.html")


@router.get("/services", response_class=HTMLResponse)
async def services(request: Request) -> HTMLResponse:
    return render(request, "services.html", {
        "title": "AI, Automation & Software Development Services",
        "description": "AI and machine learning, AI agents, business process automation, e-commerce growth, web and software development, and AI consulting from Aivoraflux.",
    })


@router.get("/services/{slug}", response_class=HTMLResponse)
async def service_detail(request: Request, slug: str) -> HTMLResponse:
    service = C.SERVICE_BY_SLUG.get(slug)
    if not service:
        raise HTTPException(404, "Service not found")
    related = [s for s in C.SERVICES if s["slug"] != slug][:3]
    return render(request, "service_detail.html", {
        "title": service["title"],
        "description": service["meta"],
        "service": service,
        "related": related,
    })


@router.get("/industries", response_class=HTMLResponse)
async def industries(request: Request) -> HTMLResponse:
    return render(request, "industries.html", {
        "title": "Industries We Serve",
        "description": "Practical AI and automation use cases for finance, healthcare, retail and e-commerce, real estate, manufacturing, education and professional services.",
    })


@router.get("/work", response_class=HTMLResponse)
async def work(request: Request) -> HTMLResponse:
    return render(request, "work.html", {
        "title": "Our Work",
        "description": "Aivoraflux products, internal platforms and illustrative capability demonstrations across procurement, document processing, analytics and automation.",
    })


@router.get("/about", response_class=HTMLResponse)
async def about(request: Request) -> HTMLResponse:
    return render(request, "about.html", {
        "title": "About Us",
        "description": "Aivoraflux is an AI engineering and automation consultancy that designs, builds and runs intelligent systems for growing businesses.",
    })


@router.get("/engagement-models", response_class=HTMLResponse)
async def engagement_models(request: Request) -> HTMLResponse:
    return render(request, "engagement_models.html", {
        "title": "Engagement Models",
        "description": "Work with Aivoraflux through a strategy sprint, a scoped build project or an ongoing embedded team.",
    })


@router.get("/faqs", response_class=HTMLResponse)
async def faqs(request: Request) -> HTMLResponse:
    return render(request, "faqs.html", {
        "title": "Frequently Asked Questions",
        "description": "Answers to common questions about working with Aivoraflux on AI, automation and software projects.",
        "faqs": C.FAQS,
    })


# ── Contact ───────────────────────────────────────────────────

FIELD_LIMITS = {"full_name": 120, "email": 160, "company": 160, "message": 5000}


def _contact_ctx(values: dict | None = None, errors: dict | None = None, **extra) -> dict:
    ctx = {
        "title": "Contact Us",
        "description": "Book a strategy call or send an enquiry to Aivoraflux about AI, automation or software development.",
        "countries": C.COUNTRIES,
        "industry_options": C.INDUSTRY_OPTIONS,
        "service_options": C.SERVICE_OPTIONS,
        "budget_options": C.BUDGET_OPTIONS,
        "values": values or {},
        "errors": errors or {},
    }
    ctx.update(extra)
    return ctx


def validate_enquiry(form: dict) -> tuple[dict, dict]:
    v = {k: (str(form.get(k) or "")).strip() for k in
         ("full_name", "email", "company", "country", "industry", "service", "budget", "message")}
    e: dict[str, str] = {}
    if not v["full_name"]:
        e["full_name"] = "Please enter your full name."
    if not v["email"]:
        e["email"] = "Please enter your business email."
    elif not EMAIL_RE.match(v["email"]):
        e["email"] = "Enter a valid business email."
    if not v["company"]:
        e["company"] = "Please enter your company name."
    if v["country"] not in C.COUNTRIES:
        e["country"] = "Please select your country."
    if v["industry"] not in C.INDUSTRY_OPTIONS:
        e["industry"] = "Please select your industry."
    if v["service"] not in C.SERVICE_OPTIONS:
        e["service"] = "Please select the service you need."
    if v["budget"] not in C.BUDGET_OPTIONS:
        e["budget"] = "Please select a budget range."
    if len(v["message"]) < 20:
        e["message"] = "Please describe your project (at least 20 characters)."
    for key, limit in FIELD_LIMITS.items():
        if len(v[key]) > limit and key not in e:
            e[key] = f"Please keep this under {limit} characters."
    return v, e


@router.get("/contact", response_class=HTMLResponse)
async def contact(request: Request, submitted: int = 0, error: int = 0, service: str = "") -> HTMLResponse:
    preset = {"service": service} if service in C.SERVICE_OPTIONS else {}
    return render(request, "contact.html", _contact_ctx(values=preset, submitted=bool(submitted), failed=bool(error)))


@router.post("/contact", response_class=HTMLResponse)
async def contact_submit(request: Request) -> Response:
    form = dict(await request.form())
    # Honeypot: bots fill the hidden "website" field. Pretend success, store nothing.
    if str(form.get("website") or "").strip():
        return RedirectResponse("/contact?submitted=1#enquiry", status_code=303)

    values, errors = validate_enquiry(form)
    if errors:
        return render(request, "contact.html", _contact_ctx(values, errors), status_code=422)

    lead = Lead(**values, source_path=str(request.headers.get("referer", ""))[:300])
    try:
        with Session(get_engine()) as s:
            s.add(lead)
            s.commit()
            s.refresh(lead)
    except Exception:  # pragma: no cover - database unavailable
        logger.exception("could not store lead")
        lead = None

    recipient = os.getenv("CONTACT_INBOX_EMAIL", "").strip() or os.getenv("SMTP_USER", "").strip()
    sent = False
    if recipient:
        body = "\n".join([
            "New enquiry from the aivoraflux.com contact form",
            "",
            f"Name:     {values['full_name']}",
            f"Email:    {values['email']}",
            f"Company:  {values['company']}",
            f"Country:  {values['country']}",
            f"Industry: {values['industry']}",
            f"Service:  {values['service']}",
            f"Budget:   {values['budget']}",
            "",
            "Project description:",
            values["message"],
        ])
        sent = send_email(recipient, f"New enquiry: {values['service']} from {values['company']}", body)
    if lead is not None and sent:
        try:
            with Session(get_engine()) as s:
                db_lead = s.get(Lead, lead.id)
                if db_lead:
                    db_lead.email_sent = True
                    s.add(db_lead)
                    s.commit()
        except Exception:  # pragma: no cover
            logger.exception("could not mark lead as emailed")

    if lead is None and not sent:
        return render(request, "contact.html", _contact_ctx(values, {}, failed=True), status_code=503)
    return RedirectResponse("/contact?submitted=1#enquiry", status_code=303)


# ── Redirects and SEO ─────────────────────────────────────────

@router.get("/case-studies", include_in_schema=False)
async def case_studies_redirect() -> RedirectResponse:
    return RedirectResponse("/work", status_code=301)


@router.get("/pricing", include_in_schema=False)
async def pricing_redirect() -> RedirectResponse:
    return RedirectResponse("/engagement-models", status_code=301)


@router.get("/sitemap.xml", include_in_schema=False)
async def sitemap() -> Response:
    base = _site_url()
    paths = STATIC_PATHS + [f"/services/{s['slug']}" for s in C.SERVICES]
    urls = "".join(
        f"<url><loc>{base}{p}</loc><changefreq>monthly</changefreq>"
        f"<priority>{'1.0' if p == '/' else '0.8' if p.startswith('/services') else '0.6'}</priority></url>"
        for p in paths
    )
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    return Response(xml, media_type="application/xml")


INTERNAL_PREFIXES = ["/dashboard", "/studio", "/agents", "/projects", "/settings", "/workspaces", "/observability", "/bridge", "/docs", "/redoc"]


@router.get("/robots.txt", include_in_schema=False)
async def robots() -> PlainTextResponse:
    lines = ["User-agent: *", "Allow: /"] + [f"Disallow: {p}" for p in INTERNAL_PREFIXES]
    lines += ["", f"Sitemap: {_site_url()}/sitemap.xml"]
    return PlainTextResponse("\n".join(lines) + "\n")
