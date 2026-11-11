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
from urllib.parse import urlencode
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

ASSET_VERSION = "38"
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
NAV = [
    ("/", "Home"),
    ("/services", "Services"),
    ("/industries", "Industries"),
    ("/work", "Our Work"),
    ("/insights", "Insights"),
    ("/education-training", "Education & Training"),
    ("/about", "About Us"),
    ("/contact", "Contact"),
]
STATIC_PATHS = ["/", "/services", "/industries", "/work", "/insights", "/education-training", "/about", "/engagement-models", "/faqs", "/contact"]


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
            "makesOffer": [{"@type": "Offer", "itemOffered": {"@type": "Service", "name": s["name"], "url": f"{url}/services/{s['slug']}"}} for s in C.SERVICES]
            + [{"@type": "Offer", "itemOffered": {"@type": "Service", "name": "AI Education & Professional Training", "url": f"{url}/education-training"}}],
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
    return render(request, "home.html", {
        "edu": C.EDUCATION,
        "edu_institution_cta": "/contact?" + urlencode({"service": C.EDU_INSTITUTION_OPTION, "industry": "Education"}) + "#enquiry",
    })


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
        "definition": C.DEFINITIONS.get(slug, service["intro"]),
        "service_insights": [x for x in C.INSIGHTS if x["service"] == slug],
        "updated": C.CONTENT_UPDATED,
        "updated_label": C.CONTENT_UPDATED_LABEL,
        "title": service["title"],
        "description": service["meta"],
        "service": service,
        "related": related,
    })


@router.get("/industries", response_class=HTMLResponse)
async def industries(request: Request) -> HTMLResponse:
    return render(request, "industries.html", {
        "title": "Industries We Serve",
        "description": "Practical AI and automation use cases for finance, healthcare, retail and e-commerce, manufacturing, energy, education and professional services.",
        "industry_intros": C.INDUSTRY_INTROS,
    })


@router.get("/work", response_class=HTMLResponse)
async def work(request: Request) -> HTMLResponse:
    return render(request, "work.html", {
        "title": "Our Work: AI Procurement Platform & Automation",
        "description": "Aivoraflux products, internal platforms and illustrative capability demonstrations across procurement, document processing, analytics and automation.",
    })


@router.get("/about", response_class=HTMLResponse)
async def about(request: Request) -> HTMLResponse:
    founder = C.FOUNDER if (C.FOUNDER.get("name") and C.FOUNDER.get("bio")) else None
    return render(request, "about.html", {
        "founder": founder,
        "title": "About Us: AI Engineering & Automation Team",
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
        "updated": C.CONTENT_UPDATED,
        "updated_label": C.CONTENT_UPDATED_LABEL,
    })


# ── Education & Training ──────────────────────────────────────

@router.get("/education-training", response_class=HTMLResponse)
async def education_training(request: Request) -> HTMLResponse:
    q = lambda **kw: "/contact?" + urlencode(kw) + "#enquiry"
    return render(request, "education_training.html", {
        "title": "AI Education & Professional Training Programs",
        "description": "Practical AI training for colleges, universities and companies: workshops, bootcamps, FDPs, hackathons and semester-long programmes.",
        "edu": C.EDUCATION,
        "institution_cta": q(service=C.EDU_INSTITUTION_OPTION, industry="Education"),
        "student_cta": q(service=C.EDU_STUDENT_OPTION, industry="Education"),
    })


# ── Insights ──────────────────────────────────────────────────

@router.get("/insights", response_class=HTMLResponse)
async def insights(request: Request) -> HTMLResponse:
    return render(request, "insights.html", {
        "title": "Insights: AI in Procurement & Automation",
        "description": "Practical guides on AI in procurement, vendor onboarding automation and document AI, from the Aivoraflux engineering team.",
        "articles": C.INSIGHTS,
    })


@router.get("/insights/{slug}", response_class=HTMLResponse)
async def insight_detail(request: Request, slug: str) -> HTMLResponse:
    article = C.INSIGHT_BY_SLUG.get(slug)
    if not article:
        raise HTTPException(404, "Article not found")
    others = [a for a in C.INSIGHTS if a["slug"] != slug]
    return render(request, f"insights/{slug}.html", {
        "title": article.get("seo_title") or article["title"],
        "description": article["meta"],
        "article": article,
        "author": C.INSIGHTS_AUTHOR,
        "related_service": C.SERVICE_BY_SLUG.get(article["service"]),
        "others": others,
    })


# ── Contact ───────────────────────────────────────────────────

FIELD_LIMITS = {"full_name": 120, "email": 160, "company": 160, "message": 5000}


def _contact_ctx(values: dict | None = None, errors: dict | None = None, **extra) -> dict:
    ctx = {
        "title": "Contact Us: Book an AI Strategy Call",
        "description": "Book a strategy call or send an enquiry to Aivoraflux about AI, automation or software development.",
        "countries": C.COUNTRIES,
        "industry_options": C.INDUSTRY_OPTIONS,
        "service_options": C.SERVICE_OPTIONS,
        "budget_options": C.BUDGET_OPTIONS,
        "contact_faqs": C.CONTACT_FAQS,
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
async def contact(request: Request, submitted: int = 0, error: int = 0, service: str = "", industry: str = "") -> HTMLResponse:
    preset = {"service": service} if service in C.SERVICE_OPTIONS else {}
    if industry in C.INDUSTRY_OPTIONS:
        preset["industry"] = industry
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
    paths = STATIC_PATHS + [f"/services/{s['slug']}" for s in C.SERVICES] + [f"/insights/{a['slug']}" for a in C.INSIGHTS]
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
    # Standard directives only: Lighthouse and robots validators flag unknown
    # lines such as Content-Signal as errors.
    lines = ["User-agent: *", "Allow: /"] + [f"Disallow: {p}" for p in INTERNAL_PREFIXES]
    lines += ["", f"Sitemap: {_site_url()}/sitemap.xml"]
    return PlainTextResponse("\n".join(lines) + "\n")


@router.get("/llms.txt", include_in_schema=False)
async def llms_txt() -> PlainTextResponse:
    """Plain-language summary of the site for AI assistants (llms.txt convention)."""
    base = _site_url()
    lines = [
        "# Aivoraflux",
        "",
        "> " + C.DEFAULT_DESCRIPTION,
        "",
        "Aivoraflux is an AI engineering consultancy. It builds AI agents, intelligent automation, "
        "document processing, machine learning systems, custom software and e-commerce websites, and "
        "owns a vendor & procurement intelligence platform. It works with businesses in the UAE, USA, UK, India and beyond.",
        "",
        "## Services",
    ]
    lines += [f"- [{s['name']}]({base}/services/{s['slug']}): {C.DEFINITIONS.get(s['slug'], s['short'])}" for s in C.SERVICES]
    lines += [
        f"- [AI Education & Professional Training]({base}/education-training): AI workshops, bootcamps, faculty development programmes, hackathons and semester-long training for colleges, universities and companies.",
        "",
        "## Company",
        f"- [Our work]({base}/work): own products, internal platforms and clearly labelled illustrative capabilities",
        f"- [Industries]({base}/industries): use cases by sector",
        f"- [Engagement models]({base}/engagement-models): strategy sprint, scoped build, embedded team",
        f"- [FAQs]({base}/faqs)",
        "",
        "## Insights",
        *[f"- [{a['title']}]({base}/insights/{a['slug']}): {a['meta']}" for a in C.INSIGHTS],
        f"- [About]({base}/about)",
        "",
        "## Contact",
        f"- [Contact and enquiries]({base}/contact)",
        f"- Email: {os.getenv('PUBLIC_CONTACT_EMAIL', 'admin@aivoraflux.com').strip()}",
        "",
        f"Last updated: {C.CONTENT_UPDATED}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")


# ── Security headers ──────────────────────────────────────────

# Public pages load only same-origin scripts and Google Fonts, so they get a
# strict CSP. Internal app pages load Tailwind/Alpine from a CDN and are left
# without a CSP to avoid breaking them; they still get the other headers.
PUBLIC_CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])
SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def add_cache_headers(path: str, query: str, headers) -> None:
    """Long cache for versioned static assets (?v=...), a day for the rest."""
    if not path.startswith("/static/") or "cache-control" in headers:
        return
    if "v=" in query:
        headers["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        headers["Cache-Control"] = "public, max-age=86400"


def add_security_headers(path: str, headers) -> None:
    """Set security headers on a response (CSP only on public pages)."""
    for k, v in SECURITY_HEADERS.items():
        headers.setdefault(k, v)
    if not any(path == p or path.startswith(p + "/") for p in INTERNAL_PREFIXES):
        headers.setdefault("Content-Security-Policy", PUBLIC_CSP)
