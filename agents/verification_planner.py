import json
from typing import Any, List
from .base import BaseAgent, AgentResult
from .prompts import VERIFICATION_PLANNER_PROMPT
from .models import VerificationPlan
from integrations.gemini_client import LLMClient
import structlog

logger = structlog.get_logger()

class VerificationPlannerAgent(BaseAgent):
    name = "verification_planner"
    
    async def invoke(self, context: dict[str, Any], task_description: str, changed_files: List[str]) -> AgentResult:
        """
        Input: Task description and changed files.
        Output: A VerificationPlan.
        """
        logger.info("Planning verification", task=task_description[:50])
        
        repo_context = context.get("repo_context", "")
        
        messages = [
            {"role": "system", "content": VERIFICATION_PLANNER_PROMPT},
            {"role": "user", "content": f"REPO CONTEXT:\n{repo_context}\n\nTASK:\n{task_description}\n\nCHANGED FILES:\n{', '.join(changed_files)}\n\nPlease provide the verification plan JSON."}
        ]
        
        raw_response, metrics = await LLMClient.chat(model_id=self.model_id, messages=messages)
        
        try:
            clean_json = raw_response.replace("```json", "").replace("```", "").strip()
            plan_data = json.loads(clean_json)
            plan = VerificationPlan(**plan_data)
            return AgentResult(success=True, output=plan)
        except Exception as e:
            logger.error("Verification planning parsing error", error=str(e))
            return AgentResult(success=False, output=f"Parsing error: {str(e)}")
