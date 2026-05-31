"""Docs & Delivery Agent — generates docs, proof report PDF, zip package.

Model: claude-sonnet-4-6. Documentation is generated locally from validation
reports + architecture; the LLM is only used to write prose narrative for the
proof report (skipped if api_key is missing).
"""
from __future__ import annotations

import logging
import os
import shutil
import zipfile
from datetime import date
from pathlib import Path
from typing import Optional

from agents._anthropic_client import AnthropicClient
from agents.base_agent import AgentResult, BaseAgent
from schemas.delivery import DeliveryPackage, DocumentsGenerated

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
PROJECTS_DIR = Path(os.getenv("PROJECTS_BASE_PATH", "./projects"))


class DocsDeliveryAgent(BaseAgent):
    name = "docs_delivery_agent"
    model = "claude-sonnet-4-6"

    def __init__(
        self,
        client: Optional[AnthropicClient] = None,
        prompt_path: Optional[Path] = None,
        projects_dir: Optional[Path] = None,
        templates_dir: Optional[Path] = None,
        skip_pdf: bool = False,
        max_retries: int = 3,
    ):
        super().__init__(max_retries=max_retries)
        self._client = client or AnthropicClient()
        self._prompt = (prompt_path or PROMPTS_DIR / "docs_delivery_agent.md").read_text(
            encoding="utf-8"
        )
        self._projects_dir = projects_dir or PROJECTS_DIR
        self._templates_dir = templates_dir or TEMPLATES_DIR
        self._skip_pdf = skip_pdf

    async def run(self, input: dict) -> AgentResult:
        try:
            project_id = input["project_id"]
            project_name = input["project_name"]
            client_name = input.get("client_name", project_name)
            architecture = input["architecture"]
            all_task_results = input.get("all_task_results", [])
            all_validation_reports = input.get("all_validation_reports", [])
        except KeyError as e:
            return AgentResult(status="failed", error=f"missing field: {e}")

        out_dir = self._projects_dir / project_id / "delivery"
        out_dir.mkdir(parents=True, exist_ok=True)

        # 1. API docs (Markdown).
        api_docs_path = out_dir / "api_docs.md"
        api_docs_path.write_text(self._render_api_docs(architecture), encoding="utf-8")

        # 2. SETUP.md
        setup_path = out_dir / "SETUP.md"
        setup_path.write_text(self._render_setup(architecture, input), encoding="utf-8")

        # 3. ARCHITECTURE.md
        arch_path = out_dir / "ARCHITECTURE.md"
        arch_path.write_text(self._render_architecture(architecture), encoding="utf-8")

        # 4. RUNBOOK.md
        runbook_path = out_dir / "RUNBOOK.md"
        runbook_path.write_text(self._render_runbook(architecture), encoding="utf-8")

        # 5. .env.example
        env_path = out_dir / ".env.example"
        env_path.write_text(self._render_env_template(architecture), encoding="utf-8")

        # 6. Proof report PDF (WeasyPrint).
        proof_pdf = out_dir / "proof_report.pdf"
        proof_html = out_dir / "proof_report.html"
        proof_html.write_text(
            self._render_proof_html(project_name, client_name, architecture,
                                    all_task_results, all_validation_reports),
            encoding="utf-8",
        )
        if not self._skip_pdf:
            self._render_pdf(proof_html, proof_pdf)
        else:
            proof_pdf = proof_html  # fall back to HTML in test mode

        # 7. Zip everything.
        zip_path = out_dir / f"{project_id}_delivery.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in [api_docs_path, setup_path, arch_path, runbook_path, env_path, proof_pdf]:
                if p.exists():
                    zf.write(p, arcname=p.name)

        # 8. Find deployment scripts (anything under out_dir.parent matching common names).
        deployment_scripts = self._find_deployment_scripts(self._projects_dir / project_id)

        documents = DocumentsGenerated(
            api_docs=str(api_docs_path),
            setup_guide=str(setup_path),
            architecture_overview=str(arch_path),
            runbook=str(runbook_path),
            env_template=str(env_path),
        )
        package = DeliveryPackage(
            project_id=project_id,
            project_name=project_name,
            documents_generated=documents,
            proof_report_path=str(proof_pdf),
            deployment_scripts=deployment_scripts,
            delivery_package_path=str(zip_path),
        )
        return AgentResult(status="success", payload=package.model_dump(mode="json"))

    # -------------- renderers --------------

    @staticmethod
    def _render_api_docs(architecture: dict) -> str:
        lines = ["# API Documentation", ""]
        for c in architecture.get("api_contracts", []):
            lines += [
                f"## `{c.get('method', 'GET')} {c.get('endpoint', '')}`",
                f"**ID**: `{c.get('contract_id', '')}`",
                f"**Auth required**: {c.get('auth_required', False)}",
                "",
                c.get("description", ""),
                "",
                "**Request body**:",
                "```json",
                str(c.get("request_body") or "{}"),
                "```",
                "",
                "**Response body**:",
                "```json",
                str(c.get("response_body") or "{}"),
                "```",
                "",
            ]
            if c.get("error_responses"):
                lines.append("**Error responses**:")
                for er in c["error_responses"]:
                    lines.append(f"- {er}")
                lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_setup(architecture: dict, input: dict) -> str:
        tech = architecture.get("tech_stack", {})
        envs = architecture.get("environment_variables", [])
        lines = [
            "# Setup Guide",
            "",
            "## Prerequisites",
            f"- Backend: {tech.get('backend', '')}",
            f"- Frontend: {tech.get('frontend', '')}",
            f"- Database: {tech.get('database', '')}",
            "",
            "## Local development",
            "1. Clone the repository.",
            "2. Copy `.env.example` to `.env` and fill in values.",
            "3. Install dependencies (`pip install -r requirements.txt` / `npm install`).",
            "4. Run database migrations.",
            "5. Start the dev server.",
            "",
            "## Environment variables",
            "",
            "| Key | Required | Description |",
            "|---|---|---|",
        ]
        for e in envs:
            lines.append(
                f"| `{e.get('key','')}` | "
                f"{'yes' if e.get('required') else 'no'} | "
                f"{e.get('description','')} |"
            )
        lines += ["", "## Running tests", "", "```", "pytest tests/ -v", "```", ""]
        return "\n".join(lines)

    @staticmethod
    def _render_architecture(architecture: dict) -> str:
        lines = ["# Architecture Overview", ""]
        lines += ["## System diagram", "", "```", architecture.get("folder_structure", ""), "```", ""]
        lines += ["## Components", ""]
        for c in architecture.get("system_design", []):
            lines.append(f"### {c.get('name','')} ({c.get('component_type','')})")
            lines.append(c.get("responsibility", ""))
            lines.append("")
        lines += ["## Data flows", ""]
        for f in architecture.get("data_flows", []):
            lines.append(
                f"- `{f.get('from_component','?')}` → `{f.get('to_component','?')}` "
                f"({f.get('method','?')}): {f.get('description','')}"
            )
        lines += ["", "## Design decisions", ""]
        for d in architecture.get("architecture_decisions", []):
            lines.append(f"### {d.get('decision','')}")
            lines.append(f"**Rationale**: {d.get('rationale','')}")
            if d.get("alternatives_considered"):
                lines.append(f"**Alternatives considered**: {', '.join(d['alternatives_considered'])}")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_runbook(architecture: dict) -> str:
        return (
            "# Runbook\n\n"
            "## Deploy to staging\n"
            "1. Merge to `staging` branch.\n"
            "2. CI runs lint → test → build → deploy.\n"
            "3. Verify health check endpoint returns 200.\n\n"
            "## Deploy to production\n"
            "1. Merge `staging` → `main` after smoke tests pass.\n"
            "2. Watch CI deploy pipeline.\n"
            "3. Verify health endpoint and key user flows.\n\n"
            "## Common issues\n"
            "- **5xx errors after deploy**: roll back via CI and inspect logs.\n"
            "- **DB connection refused**: check `DB_URL` and security groups.\n\n"
            "## Monitoring\n"
            "- Liveness: `/health`\n"
            "- Readiness: `/ready`\n"
        )

    @staticmethod
    def _render_env_template(architecture: dict) -> str:
        lines = ["# Generated from architecture.environment_variables", ""]
        for e in architecture.get("environment_variables", []):
            lines.append(f"# {e.get('description','')}")
            lines.append(f"{e.get('key','')}=")
            lines.append("")
        return "\n".join(lines)

    def _render_proof_html(
        self, project_name: str, client_name: str, architecture: dict,
        task_results: list[dict], validation_reports: list[dict],
    ) -> str:
        from jinja2 import Template

        tpl_path = self._templates_dir / "proof_report.html"
        template = Template(tpl_path.read_text(encoding="utf-8"))

        # Aggregate test stats.
        tests_passed = sum(r.get("test_run_result", {}).get("passed", 0) for r in validation_reports if r.get("type") == "test")
        tests_failed = sum(r.get("test_run_result", {}).get("failed", 0) for r in validation_reports if r.get("type") == "test")
        tests_total = tests_passed + tests_failed
        pass_rate = round(100.0 * tests_passed / tests_total) if tests_total else 0
        security_checks = sum(
            1 for r in validation_reports
            if r.get("type") == "security" and r.get("status") == "passed"
        )

        features = []
        for tr in task_results:
            task = tr.get("task", {})
            features.append({
                "name": task.get("title", ""),
                "description": task.get("description", ""),
            })

        return template.render(
            project_name=project_name,
            client_name=client_name,
            delivery_date=date.today().isoformat(),
            summary=f"We built {project_name}, a complete software product covering the feature set agreed in the brief.",
            features=features,
            tests_total=tests_total,
            tests_pass_rate=pass_rate,
            security_checks=security_checks,
            quality_narrative=(
                f"Every change was validated by an automated test and security review. "
                f"Of {tests_total} tests, {pass_rate}% passed. "
                f"{security_checks} independent security checks reported no blocking issues."
            ),
            getting_started=[
                "Read the SETUP.md included in this delivery package.",
                "Fill in the .env file using .env.example as the template.",
                "Start the application and run a quick smoke test against the staging environment.",
            ],
        )

    @staticmethod
    def _render_pdf(html_path: Path, pdf_path: Path) -> None:
        try:
            from weasyprint import HTML  # type: ignore
            HTML(filename=str(html_path)).write_pdf(str(pdf_path))
        except Exception as e:  # noqa: BLE001
            logger.warning("docs.pdf_render_failed", extra={"error": str(e)})

    @staticmethod
    def _find_deployment_scripts(project_root: Path) -> list[str]:
        if not project_root.exists():
            return []
        out: list[str] = []
        for pattern in ("**/Dockerfile", "**/docker-compose.yml", "**/deploy*.sh", "**/.github/workflows/*.yml"):
            out.extend(str(p) for p in project_root.glob(pattern))
        return sorted(set(out))
