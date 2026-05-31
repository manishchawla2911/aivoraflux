"""Shared builder agent base.

All builder agents (backend, frontend, integration, devops) follow the same shape:
1. Build a CLAUDE.md context file from the task spec + an agent-specific template.
2. Spawn a Claude Code subprocess that reads the file and produces code.
3. Parse the JSON summary the subprocess prints at the end.
4. Validate against BuilderOutput schema.
5. Create a branch, commit files, push.

This base class encapsulates everything except the template, branch pattern,
and any per-agent JSON fields (e.g. env_vars_required for integration).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import ValidationError

from agents._anthropic_client import extract_json
from agents.base_agent import AgentResult, BaseAgent
from schemas.task import AgentType, BuilderOutput, Task

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
PROJECTS_DIR = Path(os.getenv("PROJECTS_BASE_PATH", "./projects"))


class BuilderAgentBase(BaseAgent):
    """Common builder agent behaviour."""

    agent_type: AgentType = AgentType.BACKEND  # subclass overrides
    branch_pattern: str = "agent/builder/{task_id}"
    prompt_filename: str = "backend_agent.md"  # subclass overrides
    timeout_seconds: int = 600  # 10 minutes per spec

    def __init__(
        self,
        repo_path: Optional[str | Path] = None,
        git_manager: Optional[Any] = None,
        claude_code_runner: Optional[Callable[[Path, Path, int], str]] = None,
        prompt_path: Optional[Path] = None,
        max_retries: int = 3,
    ):
        super().__init__(max_retries=max_retries)
        self.repo_path = Path(repo_path) if repo_path else Path.cwd()
        self.git_manager = git_manager
        self._template = (
            prompt_path or PROMPTS_DIR / self.prompt_filename
        ).read_text(encoding="utf-8")
        # claude_code_runner is injected in tests; production uses real subprocess.
        self._claude_code_runner = claude_code_runner or self._default_runner

    async def run(self, input: dict) -> AgentResult:
        try:
            task = Task.model_validate(input["task"])
        except (KeyError, ValidationError) as e:
            return AgentResult(status="failed", error=f"invalid input: {e}")

        project_id = input.get("project_id", "proj-unknown")
        project_root = PROJECTS_DIR / project_id
        project_root.mkdir(parents=True, exist_ok=True)

        # 1. Build CLAUDE.md context file
        claude_md_content = self._render_template(task, input)
        claude_md_path = project_root / f"CLAUDE.{task.task_id}.md"
        claude_md_path.write_text(claude_md_content, encoding="utf-8")

        # 2. Spawn Claude Code subprocess
        try:
            stdout = await asyncio.to_thread(
                self._claude_code_runner,
                claude_md_path,
                self.repo_path,
                self.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return AgentResult(status="failed", error="Claude Code subprocess timed out")
        except Exception as e:  # noqa: BLE001
            return AgentResult(status="failed", error=f"Claude Code failed: {e}")

        # 3. Parse JSON summary
        try:
            summary = extract_json(stdout)
        except ValueError as e:
            return AgentResult(status="failed", error=f"could not parse summary: {e}")

        # 4. Git operations
        branch_name = self.branch_pattern.format(task_id=task.task_id)
        commit_sha = "unknown"
        if self.git_manager is not None:
            try:
                self.git_manager.create_branch(branch_name)
                file_paths = [
                    f.get("path") if isinstance(f, dict) else f
                    for f in summary.get("files_created", [])
                ]
                file_paths = [p for p in file_paths if p]
                commit_sha = self.git_manager.commit_files(
                    file_paths, f"{task.task_id}: {task.title}"
                )
                self.git_manager.push_branch(branch_name)
            except Exception as e:  # noqa: BLE001
                logger.warning("builder.git_op_failed", extra={"error": str(e)})

        # 5. Validate against BuilderOutput schema
        builder_output_data = {
            "task_id": task.task_id,
            "agent_type": self.agent_type.value,
            "files_created": summary.get("files_created", []),
            "tests_written": summary.get("tests_written", []),
            "branch_name": branch_name,
            "commit_sha": commit_sha,
            "notes": summary.get("notes", ""),
            "env_vars_required": summary.get("env_vars_required", []),
        }
        try:
            output = BuilderOutput.model_validate(builder_output_data)
        except ValidationError as e:
            return AgentResult(status="failed", error=f"builder output invalid: {e}")

        return AgentResult(status="success", payload=output.model_dump(mode="json"))

    # -------------- template rendering --------------

    def _render_template(self, task: Task, input: dict) -> str:
        """Fill the prompt template with task and project context.

        Uses simple {key} placeholders. Unknown placeholders are left intact
        so subclasses can override to inject more fields.
        """
        ctx = self._build_context_dict(task, input)
        out = self._template
        for k, v in ctx.items():
            out = out.replace("{" + k + "}", v)
        return out

    def _build_context_dict(self, task: Task, input: dict) -> dict[str, str]:
        """Subclasses override or extend."""
        return {
            "task_id": task.task_id,
            "title": task.title,
            "description": task.description,
            "acceptance_criteria": self._numbered(task.acceptance_criteria),
            "files_to_create": "\n".join(f"- {f}" for f in task.files_to_create),
            "api_contracts": json.dumps(task.context_package.relevant_api_contracts, indent=2),
            "schema_tables": "\n".join(f"- {t}" for t in task.context_package.relevant_schema_tables),
            "relevant_folder_structure": task.context_package.relevant_folder_structure,
            "backend_stack": str(input.get("backend_stack", "TBD")),
            "frontend_stack": str(input.get("frontend_stack", "TBD")),
            "database": str(input.get("database", "TBD")),
            "design_tokens": str(input.get("design_tokens", 'use Tailwind defaults')),
            "service_name": str(input.get("service_name", "TBD")),
            "integration_type": str(input.get("integration_type", "other")),
            "tech_stack": json.dumps(input.get("tech_stack", {}), indent=2),
            "deployment_target": str(input.get("deployment_target", "TBD")),
            "environment": str(input.get("environment", "development")),
        }

    @staticmethod
    def _numbered(items: list[str]) -> str:
        return "\n".join(f"{i+1}. {x}" for i, x in enumerate(items))

    # -------------- subprocess --------------

    @staticmethod
    def _default_runner(claude_md_path: Path, repo_path: Path, timeout: int) -> str:
        """Spawn the real `claude` CLI in the repo with the CLAUDE.md as context."""
        cmd = ["claude", "--non-interactive", "--context", str(claude_md_path)]
        proc = subprocess.run(
            cmd,
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Claude Code exited {proc.returncode}: {proc.stderr[:500]}"
            )
        return proc.stdout
