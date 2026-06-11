from typing import Any, List, Optional
import json
from pydantic import BaseModel
from .base import BaseAgent, AgentResult
from .supervisor import SupervisorPlan, TaskDefinition
from .worker import WorkerResult
from .prompts import GATEKEEPER_PLAN_PROMPT, GATEKEEPER_CODE_PROMPT, GATEKEEPER_DESIGN_PROMPT, GATEKEEPER_SYSTEM_PROMPT
from .models import GateReviewReport
from integrations.gemini_client import LLMClient
import structlog

logger = structlog.get_logger()

class GateResult(BaseModel):
    approved: bool
    error_type: Optional[str] = None # systematic, omission, incoherent
    critique: str
    metrics: dict[str, int] = {}

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
            {"role": "user", "content": f"Requirement: {issue}\nProposed Plan: {plan.json()}\nDoes this plan have any omissions or logical flaws?"}
        ]
        
        raw_response, metrics = await LLMClient.chat(model_id=self.model_id, messages=messages, response_format=GateReviewReport)
        return self._parse_response(raw_response, metrics)

    def _parse_response(self, raw_response: str, metrics: dict[str, int] = {}) -> GateResult:
        """Extract data from the Gatekeeper's structured JSON response."""
        try:
            report_data = json.loads(raw_response)
            report = GateReviewReport(**report_data)
            
            critique = report.review_summary
            if report.remediation_steps:
                critique += f"\n\nREMEDIATION:\n{report.remediation_steps}"
                
            return GateResult(
                approved=report.approved, 
                error_type=report.primary_failure_mode if report.primary_failure_mode != "none" else None, 
                critique=critique, 
                metrics=metrics
            )
        except Exception as e:
            logger.error("Failed to parse structured gatekeeper response", error=str(e), raw=raw_response)
            # Fallback for parsing failure
            return GateResult(approved=False, error_type="incoherent", critique=f"Gatekeeper parsing failure: {raw_response}", metrics=metrics)

    async def review_design(self, task: TaskDefinition, proposed_design: str) -> GateResult:
        """Gate 2: Pre-code technical approach validation."""
        logger.info("Gatekeeper running 'review_design' gate", task=task.id)
        
        messages = [
            {"role": "system", "content": GATEKEEPER_DESIGN_PROMPT},
            {"role": "user", "content": f"Task Constraints: {task.design_constraints}\nProposed Design:\n{proposed_design}"}
        ]
        
        raw_response, metrics = await LLMClient.chat(model_id=self.model_id, messages=messages, response_format=GateReviewReport)
        return self._parse_response(raw_response, metrics)

    async def codereview(self, task: TaskDefinition, worker_result: WorkerResult) -> GateResult:
        """Gate 3: Narrow context file-scoped review."""
        logger.info("Gatekeeper running 'codereview' gate", task=task.id)
        
        messages = [
            {"role": "system", "content": GATEKEEPER_CODE_PROMPT},
            {"role": "user", "content": f"Task: {task.description}\nImplementation Diff:\n{worker_result.diff}\nValidation Output: {worker_result.linter_output}"}
        ]
        
        raw_response, metrics = await LLMClient.chat(model_id=self.model_id, messages=messages, response_format=GateReviewReport)
        return self._parse_response(raw_response, metrics)

    async def review_code(self, issue: str, plan: SupervisorPlan, diffs: List[WorkerResult]) -> GateResult:
        """Gate 4: Broad context task validation."""
        logger.info("Gatekeeper running 'review_code' gate against original issue.")
        
        all_diffs_text = "\n".join([f"Task {d.task_id} Diff:\n{d.diff}" for d in diffs])
        messages = [
            {"role": "system", "content": GATEKEEPER_SYSTEM_PROMPT},
            {"role": "user", "content": f"Original Requirement: {issue}\nComplete Implementation:\n{all_diffs_text}"}
        ]
        
        raw_response, metrics = await LLMClient.chat(model_id=self.model_id, messages=messages, response_format=GateReviewReport)
        return self._parse_response(raw_response, metrics)
