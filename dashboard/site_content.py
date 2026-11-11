"""Content for the public aivoraflux.com marketing site.

Kept as plain data so pages, the sitemap and structured data all read from one
source. Rule for anything added here: no invented clients, metrics, awards or
testimonials. Project outcomes stay empty until a real, verified result exists.
"""
from __future__ import annotations

SITE_NAME = "Aivoraflux"
SITE_TAGLINE = "AI engineering and automation consultancy"
# Keep under 160 characters so search results show it in full.
DEFAULT_DESCRIPTION = (
    "AI engineering consultancy automating vendor onboarding, invoices and other "
    "document-heavy workflows for businesses in the UAE, India, UK and USA."
)

PROCESS = [
    ("Discover", "Understand the business and identify opportunities. We map the current workflow, the data behind it and where AI or automation genuinely pays back."),
    ("Design", "Build the right architecture and solution strategy: systems, integrations, success measures and a phased roadmap."),
    ("Develop", "Engineer, integrate and deploy. Tested against your real data, connected to your systems, with human review where it matters."),
    ("Evolve", "Optimize, scale and improve. We monitor in production, raise accuracy and throughput, and extend to more workflows."),
]

DIFFERENTIATORS = [
    ("Custom-engineered solutions", "Systems are designed around your workflows, data and approvals rather than forced into a generic template."),
    ("Business-first thinking", "We start from the process and the cost of doing it by hand, not from the model. If a rule or a form solves it, we say so."),
    ("Scalable technology", "Clean APIs, observable pipelines and modular components, so a pilot can grow into a production system."),
    ("End-to-end implementation", "Strategy, engineering, integration, deployment and support from one team, with no hand-offs between vendors."),
    ("Intelligent automation", "Human-in-the-loop checkpoints, guardrails and audit trails are built in, so automation is safe to rely on."),
    ("Continuous optimization", "We keep measuring after launch and improve accuracy, speed and coverage as your business changes."),
]

SERVICES = [
    {
        "slug": "ai-machine-learning",
        "name": "AI & Machine Learning",
        "icon": "brain",
        "short": "Predictive models and AI-powered applications built on your own data.",
        "bullets": ["Predictive analytics", "Custom machine learning", "Intelligent decision systems", "AI-powered applications"],
        "title": "AI & Machine Learning Development Services",
        "meta": "Custom machine learning, predictive analytics and AI applications built on your business data, for companies in the UAE, India, UK and USA.",
        "hero": "Machine learning that turns your operational data into better decisions.",
        "intro": "We design, train and deploy models that forecast, classify and recommend, and we wrap them in applications your teams actually use.",
        "problems": [
            "Planning relies on spreadsheets and instinct, so forecasts miss and stock or capacity is wasted.",
            "Valuable data sits in ERPs, CRMs and files but is never turned into decisions.",
            "Previous AI pilots stayed as notebooks and never reached production.",
        ],
        "solutions": [
            ("Predictive analytics", "Demand, churn, maintenance and risk forecasting with clear accuracy reporting."),
            ("Custom ML models", "Classification, scoring and anomaly detection trained on your data."),
            ("Decision systems", "Models combined with business rules to recommend the next best action."),
            ("AI-powered applications", "Dashboards, internal tools and APIs that put model output in front of decision-makers."),
        ],
        "use_cases": [
            "Forecasting demand to reduce stock-outs and overstock",
            "Predicting equipment failures from maintenance and sensor logs",
            "Scoring leads or credit applications for faster, consistent decisions",
            "Detecting unusual transactions or invoices for review",
        ],
        "stack": ["Python", "scikit-learn", "PyTorch", "XGBoost", "PostgreSQL", "FastAPI", "Docker", "AWS / Azure / GCP"],
        "faqs": [
            ("How much data do we need?", "It depends on the problem. We assess your data in the discovery phase and tell you honestly whether a model is viable before any build starts."),
            ("Can you deploy into our cloud?", "Yes. We deploy into your own cloud account or on-premise environment so your data stays under your control."),
            ("How do we know the model is working?", "Every model ships with evaluation reports and production monitoring so accuracy is measured, not assumed."),
        ],
    },
    {
        "slug": "ai-agent-development",
        "name": "AI Agent Engineering",
        "icon": "agent",
        "short": "Custom AI agents and multi-agent workflows that complete real work.",
        "bullets": ["Custom AI agents", "Multi-agent workflows", "RAG-based applications", "Intelligent assistants"],
        "title": "AI Agent Development Company",
        "meta": "Custom AI agent development: multi-agent workflows, RAG applications and intelligent assistants integrated with your tools, with guardrails and human review.",
        "hero": "AI agents that take on real work inside your systems.",
        "intro": "We build agents that read documents, call your APIs, follow your rules and hand off to people when judgement is needed.",
        "problems": [
            "Teams spend hours searching documents, policies and past tickets for answers.",
            "Repetitive multi-step tasks move between inboxes, spreadsheets and systems by hand.",
            "Off-the-shelf chatbots cannot access your data or take action safely.",
        ],
        "solutions": [
            ("Custom AI agents", "Role-specific agents with tool access, memory and clear boundaries."),
            ("Multi-agent workflows", "Coordinated agents that plan, execute and review work across steps."),
            ("RAG applications", "Assistants grounded in your documents, with sources cited in every answer."),
            ("Intelligent assistants", "Internal copilots for support, operations, sales and engineering teams."),
        ],
        "use_cases": [
            "A procurement agent that compares vendor quotes and flags deviations",
            "A support assistant that drafts answers from your knowledge base",
            "An operations agent that triages incoming requests and routes them",
            "A research assistant that summarises contracts and policies with citations",
        ],
        "stack": ["Claude / OpenAI / open models", "LangGraph-style orchestration", "Vector databases", "Python", "FastAPI", "Guardrails & evaluation harnesses"],
        "faqs": [
            ("Are AI agents safe to use with business data?", "We design agents with guardrails, permission limits, audit logs and human approval steps for sensitive actions."),
            ("Which language models do you use?", "We are model-agnostic and choose per use case on accuracy, cost and data-residency requirements."),
            ("Can agents work with our existing tools?", "Yes. Integration with your ERP, CRM, email and internal APIs is a core part of every agent build."),
        ],
    },
    {
        "slug": "business-process-automation",
        "name": "Intelligent Automation",
        "icon": "flow",
        "short": "Workflow automation and document processing that remove manual work.",
        "bullets": ["Workflow automation", "Document processing", "Business intelligence", "Enterprise integrations"],
        "title": "Business Process Automation Services",
        "meta": "Intelligent business process automation: workflow automation, AI document processing, business intelligence and enterprise integrations that cut manual work.",
        "hero": "Automate the document-heavy workflows that slow your teams down.",
        "intro": "We combine AI document understanding, workflow engines and system integrations to move work from inboxes and spreadsheets into reliable automated flows.",
        "problems": [
            "Invoices, purchase orders, claims and forms are typed into systems by hand.",
            "Approvals stall in email threads with no visibility of where work is stuck.",
            "Data lives in disconnected systems, so reporting takes days of manual effort.",
        ],
        "solutions": [
            ("Workflow automation", "End-to-end flows with routing, approvals, SLAs and exception handling."),
            ("Intelligent document processing", "Extraction and validation from invoices, contracts, certificates and forms."),
            ("Business intelligence", "Automated reporting and dashboards from your operational data."),
            ("Enterprise integrations", "Connections to ERP, CRM, accounting and procurement systems through APIs."),
        ],
        "use_cases": [
            "Extracting invoice data and matching it to purchase orders",
            "Checking vendor registration documents for missing or expired items",
            "Routing and tracking approvals with automatic reminders",
            "Generating weekly operational reports without spreadsheets",
        ],
        "stack": ["Python", "OCR & document AI", "LLM extraction with validation", "Workflow engines", "REST & SOAP integrations", "SAP / Oracle / Microsoft Dynamics connectors"],
        "faqs": [
            ("What happens when the AI is not sure?", "Low-confidence results are routed to a person for review, so nothing is silently guessed."),
            ("Do we need to replace our systems?", "No. Automation is built around your current ERP and tools through integrations."),
            ("How quickly can we start?", "Most engagements begin with one high-volume workflow so value can be measured early."),
        ],
    },
    {
        "slug": "ecommerce-growth-optimization",
        "name": "E-commerce Optimization",
        "icon": "cart",
        "short": "Faster, higher-converting stores with AI-powered personalisation.",
        "bullets": ["Website performance optimization", "Conversion rate optimization", "AI-powered personalization", "Customer journey optimization"],
        "title": "E-commerce Website Optimization & Development",
        "meta": "Faster, higher-converting e-commerce stores: performance engineering, conversion design and AI search and recommendations for D2C brands.",
        "hero": "Online stores engineered to load fast and convert.",
        "intro": "We build and improve e-commerce websites for D2C and retail brands, combining performance engineering, conversion design and AI personalisation.",
        "problems": [
            "Slow pages and clunky mobile checkout lose customers before they buy.",
            "Every visitor sees the same products regardless of intent.",
            "Catalogue updates, search and merchandising take too much manual effort.",
        ],
        "solutions": [
            ("Performance optimization", "Core Web Vitals, image and script optimisation, and faster mobile pages."),
            ("Conversion rate optimization", "Checkout, product page and navigation improvements based on data."),
            ("AI-powered personalization", "Recommendations, smart search and product finders."),
            ("Customer journey optimization", "Analytics, funnels and retention flows across the full journey."),
        ],
        "use_cases": [
            "Rebuilding a slow storefront for faster mobile performance",
            "Adding AI product recommendations and smart search",
            "Simplifying checkout to reduce cart abandonment",
            "Automating catalogue enrichment and product descriptions",
        ],
        "stack": ["Shopify", "Next.js / headless commerce", "WooCommerce", "Algolia-style search", "Analytics & A/B testing", "Recommendation models"],
        "faqs": [
            ("Do you work with Shopify?", "Yes, including custom themes, apps and headless builds."),
            ("Can you improve our existing store instead of rebuilding?", "Yes. We usually start with a performance and conversion audit and fix the highest-impact issues first."),
            ("Do you offer website redesigns?", "Yes. Redesign and conversion optimisation are part of this service."),
        ],
    },
    {
        "slug": "web-software-development",
        "name": "Web & Software Engineering",
        "icon": "code",
        "short": "Custom web applications, SaaS platforms and enterprise software.",
        "bullets": ["Custom web applications", "SaaS platforms", "API integrations", "Enterprise software"],
        "title": "Custom Web & Software Development Services",
        "meta": "Custom web application, SaaS platform and enterprise software development with clean architecture, API integrations and AI capabilities built in.",
        "hero": "Custom software engineered for how your business actually works.",
        "intro": "From internal tools to customer-facing SaaS platforms, we design and build maintainable software with AI capabilities where they add value.",
        "problems": [
            "Off-the-shelf tools do not fit your process, so teams work around them.",
            "Legacy systems are hard to change and do not connect to modern tools.",
            "A product idea needs a reliable engineering team to reach market.",
        ],
        "solutions": [
            ("Custom web applications", "Internal portals, dashboards and operational tools."),
            ("SaaS platforms", "Multi-tenant products with authentication, billing and admin."),
            ("API integrations", "Reliable connections between your systems and third-party services."),
            ("Enterprise software", "Secure, auditable systems for regulated and complex workflows."),
        ],
        "use_cases": [
            "A vendor portal for registration, documents and quotations",
            "An internal operations dashboard pulling from several systems",
            "A customer-facing SaaS MVP built for scale",
            "Modernising a legacy application with a clean API layer",
        ],
        "stack": ["Python", "FastAPI / Django", "TypeScript", "React / Next.js", "PostgreSQL", "Docker & CI/CD", "AWS / Azure / GCP"],
        "faqs": [
            ("Do you build MVPs?", "Yes. We scope an MVP around the core workflow and build it so it can scale."),
            ("Who owns the code?", "You do. Code, documentation and infrastructure are handed over to you."),
            ("Do you provide support after launch?", "Yes. Ongoing maintenance and improvement can be agreed as part of the engagement."),
        ],
    },
    {
        "slug": "ai-consulting-strategy",
        "name": "AI Consulting & Strategy",
        "icon": "compass",
        "short": "AI readiness assessments and practical implementation roadmaps.",
        "bullets": ["AI readiness assessments", "AI implementation roadmaps", "Business process assessment", "Technology consulting"],
        "title": "AI Consulting & Strategy Services",
        "meta": "AI consulting and strategy: AI readiness assessments, process assessments and implementation roadmaps that prioritise use cases by business value and risk.",
        "hero": "A clear, practical plan for where AI will pay off in your business.",
        "intro": "We assess your processes, data and systems, then prioritise the AI and automation opportunities with the best balance of value, speed and risk.",
        "problems": [
            "Leadership wants an AI strategy but it is unclear where to start.",
            "Many ideas compete for budget with no way to compare their value.",
            "Teams are unsure about data readiness, security and governance.",
        ],
        "solutions": [
            ("AI readiness assessment", "A review of data, systems, skills and governance against your goals."),
            ("Implementation roadmap", "Prioritised use cases with scope, effort, risk and sequencing."),
            ("Business process assessment", "Mapping workflows to find the manual work worth automating."),
            ("Technology consulting", "Architecture, vendor and build-versus-buy guidance."),
        ],
        "use_cases": [
            "Identifying the top automation opportunities in procurement or finance",
            "Choosing between building and buying an AI solution",
            "Defining guardrails and governance before rolling out AI tools",
            "Creating a 90-day pilot plan with clear success measures",
        ],
        "stack": ["Process mapping", "Data readiness review", "Architecture design", "Cost and ROI modelling", "Governance frameworks"],
        "faqs": [
            ("How long does an assessment take?", "A focused assessment is typically a few weeks, depending on scope and access to stakeholders."),
            ("Will you only recommend your own services?", "No. Recommendations are vendor-neutral, including off-the-shelf tools where they fit better."),
            ("What do we receive at the end?", "A written roadmap with prioritised use cases, architecture options and next steps."),
        ],
    },
]

SERVICE_BY_SLUG = {s["slug"]: s for s in SERVICES}

INDUSTRIES = [
    ("E-commerce & Retail", "cart", [
        "Product recommendations and smart search",
        "Automated catalogue enrichment and descriptions",
        "Returns and customer-service triage",
        "Demand forecasting for inventory",
    ]),
    ("Financial Services", "bank", [
        "Document extraction for onboarding and KYC files",
        "Reconciliation and exception detection",
        "Credit and risk data preparation",
        "Assistants that answer from policy documents",
    ]),
    ("Healthcare", "pulse", [
        "Claims and pre-approval document processing",
        "Referral and scheduling workflow automation",
        "Supplier and inventory procurement automation",
        "Knowledge assistants for clinical operations teams",
    ]),
    ("Manufacturing", "factory", [
        "Supplier quotation comparison and PO matching",
        "Predictive maintenance from equipment data",
        "Quality and certificate document checks",
        "Production and demand forecasting",
    ]),
    ("Energy", "bolt", [
        "Vendor registration and pre-qualification automation",
        "Tender, bid and contract document analysis",
        "Material take-off and vendor data extraction for EPC projects",
        "Maintenance and inspection report processing",
    ]),
    ("Education", "cap", [
        "Admissions and enquiry handling assistants",
        "Document and application processing",
        "Procurement and vendor management for institutions",
        "Learning content search and summarisation",
    ]),
    ("Professional Services", "briefcase", [
        "Proposal and report first drafts from past work",
        "Contract and document review with citations",
        "Client onboarding workflow automation",
        "Internal knowledge assistants",
    ]),
]

# Kind: "product" (built by Aivoraflux), "internal" (our own platform) or
# "illustrative" (an anonymised capability demonstration, not a client project).
PROJECTS = [
    {
        "kind": "product",
        "label": "Aivoraflux product",
        "name": "Vendor & procurement intelligence platform",
        "overview": "A platform that brings vendor registration, vendor documents, quotations and approvals into one place.",
        "problem": "Procurement teams chase vendor paperwork across email and spreadsheets, and expired or missing documents are found late.",
        "solution": "A single vendor workspace where AI reads registration papers, certificates and quotations, and flags missing or expired items for the team.",
        "stack": ["AI document extraction", "Workflow & approvals", "ERP-ready integration"],
        "approach": "Configured per organisation and introduced alongside existing ERP or procurement systems.",
        "outcome": None,
        "visual": "procurement",
    },
    {
        "kind": "internal",
        "label": "Internal platform",
        "name": "Multi-agent software delivery platform",
        "overview": "Our own orchestration system that runs a team of specialised AI agents from a client brief to tested code.",
        "problem": "Turning a brief into reliable software involves many hand-offs: requirements, architecture, task planning, build, test and security review.",
        "solution": "Eleven specialised agents coordinated by an orchestrator, with human approval gates, guardrails, cost estimation and observability.",
        "stack": ["Python", "FastAPI", "SQLModel", "Claude API", "ChromaDB", "Pydantic schemas"],
        "approach": "State-machine orchestration with validated agent contracts, bounded agent-to-agent conversations and fail-open telemetry.",
        "outcome": None,
        "visual": "agents",
    },
    {
        "kind": "illustrative",
        "label": "Illustrative capability",
        "name": "Intelligent document processing",
        "overview": "An anonymised demonstration of extracting and validating data from invoices, purchase orders and certificates.",
        "problem": "High-volume documents are keyed in manually, which is slow and error-prone.",
        "solution": "Document AI extracts fields, validates them against business rules and routes low-confidence results to a reviewer.",
        "stack": ["OCR", "LLM extraction", "Validation rules", "Review queue", "ERP API"],
        "approach": "Start with one document type, measure accuracy on real samples, then expand.",
        "outcome": None,
        "visual": "documents",
    },
    {
        "kind": "illustrative",
        "label": "Illustrative capability",
        "name": "Predictive analytics system",
        "overview": "An anonymised demonstration of forecasting demand and flagging risk from historical operational data.",
        "problem": "Planning decisions rely on manual spreadsheets and lagging reports.",
        "solution": "Forecasting models with an operations dashboard and alerts when actuals drift from predictions.",
        "stack": ["Python", "Gradient boosting", "Time-series models", "PostgreSQL", "Dashboard"],
        "approach": "Baseline current accuracy, build models, then monitor them in production.",
        "outcome": None,
        "visual": "analytics",
    },
    {
        "kind": "illustrative",
        "label": "Illustrative capability",
        "name": "AI-powered business automation",
        "overview": "An anonymised demonstration of an agent that triages incoming requests, drafts responses and updates systems.",
        "problem": "Shared inboxes and request queues are handled manually, with slow and inconsistent responses.",
        "solution": "An AI agent classifies each request, gathers context, drafts the reply and updates the record, with a person approving key actions.",
        "stack": ["AI agents", "Email & ticketing integrations", "Guardrails", "Audit logging"],
        "approach": "Shadow mode first, comparing agent output with human decisions before enabling automation.",
        "outcome": None,
        "visual": "automation",
    },
]

FAQS = [
    ("What does Aivoraflux do?", "Aivoraflux is an AI engineering consultancy. We design and build AI agents, intelligent automation, document processing, machine learning systems, e-commerce websites and custom software."),
    ("Which countries do you work with?", "We work with clients internationally, with a focus on the USA, the UAE, the UK and India. Engagements run remotely with scheduled working sessions across time zones."),
    ("Do you provide strategy, or implementation too?", "Both. We can start with an assessment and roadmap, and we also engineer, integrate and deploy the solution."),
    ("How do you handle data security?", "We follow least-privilege access, can deploy into your own cloud or infrastructure, and build guardrails, audit trails and human review into automated workflows."),
    ("Can you work with our existing systems?", "Yes. Integration with existing ERP, CRM, procurement and finance systems is a standard part of our delivery."),
    ("How do we get started?", "Book a strategy call or send an enquiry. We will review your goals and propose a clear, scoped next step."),
]

COUNTRIES = [
    "United States", "United Arab Emirates", "United Kingdom", "India", "Saudi Arabia", "Qatar",
    "Kuwait", "Oman", "Bahrain", "Canada", "Australia", "Germany", "Singapore", "Other",
]
HOME_INDUSTRIES = ["Financial Services", "Healthcare", "E-commerce & Retail", "Manufacturing", "Energy", "Education"]
INDUSTRY_OPTIONS = [name for name, _, _ in INDUSTRIES] + ["Logistics & Supply Chain", "Technology / SaaS", "Other"]
SERVICE_OPTIONS = [s["name"] for s in SERVICES] + ["Vendor & procurement platform", "Not sure yet"]
BUDGET_OPTIONS = ["Under $500", "$500–$1,000", "$1,000–$2,000", "$2,000–$5,000", "$5,000+"]


# ── AI-search (AEO) content ────────────────────────────────────────────────
# Self-contained "What is X?" answers (roughly 40-60 words) placed near the top
# of each service page so AI answer engines can quote them directly.
CONTENT_UPDATED = "2026-11-10"
CONTENT_UPDATED_LABEL = "November 2026"

DEFINITIONS = {
    "ai-machine-learning": "AI and machine learning development is building models that learn from your business data to forecast, classify or recommend, such as predicting demand, scoring leads or flagging unusual invoices, and putting those predictions into the tools your team already uses, with accuracy measured in production rather than assumed.",
    "ai-agent-development": "AI agent engineering is building software agents that use a language model to complete multi-step work: reading documents, calling your systems' APIs, following business rules and handing decisions to a person when confidence is low. Production agents need guardrails, audit logs and evaluation, not just a prompt.",
    "business-process-automation": "Intelligent automation combines AI document understanding with workflow automation and system integrations, so processes like vendor onboarding, invoice matching or claims handling run end to end. Unlike rule-only RPA, it can read unstructured documents, and it routes uncertain cases to people instead of guessing.",
    "ecommerce-growth-optimization": "E-commerce optimization is improving an online store's speed, conversion rate and average order value through performance engineering, clearer product discovery, checkout fixes and AI personalisation such as smart search and recommendations, with every change measured in analytics rather than judged on opinion.",
    "web-software-development": "Custom web and software engineering means designing and building applications around your own workflows, such as internal tools, customer portals or SaaS products, with clean APIs, automated testing and AI features where they add value, so the system can be maintained and extended long after launch.",
    "ai-consulting-strategy": "AI consulting and strategy is assessing where AI and automation will create value in your business, which data and systems that depends on, and in what order to build. The output is a prioritised roadmap with a scoped first project, not a slide deck of possibilities.",
}

FAQS += [
    ("What is intelligent document processing?", "Intelligent document processing (IDP) uses AI to read documents such as invoices, purchase orders, certificates and contracts, extract the fields that matter, validate them against business rules and your systems, and send low-confidence results to a reviewer."),
    ("What is the difference between AI automation and RPA?", "RPA follows fixed rules on screens and structured data, so it breaks when formats change. AI automation can read unstructured documents like PDFs, emails and scans, understand what they contain, and route uncertain cases to a person. The two are often combined."),
    ("Can Aivoraflux work alongside SAP Ariba, Coupa or Ivalua?", "Yes. We build AI that reads vendor documents, quotations and invoices and feeds clean, checked data into the procurement or ERP suite you already use, rather than replacing it."),
    ("How much does an AI automation project cost?", "It depends on scope. Budgets range from under $500 for a small automation to $5,000+ for multi-system builds. We agree a fixed scope after a short discovery call."),
]


# ── Insights (articles) ───────────────────────────────────────────────────
# Bodies live in templates/site/insights/<slug>.html. Rules: no invented
# statistics, customers or quotes; name the author; keep dates accurate.
INSIGHTS_AUTHOR = "Aivoraflux engineering team"
INSIGHTS = [
    {
        "slug": "ai-in-procurement",
        "seo_title": "AI in Procurement: A Practical Guide",
        "title": "AI in Procurement: A Practical Guide to Automating Vendor Management",
        "meta": "Where AI helps procurement and where it doesn't, a step-by-step adoption path, and how to work alongside Ariba, Coupa, Ivalua or your ERP.",
        "dek": "Where AI genuinely helps a procurement team, where it doesn't, and a low-risk way to start.",
        "kind": "Guide",
        "published": "2026-11-11",
        "updated": "2026-11-11",
        "read_minutes": 9,
        "service": "business-process-automation",
        "faqs": [
            ("What is AI in procurement?", "AI in procurement means using machine learning and language models to read, check and route procurement documents and data, such as vendor registrations, quotations, purchase orders and invoices, so the team handles exceptions instead of every document."),
            ("Do we need to replace our procurement suite to use AI?", "No. Most of the value comes from AI that sits alongside an existing suite or ERP: it reads incoming documents, checks them against your rules and records, and passes clean, structured data into the system you already use."),
            ("Where should a procurement team start with AI?", "Start with one high-volume, document-heavy step with clear rules, such as vendor registration checks or quotation comparison. Measure the current effort, automate that step with human review on exceptions, then expand."),
            ("Is AI safe for procurement decisions?", "It is safe when it is designed with controls: confidence thresholds, human review for uncertain or high-value items, a full audit trail, and deployment in an environment that meets your data-residency rules."),
            ("How do we measure whether AI in procurement is working?", "Track the time per document or vendor before and after, the share of items handled without manual keying, the exception rate, and errors caught, such as expired certificates found before they caused a problem."),
        ],
    },
    {
        "slug": "automate-vendor-onboarding",
        "seo_title": "How to Automate Vendor Onboarding with AI",
        "title": "How to Automate Vendor Onboarding Without Replacing Your ERP",
        "meta": "A step-by-step guide to automating vendor registration and document checks with AI, while keeping your ERP or procurement suite as the system of record.",
        "dek": "A practical, step-by-step approach that keeps your ERP as the system of record.",
        "kind": "How-to",
        "published": "2026-11-11",
        "updated": "2026-11-11",
        "read_minutes": 7,
        "service": "business-process-automation",
        "faqs": [
            ("Which vendor documents can AI check?", "Common ones include trade licences, tax registrations (such as VAT TRN in the UAE or GST and PAN in India), insurance certificates, bank letters, quality certificates such as ISO, and signed declarations."),
            ("How long does it take to automate vendor onboarding?", "It depends on the number of document types and systems involved. A focused first phase usually covers a handful of the most common documents and one integration, then expands once accuracy is proven on real samples."),
            ("What happens when the AI is not sure?", "The document goes to a person in a review queue with the extracted fields and the reason it was flagged. Nothing is approved outside the rules you set."),
        ],
    },
    {
        "slug": "ai-vs-rpa-vendor-onboarding",
        "seo_title": "AI vs RPA for Vendor Onboarding",
        "title": "AI vs RPA for Vendor Onboarding: Which Fits Your Workflow?",
        "meta": "AI and RPA compared for vendor onboarding: what each does well, where each breaks, and how to combine them. Includes a decision checklist.",
        "dek": "What each approach does well, where each breaks, and when to combine them.",
        "kind": "Comparison",
        "published": "2026-11-11",
        "updated": "2026-11-11",
        "read_minutes": 6,
        "service": "ai-agent-development",
        "faqs": [
            ("What is the difference between AI and RPA?", "RPA (robotic process automation) repeats fixed steps on screens and structured data. AI can read unstructured content such as PDFs, scans and emails, interpret it and decide what to do, and route uncertain cases to people."),
            ("Can AI and RPA be used together?", "Yes. A common design uses AI to read and check documents and RPA or APIs to enter the resulting clean data into systems that have no API."),
            ("Is RPA cheaper than AI?", "For stable, rule-only tasks on structured data, RPA can be simpler. When inputs vary, such as supplier documents in many formats, maintaining RPA rules often costs more than an AI approach that handles the variation."),
        ],
    },
]
INSIGHT_BY_SLUG = {a["slug"]: a for a in INSIGHTS}

# Optional founder profile for the About page. Left empty on purpose: it is
# shown only when real details are filled in (never invent a bio).
FOUNDER = {
    "name": "",
    "role": "Founder",
    "bio": "",
    "photo": "",       # e.g. "/static/founder.jpg"
    "linkedin": "",    # https://www.linkedin.com/in/...
}

# One-paragraph context per industry (Industries page).
INDUSTRY_INTROS = {
    "E-commerce & Retail": "Retail and D2C teams handle large catalogues, frequent supplier changes and high customer-service volumes. AI helps most where the same content is created or checked repeatedly: product data, supplier documents and customer requests.",
    "Financial Services": "Banks, lenders and insurers run on documents: onboarding files, statements, claims and policies. The opportunity is reading and checking those documents faster while keeping a full audit trail for compliance.",
    "Healthcare": "Hospital groups manage claims, referrals and a large supplier base for equipment and consumables. Automation can take on the paperwork around care, such as pre-approvals and procurement, so staff time goes to patients.",
    "Manufacturing": "Manufacturers buy from many suppliers and depend on quality documentation and accurate forecasts. AI fits supplier quotations, certificate checks, maintenance data and demand planning.",
    "Energy": "Energy, EPC and oil and gas projects involve large tenders, long vendor lists and strict pre-qualification. Vendor registration, tender analysis and inspection reports are where document AI saves the most effort.",
    "Education": "Universities and school groups handle admissions enquiries, applications and institutional purchasing. Assistants and document processing reduce repetitive admin without changing how decisions are made.",
    "Professional Services": "Consultancies and service firms reuse knowledge across proposals, contracts and reports. AI can draft from past work, review documents against standards and answer questions from internal knowledge.",
}

# Contact page FAQs.
CONTACT_FAQS = [
    ("What should I include in my enquiry?", "The process or product you want to improve, the systems involved (for example your ERP or procurement suite), rough volumes such as documents per month, and any deadline. A few sentences is enough to start."),
    ("Do I need a fixed budget before contacting you?", "No. Pick the closest range on the form. We'll recommend a scoped first phase that fits it, or tell you if the problem needs a different approach."),
    ("Can you work with confidential documents?", "Yes. We can work under an NDA, use anonymised samples for early tests, and deploy into your own cloud or on-premise environment when data must stay with you."),
    ("Which countries do you work in?", "We work remotely with businesses in the UAE, India, the UK, the USA and elsewhere, with working sessions scheduled across time zones."),
]
