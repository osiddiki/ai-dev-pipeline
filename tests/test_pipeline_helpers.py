import asyncio
import tempfile
import unittest
from pathlib import Path

from orchestrator.pipeline import ReleaseArcOrchestrator
from agents.models import GateConfig
from agents.supervisor import TaskDefinition


class PipelineHelperTests(unittest.TestCase):
    def test_parallel_execution_requires_disjoint_allowlists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            orchestrator = ReleaseArcOrchestrator(str(repo), config=GateConfig(show_ui_steps=False))

            task_a = TaskDefinition(id="a", description="A", target_files=["src/a.ts"])
            task_b = TaskDefinition(id="b", description="B", target_files=["src/b.ts"])
            overlap = TaskDefinition(id="c", description="C", target_files=["src/a.ts"])

            self.assertTrue(orchestrator._tasks_can_run_in_parallel([task_a, task_b]))
            self.assertFalse(orchestrator._tasks_can_run_in_parallel([task_a, overlap]))

    def test_test_writer_requires_explicit_test_targets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            orchestrator = ReleaseArcOrchestrator(str(repo), config=GateConfig(show_ui_steps=False))

            task_with_tests = TaskDefinition(
                id="a",
                description="A",
                target_files=["src/app.ts", "src/app.test.ts"],
                requires_tests=True,
            )
            task_without_tests = TaskDefinition(
                id="b",
                description="B",
                target_files=["src/app.ts"],
                requires_tests=True,
            )

            self.assertTrue(orchestrator._should_run_test_writer(task_with_tests))
            self.assertFalse(orchestrator._should_run_test_writer(task_without_tests))

    def test_gather_context_reports_repo_shape_and_git_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            (repo / "src").mkdir()
            (repo / "src" / "app.ts").write_text("export const app = true;\n", encoding="utf-8")
            (repo / "package.json").write_text('{"name":"demo"}\n', encoding="utf-8")

            orchestrator = ReleaseArcOrchestrator(str(repo), config=GateConfig(show_ui_steps=False))
            orchestrator._ensure_git_repo(repo)

            context = asyncio.run(orchestrator.gather_context(repo))

        self.assertIn("Repository root:", context)
        self.assertIn("Detected languages:", context)
        self.assertIn("package.json", context)
        self.assertIn("Current git status:", context)

    def test_simple_issue_disables_semantic_rag(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            orchestrator = ReleaseArcOrchestrator(str(Path(tmpdir)), config=GateConfig(show_ui_steps=False))

            provider = orchestrator._resolve_rag_provider("Fix a typo in the README")

        self.assertEqual(provider, "disabled")

    def test_regular_issue_keeps_semantic_rag_local(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            orchestrator = ReleaseArcOrchestrator(str(Path(tmpdir)), config=GateConfig(show_ui_steps=False))

            provider = orchestrator._resolve_rag_provider("Add a new patient export generator")

        self.assertEqual(provider, "local")


if __name__ == "__main__":
    unittest.main()
