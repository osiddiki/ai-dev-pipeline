import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agents.aider_worker import AiderWorkerAgent
from agents.supervisor import TaskDefinition
from integrations.gemini_client import LLMClient


class FakeUsage:
    prompt_tokens = 10
    completion_tokens = 5


class FakeMessage:
    tool_calls = None
    content = "ok"


class FakeChoice:
    message = FakeMessage()


class FakeResponse:
    choices = [FakeChoice()]
    usage = FakeUsage()


class WorkerAndClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_aider_worker_creates_parent_directories_for_allowed_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            task = TaskDefinition(
                id="task_1",
                description="Create nested file",
                target_files=["nested/deeper/output.ts"],
            )
            worker = AiderWorkerAgent(model_id="fake-model")

            fake_proc = mock.AsyncMock()
            fake_proc.communicate.return_value = (b"", b"")
            fake_proc.returncode = 0

            with mock.patch("asyncio.create_subprocess_exec", return_value=fake_proc):
                await worker.invoke({"repo_path": str(repo)}, task)

            self.assertTrue((repo / "nested" / "deeper").is_dir())

    async def test_gemini_safety_settings_only_apply_when_enabled(self):
        async def fake_completion(**kwargs):
            captured.append(kwargs)
            return FakeResponse()

        captured = []
        old_value = os.environ.get("GATE_GEMINI_SAFETY_MODE")
        try:
            os.environ["GATE_GEMINI_SAFETY_MODE"] = "default"
            with mock.patch("integrations.gemini_client.acompletion", side_effect=fake_completion):
                await LLMClient.chat("gemini/gemini-2.5-flash", [{"role": "user", "content": "hi"}])
            self.assertNotIn("safety_settings", captured[-1])

            os.environ["GATE_GEMINI_SAFETY_MODE"] = "block_none"
            with mock.patch("integrations.gemini_client.acompletion", side_effect=fake_completion):
                await LLMClient.chat("gemini/gemini-2.5-flash", [{"role": "user", "content": "hi"}])
            self.assertIn("safety_settings", captured[-1])
        finally:
            if old_value is None:
                os.environ.pop("GATE_GEMINI_SAFETY_MODE", None)
            else:
                os.environ["GATE_GEMINI_SAFETY_MODE"] = old_value


if __name__ == "__main__":
    unittest.main()
