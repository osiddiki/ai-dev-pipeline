import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

import yaml

from agents.models import GateConfig, VerificationResult
from agents.supervisor import SupervisorPlan, TaskDefinition

FailureClass = Literal[
    "bad_plan",
    "missing_context",
    "bad_allowlist",
    "dependency_issue",
    "syntax_or_type_error",
    "scope_drift",
    "weak_prompt",
    "model_limit",
    "environment_issue",
]


@dataclass
class FailureAnalysis:
    failure_class: FailureClass
    confidence: float
    evidence: str
    recommended_action: Literal[
        "rewrite_prompt",
        "repair_plan",
        "escalate_model",
        "block_environment",
        "stop_retrying",
    ]


@dataclass
class PromptRewrite:
    repair_brief: str
    rewrite_summary: str
    active_rule_ids: list[str] = field(default_factory=list)


@dataclass
class Rule:
    id: str
    text: str
    scope: str = "global"


@dataclass
class ModelRoute:
    planner_model: str
    verifier_model: str
    classifier_model: str
    reason: str


class FailureAnalyzer:
    """Classify failed attempts into a small set of retry policies."""

    def analyze(
        self,
        *,
        task: TaskDefinition,
        attempt: int,
        changed_files: list[str],
        verifier_result: Optional[VerificationResult] = None,
        gate_critique: str = "",
        allowlist_error: str = "",
        worker_error: str = "",
        previous_failures: list[FailureAnalysis] | None = None,
    ) -> FailureAnalysis:
        evidence_parts = [
            allowlist_error,
            worker_error,
            gate_critique,
            verifier_result.reason if verifier_result else "",
            verifier_result.evidence if verifier_result else "",
        ]
        evidence = "\n".join(part for part in evidence_parts if part).strip()
        text = evidence.lower()

        if allowlist_error:
            if "outside the task allowlist" in text:
                allowed = set(task.allowed_files)
                produced = set(changed_files)
                if produced and produced - allowed:
                    return FailureAnalysis(
                        "bad_allowlist",
                        0.78,
                        allowlist_error,
                        "repair_plan",
                    )
            return FailureAnalysis("scope_drift", 0.9, allowlist_error, "rewrite_prompt")

        if any(marker in text for marker in ("aider exited", "command not found", "permission denied")):
            return FailureAnalysis("environment_issue", 0.86, evidence, "block_environment")

        if any(marker in text for marker in ("npm install failed", "dependency install failed", "cannot find module", "missing external dependency")):
            return FailureAnalysis("dependency_issue", 0.86, evidence, "block_environment")

        if any(marker in text for marker in ("invalid json", "syntax failed", "typescript", "tsc", "parse", "type error")):
            return FailureAnalysis("syntax_or_type_error", 0.84, evidence, "rewrite_prompt")

        if any(marker in text for marker in ("omission", "missed", "incomplete", "does not solve", "acceptance")):
            return FailureAnalysis("weak_prompt", 0.72, evidence, "rewrite_prompt")

        if any(marker in text for marker in ("target_files", "allowed files", "wrong file", "necessary file", "plan")):
            return FailureAnalysis("bad_plan", 0.75, evidence, "repair_plan")

        repeated_same = previous_failures and len(previous_failures) >= 2 and all(
            failure.failure_class == "weak_prompt" for failure in previous_failures[-2:]
        )
        if repeated_same or attempt >= 3:
            return FailureAnalysis("model_limit", 0.66, evidence or "Repeated attempts did not converge.", "escalate_model")

        return FailureAnalysis("missing_context", 0.58, evidence or "Failure lacks enough context.", "rewrite_prompt")


class PromptRewriter:
    """Build concise repair briefs from structured failure evidence."""

    def rewrite(
        self,
        *,
        task: TaskDefinition,
        issue_description: str,
        analysis: FailureAnalysis,
        changed_files: list[str],
        prior_diff: str = "",
        verifier_result: Optional[VerificationResult] = None,
        gate_critique: str = "",
        active_rules: list[Rule] | None = None,
    ) -> PromptRewrite:
        active_rules = active_rules or []
        evidence = analysis.evidence or gate_critique or (verifier_result.reason if verifier_result else "")
        evidence = self._compact(evidence, 1400)
        diff_summary = self._summarize_diff(prior_diff, changed_files)
        rule_lines = [f"- {rule.id}: {rule.text}" for rule in active_rules]

        sections = [
            "Repair this task. Do not start over from the mission.",
            f"Task: {task.id} - {task.description}",
            f"Failure class: {analysis.failure_class} ({analysis.confidence:.2f})",
            f"Recommended correction: {analysis.recommended_action}",
            f"Allowed files: {json.dumps(task.allowed_files)}",
            f"Acceptance criteria: {task.acceptance_criteria}",
            f"Evidence to fix:\n{evidence or 'No detailed evidence captured.'}",
            f"Prior changed files/diff summary:\n{diff_summary}",
        ]
        if rule_lines:
            sections.append("Active learned rules:\n" + "\n".join(rule_lines))
        sections.append(
            "Next attempt requirements:\n"
            "- Modify only allowed files unless a plan repair changes the allowlist.\n"
            "- Address the specific verifier/gate evidence above.\n"
            "- Keep the edit minimal and rerunnable."
        )

        return PromptRewrite(
            repair_brief="\n\n".join(sections),
            rewrite_summary=f"{analysis.failure_class}: {analysis.recommended_action}",
            active_rule_ids=[rule.id for rule in active_rules],
        )

    def _compact(self, text: str, limit: int) -> str:
        text = re.sub(r"\n{3,}", "\n\n", text.strip())
        return text[:limit] + ("\n[truncated]" if len(text) > limit else "")

    def _summarize_diff(self, diff: str, changed_files: list[str]) -> str:
        files = ", ".join(changed_files) if changed_files else "none"
        hunks = len(re.findall(r"^@@", diff, flags=re.MULTILINE))
        additions = len(re.findall(r"^\+", diff, flags=re.MULTILINE))
        removals = len(re.findall(r"^-", diff, flags=re.MULTILINE))
        return f"Changed files: {files}. Diff hunks: {hunks}, additions: {additions}, removals: {removals}."


class ModelRouter:
    """Small policy ladder for choosing models by risk and failure pattern."""

    def __init__(self, config: GateConfig):
        self.config = config

    def route(self, analysis: Optional[FailureAnalysis] = None, attempt: int = 1) -> ModelRoute:
        if self.config.model_policy == "best_always":
            return ModelRoute(
                planner_model=self.config.strong_planner_model,
                verifier_model=self.config.strong_verifier_model,
                classifier_model=self.config.strong_planner_model,
                reason="best_always",
            )

        if self.config.model_policy == "cost_first":
            return ModelRoute(
                planner_model=self.config.planner_model,
                verifier_model=self.config.verifier_model,
                classifier_model=self.config.cheap_model,
                reason="cost_first",
            )

        should_escalate = (
            analysis is not None
            and analysis.failure_class in {"bad_plan", "missing_context", "model_limit"}
            and attempt >= 2
        )
        if should_escalate:
            return ModelRoute(
                planner_model=self.config.strong_planner_model,
                verifier_model=self.config.strong_verifier_model,
                classifier_model=self.config.cheap_model,
                reason=f"policy_ladder_escalated_for_{analysis.failure_class}",
            )

        return ModelRoute(
            planner_model=self.config.planner_model,
            verifier_model=self.config.verifier_model,
            classifier_model=self.config.cheap_model,
            reason="policy_ladder_default",
        )


class PlanRepairAgent:
    """Bounded plan repair that updates pending tasks without touching completed checkpoints."""

    def repair(
        self,
        *,
        plan: SupervisorPlan,
        failed_task: TaskDefinition,
        analysis: FailureAnalysis,
        changed_files: list[str],
        completed_task_ids: set[str],
    ) -> tuple[SupervisorPlan, str]:
        repaired_tasks: list[TaskDefinition] = []
        reason = f"{analysis.failure_class}: {analysis.evidence[:500]}"

        for task in plan.tasks:
            if task.id in completed_task_ids or task.id != failed_task.id:
                repaired_tasks.append(task)
                continue

            data = task.model_dump()
            target_files = list(dict.fromkeys(task.allowed_files))
            if analysis.failure_class == "bad_allowlist":
                target_files = list(dict.fromkeys([*target_files, *changed_files]))
                data["target_files"] = target_files
                data["design_constraints"] = (
                    task.design_constraints
                    + "\nPlan repair: allowlist expanded because the implementation requires these generated/companion files."
                )
            elif analysis.failure_class in {"bad_plan", "missing_context"}:
                data["acceptance_criteria"] = (
                    task.acceptance_criteria
                    + "\nPlan repair: explicitly satisfy the failure evidence before any code review can pass."
                )
                data["design_constraints"] = task.design_constraints + f"\nFailure evidence: {analysis.evidence[:800]}"
            repaired_tasks.append(TaskDefinition(**data))

        return SupervisorPlan(tasks=repaired_tasks), reason


class RuleStore:
    """Load approved durable rules from YAML and database records."""

    def __init__(self, metadata_dir: Path):
        self.metadata_dir = metadata_dir
        self.rules_file = metadata_dir / "rules.yml"

    async def load_active_rules(self, db: Any, project_name: str) -> list[Rule]:
        rules: dict[str, Rule] = {}

        if self.rules_file.exists():
            data = yaml.safe_load(self.rules_file.read_text(encoding="utf-8")) or {}
            for item in data.get("rules", []):
                if not item.get("id") or not item.get("text"):
                    continue
                rules[item["id"]] = Rule(
                    id=str(item["id"]),
                    text=str(item["text"]),
                    scope=str(item.get("scope", "project")),
                )

        try:
            async with db.execute(
                "SELECT id, rule_text, scope FROM rule_proposals WHERE status = 'approved'"
            ) as cursor:
                rows = await cursor.fetchall()
            for row in rows:
                rule_id = f"db-{row['id']}"
                rules[rule_id] = Rule(id=rule_id, text=row["rule_text"], scope=row["scope"] or project_name)
        except Exception:
            pass

        return sorted(rules.values(), key=lambda rule: rule.id)

    def render_for_prompt(self, rules: list[Rule]) -> str:
        if not rules:
            return "No approved learned rules are active."
        return "\n".join(f"- {rule.id} [{rule.scope}]: {rule.text}" for rule in rules)


class RuleMiner:
    """Mine historical failed attempts into inactive durable rule proposals."""

    async def mine_arc(self, db: Any, arc_id: int, project_name: str) -> list[dict[str, Any]]:
        proposals: list[dict[str, Any]] = []
        failed_verifications = await self._fetch_failed_verifications(db, arc_id)
        rejected_gates = await self._fetch_rejected_gates(db, arc_id)

        temp_failures = [row for row in failed_verifications if self._contains(row["reason"], "temporary", "scratch", ".tmp")]
        if temp_failures:
            proposals.append(
                {
                    "rule_text": "Do not create temporary, backup, patch-reject, or scratch files during implementation attempts.",
                    "scope": project_name,
                    "source_failures": json.dumps([row["id"] for row in temp_failures[:5]]),
                    "confidence": 0.86,
                }
            )

        json_failures = [row for row in failed_verifications if self._contains(row["reason"], "invalid json")]
        if json_failures:
            proposals.append(
                {
                    "rule_text": "After editing JSON, parse the exact file before declaring the task complete.",
                    "scope": "json",
                    "source_failures": json.dumps([row["id"] for row in json_failures[:5]]),
                    "confidence": 0.82,
                }
            )

        ts_failures = [row for row in failed_verifications if self._contains(row["reason"], "typescript", "tsc")]
        if ts_failures:
            proposals.append(
                {
                    "rule_text": "For TypeScript changes, keep exported names and imports consistent with the nearest tsconfig project.",
                    "scope": "typescript",
                    "source_failures": json.dumps([row["id"] for row in ts_failures[:5]]),
                    "confidence": 0.78,
                }
            )

        omissions = [row for row in rejected_gates if self._contains(row["error_type"], "omission")]
        if omissions:
            proposals.append(
                {
                    "rule_text": "Before coding, restate every acceptance criterion as a checklist and verify each item against the final diff.",
                    "scope": project_name,
                    "source_failures": json.dumps([row["id"] for row in omissions[:5]]),
                    "confidence": 0.74,
                }
            )

        proposals.extend(await self._mine_retirements(db, arc_id))

        inserted = []
        for proposal in self._dedupe(proposals):
            if await self._proposal_exists(db, proposal["rule_text"], proposal["scope"]):
                continue
            cursor = await db.execute(
                """
                INSERT INTO rule_proposals (rule_text, scope, source_failures, confidence, status)
                VALUES (?, ?, ?, ?, 'proposed')
                """,
                (
                    proposal["rule_text"],
                    proposal["scope"],
                    proposal["source_failures"],
                    proposal["confidence"],
                ),
            )
            proposal["id"] = cursor.lastrowid
            inserted.append(proposal)
        await db.commit()
        return inserted

    async def _fetch_failed_verifications(self, db: Any, arc_id: int) -> list[Any]:
        async with db.execute(
            "SELECT id, reason, evidence, changed_files FROM verification_runs WHERE arc_id = ? AND status = 'failed'",
            (arc_id,),
        ) as cursor:
            return await cursor.fetchall()

    async def _fetch_rejected_gates(self, db: Any, arc_id: int) -> list[Any]:
        async with db.execute(
            "SELECT id, error_type, critique_summary FROM gate_reviews WHERE arc_id = ? AND status = 'rejected'",
            (arc_id,),
        ) as cursor:
            return await cursor.fetchall()

    async def _proposal_exists(self, db: Any, text: str, scope: str) -> bool:
        async with db.execute(
            "SELECT id FROM rule_proposals WHERE rule_text = ? AND scope = ? AND status != 'retired' LIMIT 1",
            (text, scope),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def _mine_retirements(self, db: Any, arc_id: int) -> list[dict[str, Any]]:
        try:
            async with db.execute(
                """
                SELECT p.id, p.rule_text, p.scope, COUNT(f.id) AS failure_count
                FROM rule_proposals p
                JOIN prompt_rewrites r ON r.active_rules_used LIKE '%' || 'db-' || p.id || '%'
                JOIN failure_analyses f
                  ON f.arc_id = r.arc_id
                 AND f.task_id = r.task_id
                 AND f.attempt_number = r.attempt_number
                WHERE p.status = 'approved' AND r.arc_id = ?
                GROUP BY p.id, p.rule_text, p.scope
                HAVING failure_count >= 2
                """,
                (arc_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        except Exception:
            return []

        proposals = []
        for row in rows:
            proposals.append(
                {
                    "rule_text": f"Retire rule db-{row['id']}: {row['rule_text']}",
                    "scope": row["scope"] or "global",
                    "source_failures": json.dumps({"arc_id": arc_id, "rule_id": row["id"]}),
                    "confidence": min(0.95, 0.55 + (0.1 * row["failure_count"])),
                }
            )
        return proposals

    def _contains(self, value: Any, *needles: str) -> bool:
        text = str(value or "").lower()
        return any(needle in text for needle in needles)

    def _dedupe(self, proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen = set()
        result = []
        for proposal in proposals:
            key = (proposal["scope"], proposal["rule_text"])
            if key in seen:
                continue
            seen.add(key)
            result.append(proposal)
        return result


class CircuitBreaker:
    """Stop attempts that are repeating the same failed shape."""

    def should_stop(
        self,
        *,
        analyses: list[FailureAnalysis],
        changed_file_history: list[list[str]],
        latest_changed_files: list[str],
    ) -> Optional[str]:
        if len(analyses) < 2:
            return None
        if len(changed_file_history) >= 2:
            if sorted(changed_file_history[-1]) == sorted(latest_changed_files) == sorted(changed_file_history[-2]):
                if analyses[-1].failure_class == analyses[-2].failure_class:
                    return "Two consecutive attempts produced the same changed files and failure class."
        if len(analyses) >= 2 and all(a.failure_class == "scope_drift" for a in analyses[-2:]):
            return "The worker repeatedly edited outside the task allowlist."
        if analyses[-1].failure_class in {"environment_issue", "dependency_issue"}:
            return "Failure is deterministic environment/dependency related; retrying code generation is unlikely to help."
        return None


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()
