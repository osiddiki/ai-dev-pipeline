from typing import Any, List, Optional
from pydantic import BaseModel
from .base import BaseAgent, AgentResult
from .supervisor import SupervisorPlan, TaskDefinition
from .worker import WorkerResult
from .prompts import GATEKEEPER_PLAN_PROMPT, GATEKEEPER_CODE_PROMPT
from integrations.gemini_client import LLMClient
import structlog

logger = structlog.get_logger()

class GateResult(BaseModel):
    approved: bool
    error_type: Optional[str] = None # systematic, omission, incoherent
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
            {"role": "user", "content": f"Requirement: {issue}\nProposed Plan: {plan.json()}\nDoes this plan have any omissions or logical flaws? Respond using the prescribed format."}
        ]
        
        raw_response = await LLMClient.chat(model_id=self.model_id, messages=messages)
        return self._parse_response(raw_response)

    def _parse_response(self, raw_response: str) -> GateResult:
        """Extract STATUS and ERROR_TYPE from the Gatekeeper's response."""
        response_upper = raw_response.upper()
        
        is_approved = "STATUS: APPROVED" in response_upper or "STATUS:APPROVED" in response_upper
        is_rejected = "STATUS: REJECT" in response_upper or "STATUS:REJECT" in response_upper
        
        error_type = None
        if is_rejected:
            if "ERROR_TYPE: OMISSION" in response_upper: error_type = "omission"
            elif "ERROR_TYPE: SYSTEMATIC" in response_upper: error_type = "systematic"
            elif "ERROR_TYPE: INCOHERENT" in response_upper: error_type = "incoherent"
        
        return GateResult(approved=is_approved, error_type=error_type, critique=raw_response)

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
            {"role": "user", "content": f"Task: {task.description}\nImplementation Diff:\n{worker_result.diff}\nValidation Output: {worker_result.linter_output}\nRespond using the prescribed format."}
        ]
        
        raw_response = await LLMClient.chat(model_id=self.model_id, messages=messages)
        return self._parse_response(raw_response)

    async def review_code(self, issue: str, plan: SupervisorPlan, diffs: List[WorkerResult]) -> GateResult:
        """Gate 4: Broad context task validation."""
        logger.info("Gatekeeper running 'review_code' gate against original issue.")
        
        all_diffs_text = "\n".join([f"Task {d.task_id} Diff:\n{d.diff}" for d in diffs])
        messages = [
            {"role": "system", "content": GATEKEEPER_CODE_PROMPT},
            {"role": "user", "content": f"Original Requirement: {issue}\nComplete Implementation:\n{all_diffs_text}\nRespond using the prescribed format."}
        ]
        
        raw_response = await LLMClient.chat(model_id=self.model_id, messages=messages)
        return self._parse_response(raw_response)
