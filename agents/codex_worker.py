import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import Any, List

import structlog

from .base import AgentResult, BaseAgent
from .supervisor import TaskDefinition
from .worker import WorkerResult

logger = structlog.get_logger()


class CodexWorkerAgent(BaseAgent):
    """Worker backend that delegates implementation to Codex CLI."""

    name = "codex_worker"

    def __init__(
        self,
        model_id: str = "codex-cli",
        codex_command: str = "codex",
        sandbox: str = "workspace-write",
    ):
        super().__init__(model_id=model_id)
        self.codex_command = codex_command
        self.sandbox = sandbox

    async def invoke(
        self,
        context: dict[str, Any],
        input_data: TaskDefinition,
        temperature: float = 0.3,
    ) -> AgentResult:
        repo_path = Path(context.get("repo_path", ".")).resolve()
        prompt = self.build_prompt(context, input_data)

        logger.info("Codex worker executing task", task_id=input_data.id, repo=str(repo_path))

        try:
            proc = await asyncio.create_subprocess_exec(
                self.codex_command,
                "exec",
                "--sandbox",
                self.sandbox,
                "--json",
                prompt,
                cwd=str(repo_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await proc.communicate()
        except FileNotFoundError:
            return AgentResult(
                success=False,
                output=f"Codex command not found: {self.codex_command}",
                confidence_score=0.0,
            )

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        final_message = self._extract_final_message(stdout) or stderr[-4000:]
        changed_files = self._changed_files(repo_path)
        diff = self._diff_with_untracked(repo_path, changed_files)

        if proc.returncode != 0:
            return AgentResult(
                success=False,
                output=f"Codex exited with {proc.returncode}: {final_message or stderr}",
                confidence_score=0.0,
            )

        return AgentResult(
            success=True,
            output=WorkerResult(
                task_id=input_data.id,
                diff=diff,
                linter_output="Codex completed. GATE deterministic verification is pending.",
                changed_files=changed_files,
                final_message=final_message,
            ),
        )

    def build_prompt(self, context: dict[str, Any], task: TaskDefinition) -> str:
        allowed_files = task.allowed_files
        feedback = context.get("feedback", "")
        repair_brief = context.get("repair_brief", "")
        active_rules = context.get("active_rules", "")
        issue = context.get("issue_description", "")
        guidelines = context.get("guidelines", "")
        discovery_report = context.get("discovery_report", "")

        return f"""You are the implementation worker inside the GATE autonomous development pipeline.

Original mission:
{issue}

Task id:
{task.id}

Task description:
{task.description}

Allowed files:
{json.dumps(allowed_files, indent=2)}

Design constraints:
{task.design_constraints}

Acceptance criteria:
{task.acceptance_criteria}

Project guidelines:
{guidelines}

Active learned rules:
{active_rules or "No approved learned rules are active."}

Technical discovery report:
{discovery_report}

Previous verifier feedback:
{feedback or "None"}

Repair brief for this attempt:
{repair_brief or "None"}

Rules:
- Edit the repository directly.
- Modify only the allowed files listed above. If the allowed file list is empty, make the smallest coherent change required by the task.
- Do not commit changes.
- Do not create .tmp, .bak, backup, conflict-marker, or scratch files.
- Do not run package install commands unless the task explicitly requires dependency installation.
- Prefer existing project conventions over new abstractions.
- When complete, summarize changed files and checks you ran.
"""

    def _extract_final_message(self, jsonl: str) -> str:
        final_message = ""
        for line in jsonl.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            item = event.get("item") or {}
            if event.get("type") == "item.completed" and item.get("type") == "agent_message":
                final_message = item.get("text", "") or final_message
        return final_message

    def _run_git(self, repo_path: Path, args: List[str]) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=str(repo_path),
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        return result.stdout

    def _changed_files(self, repo_path: Path) -> List[str]:
        tracked = self._run_git(repo_path, ["diff", "--name-only"]).splitlines()
        untracked = self._run_git(repo_path, ["ls-files", "--others", "--exclude-standard"]).splitlines()
        return sorted({p for p in tracked + untracked if p.strip()})

    def _diff_with_untracked(self, repo_path: Path, changed_files: List[str]) -> str:
        diff = self._run_git(repo_path, ["diff", "--no-ext-diff", "--binary"])
        untracked = self._run_git(repo_path, ["ls-files", "--others", "--exclude-standard"]).splitlines()
        chunks = [diff.rstrip()] if diff.strip() else []

        for rel_path in untracked:
            file_path = repo_path / rel_path
            if not file_path.is_file():
                continue
            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                content = "[binary or non-UTF-8 file omitted]"
            chunks.append(f"--- /dev/null\n+++ b/{rel_path}\n@@ untracked file @@\n{content[:12000]}")

        return "\n\n".join(chunk for chunk in chunks if chunk)
