"""Review Agent — aggregates validation outputs and decides auto-approval.

Auto-approval criteria (per AGENT_SPECS.md section "Agent 10"):
- PRD coverage ≥ 95%
- All test suites passed
- Zero blocking security findings
- All required files present
- No flags raised by any agent
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

from agents._anthropic_client import AnthropicClient
from agents.base_agent import AgentResult, BaseAgent
from schemas.delivery import FlagForHuman, GateSummary, PRDCoverage, ReviewSummary

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


class ReviewAgent(BaseAgent):
    name = "review_agent"
    model = "claude-opus-4-5"

    def __init__(
        self,
        client: Optional[AnthropicClient] = None,
        prompt_path: Optional[Path] = None,
        max_retries: int = 3,
    ):
        super().__init__(max_retries=max_retries)
        self._client = client or AnthropicClient()
        self._prompt = (prompt_path or PROMPTS_DIR / "review_agent.md").read_text(
            encoding="utf-8"
        )

    async def run(self, input: dict) -> AgentResult:
        try:
            project_id = input["project_id"]
            prd = input["prd"]
            task_results: list[dict] = input.get("task_results", [])
            all_files: list[str] = input.get("all_files", [])
        except KeyError as e:
            return AgentResult(status="failed", error=f"missing field: {e}")

        # 1. PRD coverage — map every requirement to tasks that mention it.
        coverage = self._compute_prd_coverage(prd, task_results)

        # 2. Gate summary — aggregate test/security/files outcomes.
        gate_summary = self._compute_gate_summary(task_results, all_files)

        # 3. Flags for human — anything that fails auto-approval criteria.
        flags = self._compute_flags(coverage, gate_summary, task_results)

        # 4. Auto-approval rule.
        auto_approved = (
            coverage.coverage_percent >= 95.0
            and gate_summary.tests_passed
            and gate_summary.security_passed
            and gate_summary.quality_passed
            and gate_summary.all_files_present
            and not flags
        )
        overall = "auto_approved" if auto_approved else ("failed" if flags and any(
            f.flag_type in ("security_blocker", "test_failure") for f in flags
        ) else "needs_human")

        rationale = ""
        if auto_approved:
            rationale = (
                f"PRD coverage {coverage.coverage_percent:.1f}% ≥ 95%, "
                "all tests passed, security & quality gates green, all required files present."
            )

        summary = ReviewSummary(
            project_id=project_id,
            overall_status=overall,
            prd_coverage=coverage,
            gate_summary=gate_summary,
            flags_for_human=flags,
            auto_approval_rationale=rationale,
            merge_ready=auto_approved,
        )
        return AgentResult(
            status="success" if auto_approved else "needs_human",
            payload=summary.model_dump(mode="json"),
        )

    # -------------- helpers --------------

    @staticmethod
    def _compute_prd_coverage(prd: dict, task_results: list[dict]) -> PRDCoverage:
        req_ids = [r["req_id"] for r in prd.get("functional_requirements", [])]
        if not req_ids:
            return PRDCoverage(
                requirements_covered=[], requirements_missing=[], coverage_percent=100.0,
            )
        covered: list[str] = []
        for rid in req_ids:
            for tr in task_results:
                task = tr.get("task", {})
                ctx = task.get("context_package", {})
                if rid in ctx.get("relevant_prd_sections", []):
                    covered.append(rid)
                    break
                # Fall back: scan description / acceptance criteria for the req ID.
                blob = " ".join([
                    str(task.get("description", "")),
                    " ".join(task.get("acceptance_criteria", [])),
                ])
                if rid in blob:
                    covered.append(rid)
                    break
        missing = [r for r in req_ids if r not in covered]
        return PRDCoverage(
            requirements_covered=covered,
            requirements_missing=missing,
            coverage_percent=round(100.0 * len(covered) / len(req_ids), 2),
        )

    @staticmethod
    def _compute_gate_summary(task_results: list[dict], all_files: list[str]) -> GateSummary:
        tests_passed = all(
            (tr.get("test_result") or {}).get("status") == "passed" for tr in task_results
        )
        sec = [tr.get("security_result") or {} for tr in task_results]
        security_passed = all(s.get("status") == "passed" for s in sec)
        quality_passed = all(
            (s.get("scores") or {}).get("quality_score", 0) >= 70 for s in sec
        )
        # All files required by builder_output should appear in all_files
        required = []
        for tr in task_results:
            for f in (tr.get("builder_output") or {}).get("files_created", []):
                if isinstance(f, dict):
                    required.append(f.get("path"))
        files_present = all(p in all_files for p in required if p)
        return GateSummary(
            tests_passed=bool(tests_passed),
            security_passed=bool(security_passed),
            quality_passed=bool(quality_passed),
            all_files_present=bool(files_present),
        )

    @staticmethod
    def _compute_flags(
        coverage: PRDCoverage, gate: GateSummary, task_results: list[dict]
    ) -> list[FlagForHuman]:
        flags: list[FlagForHuman] = []
        if coverage.coverage_percent < 95:
            flags.append(FlagForHuman(
                flag_type="prd_coverage_low",
                description=(
                    f"PRD coverage is {coverage.coverage_percent:.1f}%. "
                    f"Missing: {coverage.requirements_missing}"
                ),
                options=["accept and ship", "rerun planner with focus on missing", "abort"],
                recommended_option="rerun planner with focus on missing",
            ))
        if not gate.tests_passed:
            flags.append(FlagForHuman(
                flag_type="test_failure",
                description="One or more test suites failed.",
                options=["fix and rerun", "ship anyway", "abort"],
                recommended_option="fix and rerun",
            ))
        for tr in task_results:
            sec = tr.get("security_result") or {}
            blocking = sec.get("blocking_findings", [])
            if blocking:
                flags.append(FlagForHuman(
                    flag_type="security_blocker",
                    description=(
                        f"Task {tr.get('task',{}).get('task_id','?')} has "
                        f"{len(blocking)} blocking finding(s)."
                    ),
                    options=["fix before merge", "document exception", "abort"],
                    recommended_option="fix before merge",
                ))
        return flags
