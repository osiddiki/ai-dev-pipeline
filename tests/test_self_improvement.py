import asyncio
import sqlite3
import unittest

import aiosqlite

from agents.models import GateConfig, VerificationResult
from agents.supervisor import SupervisorPlan, TaskDefinition
from orchestrator.self_improvement import (
    CircuitBreaker,
    FailureAnalysis,
    FailureAnalyzer,
    ModelRouter,
    PlanRepairAgent,
    PromptRewriter,
    Rule,
    RuleMiner,
)


class SelfImprovementTests(unittest.TestCase):
    def test_failure_analyzer_classifies_invalid_json(self):
        task = TaskDefinition(id="task_1", description="Create config", target_files=["config.json"])
        result = VerificationResult(success=False, reason="Invalid JSON in config.json: trailing comma")

        analysis = FailureAnalyzer().analyze(
            task=task,
            attempt=1,
            changed_files=["config.json"],
            verifier_result=result,
        )

        self.assertEqual(analysis.failure_class, "syntax_or_type_error")
        self.assertEqual(analysis.recommended_action, "rewrite_prompt")

    def test_failure_analyzer_routes_allowlist_to_plan_repair(self):
        task = TaskDefinition(id="task_1", description="Add package", target_files=["package.json"])

        analysis = FailureAnalyzer().analyze(
            task=task,
            attempt=1,
            changed_files=["package.json", "package-lock.json"],
            allowlist_error="Codex modified files outside the task allowlist.",
        )

        self.assertEqual(analysis.failure_class, "bad_allowlist")
        self.assertEqual(analysis.recommended_action, "repair_plan")

    def test_prompt_rewriter_keeps_brief_structured_and_limited(self):
        task = TaskDefinition(
            id="task_1",
            description="Create a JSON config",
            target_files=["config.json"],
            acceptance_criteria="JSON parses.",
        )
        analysis = FailureAnalysis(
            failure_class="syntax_or_type_error",
            confidence=0.9,
            evidence="Invalid JSON\n" + ("noisy log\n" * 500),
            recommended_action="rewrite_prompt",
        )

        rewrite = PromptRewriter().rewrite(
            task=task,
            issue_description="Create config",
            analysis=analysis,
            changed_files=["config.json"],
            active_rules=[Rule(id="json-1", text="Parse JSON after edits", scope="json")],
        )

        self.assertIn("Failure class: syntax_or_type_error", rewrite.repair_brief)
        self.assertIn("json-1", rewrite.repair_brief)
        self.assertIn("[truncated]", rewrite.repair_brief)
        self.assertLess(len(rewrite.repair_brief), 2600)

    def test_plan_repair_preserves_completed_tasks_and_expands_failed_allowlist(self):
        completed = TaskDefinition(id="task_0", description="Done", target_files=["done.ts"])
        failed = TaskDefinition(id="task_1", description="Add package", target_files=["package.json"])
        plan = SupervisorPlan(tasks=[completed, failed])
        analysis = FailureAnalysis(
            failure_class="bad_allowlist",
            confidence=0.8,
            evidence="package-lock.json was generated",
            recommended_action="repair_plan",
        )

        repaired, _ = PlanRepairAgent().repair(
            plan=plan,
            failed_task=failed,
            analysis=analysis,
            changed_files=["package.json", "package-lock.json"],
            completed_task_ids={"task_0"},
        )

        self.assertEqual(repaired.tasks[0].target_files, ["done.ts"])
        self.assertIn("package-lock.json", repaired.tasks[1].target_files)

    def test_model_router_policy_ladder_escalates_relevant_repeated_failures(self):
        config = GateConfig(
            model_policy="policy_ladder",
            planner_model="cheap-planner",
            verifier_model="cheap-reviewer",
            strong_planner_model="strong-planner",
            strong_verifier_model="strong-reviewer",
        )
        analysis = FailureAnalysis(
            failure_class="missing_context",
            confidence=0.7,
            evidence="Need more repo context",
            recommended_action="rewrite_prompt",
        )

        route = ModelRouter(config).route(analysis, attempt=2)

        self.assertEqual(route.planner_model, "strong-planner")
        self.assertEqual(route.verifier_model, "strong-reviewer")

    def test_circuit_breaker_stops_repeated_same_failure_and_files(self):
        breaker = CircuitBreaker()
        analyses = [
            FailureAnalysis("syntax_or_type_error", 0.8, "tsc failed", "rewrite_prompt"),
            FailureAnalysis("syntax_or_type_error", 0.8, "tsc failed", "rewrite_prompt"),
        ]

        reason = breaker.should_stop(
            analyses=analyses,
            changed_file_history=[["src/a.ts"], ["src/a.ts"]],
            latest_changed_files=["src/a.ts"],
        )

        self.assertIsNotNone(reason)

    def test_rule_miner_proposes_inactive_rules(self):
        async def run():
            db = await aiosqlite.connect(":memory:")
            db.row_factory = sqlite3.Row
            await db.executescript(
                """
                CREATE TABLE verification_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    arc_id INTEGER,
                    task_id TEXT,
                    status TEXT,
                    reason TEXT,
                    evidence TEXT,
                    changed_files TEXT
                );
                CREATE TABLE gate_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    arc_id INTEGER,
                    status TEXT,
                    error_type TEXT,
                    critique_summary TEXT
                );
                CREATE TABLE rule_proposals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_text TEXT,
                    scope TEXT,
                    source_failures TEXT,
                    confidence REAL,
                    status TEXT DEFAULT 'proposed'
                );
                INSERT INTO verification_runs (arc_id, status, reason)
                VALUES (1, 'failed', 'Invalid JSON in package.json');
                INSERT INTO gate_reviews (arc_id, status, error_type, critique_summary)
                VALUES (1, 'rejected', 'omission', 'Missed acceptance criteria');
                """
            )
            proposals = await RuleMiner().mine_arc(db, 1, "demo")
            async with db.execute("SELECT status FROM rule_proposals") as cursor:
                statuses = [row["status"] for row in await cursor.fetchall()]
            await db.close()
            return proposals, statuses

        proposals, statuses = asyncio.run(run())

        self.assertGreaterEqual(len(proposals), 2)
        self.assertTrue(all(status == "proposed" for status in statuses))

    def test_rule_miner_proposes_rule_retirement_when_approved_rule_correlates_with_failures(self):
        async def run():
            db = await aiosqlite.connect(":memory:")
            db.row_factory = sqlite3.Row
            await db.executescript(
                """
                CREATE TABLE verification_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    arc_id INTEGER,
                    status TEXT,
                    reason TEXT,
                    evidence TEXT,
                    changed_files TEXT
                );
                CREATE TABLE gate_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    arc_id INTEGER,
                    status TEXT,
                    error_type TEXT,
                    critique_summary TEXT
                );
                CREATE TABLE failure_analyses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    arc_id INTEGER,
                    task_id TEXT,
                    attempt_number INTEGER
                );
                CREATE TABLE prompt_rewrites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    arc_id INTEGER,
                    task_id TEXT,
                    attempt_number INTEGER,
                    active_rules_used TEXT
                );
                CREATE TABLE rule_proposals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_text TEXT,
                    scope TEXT,
                    source_failures TEXT,
                    confidence REAL,
                    status TEXT DEFAULT 'proposed'
                );
                INSERT INTO rule_proposals (id, rule_text, scope, confidence, status)
                VALUES (7, 'Always use pattern X', 'demo', 0.9, 'approved');
                INSERT INTO prompt_rewrites (arc_id, task_id, attempt_number, active_rules_used)
                VALUES (1, 'task_1', 1, '["db-7"]'), (1, 'task_1', 2, '["db-7"]');
                INSERT INTO failure_analyses (arc_id, task_id, attempt_number)
                VALUES (1, 'task_1', 1), (1, 'task_1', 2);
                """
            )
            proposals = await RuleMiner().mine_arc(db, 1, "demo")
            await db.close()
            return proposals

        proposals = asyncio.run(run())

        self.assertTrue(any("Retire rule db-7" in proposal["rule_text"] for proposal in proposals))


if __name__ == "__main__":
    unittest.main()
