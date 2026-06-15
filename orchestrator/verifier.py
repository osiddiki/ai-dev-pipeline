import asyncio
import json
import os
import shlex
from pathlib import Path
from typing import List, Optional, Set

import structlog

from agents.models import VerificationResult
from environment.sandbox import DockerSandbox

logger = structlog.get_logger()


class VerifierEngine:
    """Deterministic verifier for GATE task acceptance."""

    def __init__(self, sandbox: DockerSandbox, planner_model: str = "gemini/gemini-2.5-pro"):
        self.sandbox = sandbox
        self.repo_root = Path(self.sandbox.target_repo_path).resolve()

    async def verify(
        self,
        task_description: str,
        repo_context: str,
        changed_files: List[str],
        acceptance_criteria: str = "",
    ) -> VerificationResult:
        logger.info("Starting deterministic verification", task=task_description[:50])

        changed_files = sorted({p for p in changed_files if p.strip()})
        if not changed_files:
            return VerificationResult(success=False, reason="No changed files were produced.")

        temp_files = [p for p in changed_files if self._is_temp_artifact(p)]
        if temp_files:
            return VerificationResult(
                success=False,
                reason=f"Temporary or scratch artifacts are not allowed: {', '.join(temp_files)}",
            )

        missing = await self._missing_files(changed_files)
        if missing:
            return VerificationResult(success=False, reason=f"Changed files are missing: {', '.join(missing)}")

        schema_result = await self._schema_and_syntax_checks(changed_files)
        if not schema_result.success:
            return schema_result

        project_result = await self._project_checks(changed_files)
        if not project_result.success:
            return project_result

        evidence = "\n".join(
            part for part in [schema_result.evidence, project_result.evidence] if part
        )
        return VerificationResult(success=True, reason="Deterministic verification passed.", evidence=evidence[:4000])

    async def verify_release(self, repo_context: str, changed_files: List[str]) -> VerificationResult:
        return await self.verify(
            task_description="Final release-level verification across all changed files.",
            repo_context=repo_context,
            changed_files=changed_files,
            acceptance_criteria="All changed source/config files are valid and project checks pass when available.",
        )

    def _is_temp_artifact(self, rel_path: str) -> bool:
        name = os.path.basename(rel_path)
        return (
            name.startswith(".tmp")
            or name.endswith(".tmp")
            or name.endswith(".bak")
            or ".orig" in name
            or rel_path.endswith(".rej")
        )

    async def _missing_files(self, changed_files: List[str]) -> List[str]:
        missing = []
        for rel_path in changed_files:
            quoted = shlex.quote(rel_path)
            output, _ = await asyncio.to_thread(self.sandbox.execute_command, f"[ -f {quoted} ] && echo yes || echo no")
            if output.strip() != "yes":
                missing.append(rel_path)
        return missing

    async def _schema_and_syntax_checks(self, changed_files: List[str]) -> VerificationResult:
        evidence = []
        checked = 0
        ts_projects: Set[Path] = set()

        for rel_path in changed_files:
            suffix = Path(rel_path).suffix
            if suffix == ".json":
                result = await self._check_json(rel_path)
            elif suffix == ".js":
                result = await self._check_js(rel_path)
            elif suffix in {".ts", ".tsx"}:
                tsconfig = self._nearest_file(rel_path, "tsconfig.json")
                if tsconfig:
                    ts_projects.add(tsconfig.parent)
                    result = VerificationResult(success=True, reason="Deferred to project TypeScript check.")
                else:
                    result = await self._check_ts_file(rel_path)
            else:
                result = VerificationResult(success=True, reason="No schema or syntax check required.")

            if suffix in {".json", ".js", ".ts", ".tsx"}:
                checked += 1
            if not result.success:
                return result
            if result.evidence:
                evidence.append(result.evidence)

        for project_dir in sorted(ts_projects):
            result = await self._check_ts_project(project_dir)
            if not result.success:
                return result
            if result.evidence:
                evidence.append(result.evidence)

        if checked == 0:
            return VerificationResult(success=True, reason="No source/config syntax checks were applicable.")

        return VerificationResult(success=True, reason="Schema and syntax checks passed.", evidence="\n".join(evidence))

    async def _project_checks(self, changed_files: List[str]) -> VerificationResult:
        evidence = []
        package_dirs = self._package_dirs(changed_files)

        for package_dir in sorted(package_dirs):
            node_modules = package_dir / "node_modules"
            package_json = package_dir / "package.json"
            if not node_modules.exists() or not package_json.exists():
                continue

            scripts = self._package_scripts(package_json)
            rel_dir = self._rel(package_dir)

            for script in ("lint", "test", "build"):
                if script not in scripts:
                    continue
                cmd = f"cd {shlex.quote(rel_dir)} && npm run {script} --if-present"
                output, code = await asyncio.to_thread(self.sandbox.execute_command, cmd)
                if code != 0:
                    return VerificationResult(
                        success=False,
                        reason=f"`npm run {script}` failed in {rel_dir}: {output[:800]}",
                        used_command=cmd,
                        evidence=output[:4000],
                    )
                evidence.append(f"{cmd}\n{output[:1000]}")

        return VerificationResult(success=True, reason="Project checks passed or were not locally runnable.", evidence="\n".join(evidence))

    async def _check_json(self, rel_path: str) -> VerificationResult:
        script = "const fs=require('fs'); JSON.parse(fs.readFileSync(process.argv[1], 'utf8'));"
        cmd = f"node -e {shlex.quote(script)} {shlex.quote(rel_path)}"
        output, code = await asyncio.to_thread(self.sandbox.execute_command, cmd)
        if code != 0:
            return VerificationResult(success=False, reason=f"Invalid JSON in {rel_path}: {output[:800]}", used_command=cmd, evidence=output)
        return VerificationResult(success=True, reason=f"JSON parsed: {rel_path}", evidence=cmd)

    async def _check_js(self, rel_path: str) -> VerificationResult:
        cmd = f"node --check {shlex.quote(rel_path)}"
        output, code = await asyncio.to_thread(self.sandbox.execute_command, cmd)
        if code != 0:
            return VerificationResult(success=False, reason=f"JavaScript syntax failed for {rel_path}: {output[:800]}", used_command=cmd, evidence=output)
        return VerificationResult(success=True, reason=f"JavaScript syntax passed: {rel_path}", evidence=cmd)

    async def _check_ts_file(self, rel_path: str) -> VerificationResult:
        cmd = f"npx tsc --noEmit --skipLibCheck --target esnext --module commonjs {shlex.quote(rel_path)}"
        output, code = await asyncio.to_thread(self.sandbox.execute_command, cmd)
        if code != 0:
            return VerificationResult(success=False, reason=f"TypeScript check failed for {rel_path}: {output[:800]}", used_command=cmd, evidence=output)
        return VerificationResult(success=True, reason=f"TypeScript check passed: {rel_path}", evidence=cmd)

    async def _check_ts_project(self, project_dir: Path) -> VerificationResult:
        rel_dir = self._rel(project_dir)
        cmd = f"cd {shlex.quote(rel_dir)} && npx tsc --noEmit -p tsconfig.json"
        output, code = await asyncio.to_thread(self.sandbox.execute_command, cmd)
        if code != 0:
            return VerificationResult(success=False, reason=f"TypeScript project check failed in {rel_dir}: {output[:800]}", used_command=cmd, evidence=output)
        return VerificationResult(success=True, reason=f"TypeScript project check passed in {rel_dir}", evidence=cmd)

    def _nearest_file(self, rel_path: str, filename: str) -> Optional[Path]:
        current = (self.repo_root / rel_path).parent
        while self.repo_root in [current, *current.parents]:
            candidate = current / filename
            if candidate.exists():
                return candidate
            if current == self.repo_root:
                break
            current = current.parent
        return None

    def _package_dirs(self, changed_files: List[str]) -> Set[Path]:
        dirs: Set[Path] = set()
        for rel_path in changed_files:
            package_json = self._nearest_file(rel_path, "package.json")
            if package_json:
                dirs.add(package_json.parent)
        return dirs

    def _package_scripts(self, package_json: Path) -> Set[str]:
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return set()
        scripts = data.get("scripts") or {}
        if not isinstance(scripts, dict):
            return set()
        return set(scripts)

    def _rel(self, path: Path) -> str:
        return str(path.resolve().relative_to(self.repo_root))
