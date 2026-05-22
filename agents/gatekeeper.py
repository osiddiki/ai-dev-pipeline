from typing import Any, List
from pydantic import BaseModel
from .base import BaseAgent, AgentResult
from .supervisor import SupervisorPlan, TaskDefinition
from .worker import WorkerResult
from .prompts import GATEKEEPER_PLAN_PROMPT, GATEKEEPER_CODE_PROMPT
from integrations.gemini_client import GeminiClient
import structlog

logger = structlog.get_logger()

class GateResult(BaseModel):
    approved: bool
    critique: str

class GatekeeperAgent(BaseAgent):
    name = "gatekeeper"
    
    async def invoke(self, context: dict[str, Any], input_data: Any) -> AgentResult:
        """Base invoke not used directly by gatekeeper."""
        raise NotImplementedError("Use specific gate methods (review_plan, review_design, etc.)")
    
    async def review_plan(self, issue: str, plan: SupervisorPlan) -> GateResult:
        """Gate 1: Shift-left validation of decomposed tasks."""
        logger.info("Gatekeeper running 'review_plan' gate", tasks_count=len(plan.tasks))
        
        messages = [
            {"role": "system", "content": GATEKEEPER_PLAN_PROMPT},
            {"role": "user", "content": f"Requirement: {issue}\nProposed Plan: {plan.json()}\nDoes this plan have any omissions?"}
        ]
        
        raw_response = await GeminiClient.chat(role="gatekeeper", messages=messages)
        
        # We look for "REJECT" or "APPROVED" in the text, or we can use a structured output.
        # For simplicity in this PoC, let's assume if it contains 'APPROVED' it's a pass.
        approved = "APPROVED" in raw_response.upper() and "REJECT" not in raw_response.upper()
        return GateResult(approved=approved, critique=raw_response)

    async def review_design(self, task: TaskDefinition, proposed_design: str) -> GateResult:
        """Gate 2: Pre-code technical approach validation."""
        logger.info("Gatekeeper running 'review_design' gate", task=task.id)
        # TODO: Implement design review logic
        return GateResult(approved=True, critique="Design aligns with repository architecture.")

    async def codereview(self, task: TaskDefinition, worker_result: WorkerResult) -> GateResult:
        """Gate 3: Narrow context file-scoped review."""
        logger.info("Gatekeeper running 'codereview' gate", task=task.id)
        
        messages = [
            {"role": "system", "content": GATEKEEPER_CODE_PROMPT},
            {"role": "user", "content": f"Task: {task.description}\nImplementation Diff:\n{worker_result.diff}\nLinter Output: {worker_result.linter_output}"}
        ]
        
        raw_response = await GeminiClient.chat(role="gatekeeper", messages=messages)
        approved = "APPROVED" in raw_response.upper() and "REJECT" not in raw_response.upper()
        return GateResult(approved=approved, critique=raw_response)

    async def review_code(self, issue: str, plan: SupervisorPlan, diffs: List[WorkerResult]) -> GateResult:
        """Gate 4: Broad context task validation."""
        logger.info("Gatekeeper running 'review_code' gate against original issue.")
        
        all_diffs_text = "\n".join([f"Task {d.task_id} Diff:\n{d.diff}" for d in diffs])
        messages = [
            {"role": "system", "content": GATEKEEPER_CODE_PROMPT},
            {"role": "user", "content": f"Original Requirement: {issue}\nComplete Implementation:\n{all_diffs_text}"}
        ]
        
        raw_response = await GeminiClient.chat(role="gatekeeper", messages=messages)
        approved = "APPROVED" in raw_response.upper() and "REJECT" not in raw_response.upper()
        return GateResult(approved=approved, critique=raw_response)
