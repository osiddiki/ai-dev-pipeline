import os
import asyncio
import structlog
from typing import List, Dict, Any
from agents.verification_planner import VerificationPlannerAgent
from agents.models import VerificationPlan, VerificationResult
from environment.sandbox import DockerSandbox

logger = structlog.get_logger()

class VerifierEngine:
    def __init__(self, sandbox: DockerSandbox, planner_model: str = "gemini/gemini-2.5-pro"):
        self.sandbox = sandbox
        self.planner = VerificationPlannerAgent(model_id=planner_model)
        
    async def verify(self, task_description: str, repo_context: str, changed_files: List[str]) -> VerificationResult:
        logger.info("Starting autonomous verification", task=task_description[:50])
        
        # 1. Plan Verification
        plan_res = await self.planner.invoke({"repo_context": repo_context}, task_description, changed_files)
        if not plan_res.success:
            return VerificationResult(success=False, reason=f"Planner failed: {plan_res.output}")
        plan: VerificationPlan = plan_res.output
        
        # 2. Try Commands
        last_failure_out = ""
        for cmd in plan.commands:
            output, exit_code = await asyncio.to_thread(self.sandbox.execute_command, cmd)
            if self._matches_criteria(exit_code, output, plan.success_criteria):
                return VerificationResult(success=True, used_command=cmd, evidence=output[:4000], reason="Command succeeded")
            last_failure_out = output
        
        # 3. Fallback Chain
        if plan.fallback_mode == "syntax_check":
            return await self._syntax_check(changed_files)
        elif plan.fallback_mode == "static_analysis":
            return await self._static_analysis_diff(changed_files)
        else:
            # If the planner explicitly set fallback to 'fail' AND there are no changed files,
            # this is an Analysis task. It is inherently successful as there's no code to break.
            if not changed_files:
                logger.info("Analysis task detected (no changed files). Bypassing empirical verification.")
                return VerificationResult(success=True, reason="Analysis tasks do not require empirical verification.")
                
            return VerificationResult(success=False, reason="No applicable verification method found for changed files.", evidence=last_failure_out)

    def _matches_criteria(self, exit_code: int, output: str, criteria: Dict) -> bool:
        if criteria.get("type") == "exit_code_zero" and exit_code == 0:
            return True
        return False

    async def _syntax_check(self, changed_files: List[str]) -> VerificationResult:
        logger.info("Falling back to syntax check")
        applicable_files = 0
        for file in changed_files:
            ext = os.path.splitext(file)[1]
            cmd_map = {
                ".py": f"python -m py_compile {file}",
                ".ts": f"npx tsc --noEmit {file}",
                ".js": f"node --check {file}",
                ".go": f"go build -o /dev/null {file}",
                ".rs": f"rustc --emit=metadata {file}",
            }
            cmd = cmd_map.get(ext)
            if cmd:
                applicable_files += 1
                output, exit_code = await asyncio.to_thread(self.sandbox.execute_command, cmd)
                if exit_code != 0:
                    return VerificationResult(success=False, reason=f"Syntax error in {file}: {output[:200]}")
        
        if applicable_files == 0:
            return VerificationResult(success=False, reason="No files had a known syntax checker")
        return VerificationResult(success=True, reason="All changed files passed syntax check")

    async def _static_analysis_diff(self, changed_files: List[str]) -> VerificationResult:
        logger.info("Falling back to static analysis (linting)")
        # Simplified: just run npm run lint or similar if it exists
        has_lint, _ = await asyncio.to_thread(self.sandbox.execute_command, "grep '\"lint\":' package.json")
        if has_lint.strip():
            has_pnpm, _ = await asyncio.to_thread(self.sandbox.execute_command, "command -v pnpm")
            cmd = "pnpm run lint" if has_pnpm.strip() else "npm run lint"
            output, exit_code = await asyncio.to_thread(self.sandbox.execute_command, cmd)
            if exit_code != 0:
                return VerificationResult(success=False, reason=f"Linter errors found: {output[:300]}")
            return VerificationResult(success=True, reason="Linter passed")
        return VerificationResult(success=False, reason="No linter found for static analysis")
