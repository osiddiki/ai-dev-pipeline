import asyncio
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, List

import structlog

from .base import AgentResult, BaseAgent
from .supervisor import TaskDefinition
from .worker import WorkerResult

logger = structlog.get_logger()


class AiderWorkerAgent(BaseAgent):
    """Worker backend that delegates implementation to Aider CLI."""

    name = "aider_worker"

    def __init__(self, model_id: str = "deepseek/deepseek-chat"):
        super().__init__(model_id=model_id)

    async def invoke(
        self,
        context: dict[str, Any],
        input_data: TaskDefinition,
        temperature: float = 0.3,
    ) -> AgentResult:
        repo_path = Path(context.get("repo_path", ".")).resolve()
        prompt = self.build_prompt(context, input_data)

        logger.info("Aider worker executing task", task_id=input_data.id, repo=str(repo_path), model=self.model_id)

        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write(prompt)
            prompt_file = f.name

        try:
            cmd = [
                "aider",
                "--model", self.model_id,
                "--message-file", prompt_file,
                "--yes",
                "--no-auto-commits",
            ]
            
            for allowed_file in input_data.allowed_files:
                if (
                    allowed_file.endswith(".pyc") 
                    or "__pycache__" in allowed_file.split("/") 
                    or allowed_file.endswith(".tmp")
                ):
                    continue
                file_path = repo_path / allowed_file
                file_path.parent.mkdir(parents=True, exist_ok=True)
                cmd.append(allowed_file)

            import sys
            env = os.environ.copy()
            venv_bin = Path(sys.executable).parent
            if venv_bin.exists():
                env["PATH"] = f"{venv_bin}:{env.get('PATH', '')}"

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(repo_path),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await proc.communicate()
        except FileNotFoundError:
            os.unlink(prompt_file)
            return AgentResult(
                success=False,
                output=f"Aider command not found. Please ensure aider-chat is installed in the environment.",
                confidence_score=0.0,
            )
        finally:
            if os.path.exists(prompt_file):
                os.unlink(prompt_file)

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        
        final_message = "Aider completed successfully." if proc.returncode == 0 else f"Aider failed with {proc.returncode}"
        
        changed_files = self._changed_files(repo_path)
        diff = self._diff_with_untracked(repo_path, changed_files)

        if proc.returncode != 0:
            return AgentResult(
                success=False,
                output=f"Aider exited with {proc.returncode}:\n\n{stdout[-2000:]}\n{stderr[-2000:]}",
                confidence_score=0.0,
            )

        return AgentResult(
            success=True,
            output=WorkerResult(
                task_id=input_data.id,
                diff=diff,
                linter_output="Aider completed. GATE deterministic verification is pending.",
                changed_files=changed_files,
                final_message=final_message,
            ),
        )

    def build_prompt(self, context: dict[str, Any], task: TaskDefinition) -> str:
        attempt = context.get("attempt", 1)
        feedback = context.get("feedback", "")
        repair_brief = context.get("repair_brief", "")
        active_rules = context.get("active_rules", "")
        issue = context.get("issue_description", "")
        guidelines = context.get("guidelines", "")
        discovery_report = context.get("discovery_report", "")
        allowed_files = "\n".join(f"- {path}" for path in task.allowed_files) or "- No explicit allowlist provided"

        retry_context = ""
        if attempt > 1:
            retry_context = f"""
Previous attempt failure:
{feedback or "No failure summary provided."}

Repair brief for this attempt:
{repair_brief or "No repair brief provided."}
"""

        return f"""You are the implementation worker inside the GATE autonomous development pipeline.

Original mission:
{issue}

Task description:
{task.description}

Design constraints:
{task.design_constraints}

Acceptance criteria:
{task.acceptance_criteria}

Allowed files for this task:
{allowed_files}

Project guidelines:
{guidelines}

Active learned rules:
{active_rules or "No approved learned rules are active."}

Technical discovery report:
{discovery_report}
{retry_context}

Rules:
- Make the smallest coherent change required by the task.
- Only modify files from the allowed file list unless the pipeline later expands the allowlist.
- Do not run package install commands unless the task explicitly requires dependency installation.
- Prefer existing project conventions over new abstractions.
"""

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
        all_changed = sorted({p for p in tracked + untracked if p.strip()})
        return [
            p for p in all_changed
            if not p.endswith(".pyc") and "__pycache__" not in p.split("/") and not p.endswith(".tmp")
        ]

    def _diff_with_untracked(self, repo_path: Path, changed_files: List[str]) -> str:
        diff = self._run_git(repo_path, ["diff", "--no-ext-diff", "--binary"])
        untracked = self._run_git(repo_path, ["ls-files", "--others", "--exclude-standard"]).splitlines()
        chunks = [diff.rstrip()] if diff.strip() else []

        for rel_path in untracked:
            if (
                rel_path.endswith(".pyc") 
                or "__pycache__" in rel_path.split("/") 
                or rel_path.endswith(".tmp")
            ):
                continue
            file_path = repo_path / rel_path
            if not file_path.is_file():
                continue
            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                content = "[binary or non-UTF-8 file omitted]"
            chunks.append(f"--- /dev/null\n+++ b/{rel_path}\n@@ untracked file @@\n{content[:12000]}")

        return "\n\n".join(chunk for chunk in chunks if chunk)
