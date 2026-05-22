import json
from typing import Any, List
from pydantic import BaseModel
from .base import BaseAgent, AgentResult
from .prompts import SUPERVISOR_PROMPT
from integrations.gemini_client import GeminiClient
import structlog

logger = structlog.get_logger()

class TaskDefinition(BaseModel):
    id: str
    description: str
    dependencies: List[str] = []

class SupervisorPlan(BaseModel):
    tasks: List[TaskDefinition]

class SupervisorAgent(BaseAgent):
    name = "supervisor"
    
    async def invoke(self, context: dict[str, Any], input_data: str) -> AgentResult:
        """
        Input: Jira/GitLab Issue description.
        Output: A decomposed list of atomic tasks (Release Arc).
        """
        logger.info("Supervisor decomposing issue", issue=input_data[:50])
        
        messages = [
            {"role": "system", "content": SUPERVISOR_PROMPT},
            {"role": "user", "content": f"Please decompose this requirement into tasks: {input_data}"}
        ]
        
        raw_response = await GeminiClient.chat(role="supervisor", messages=messages)
        
        try:
            # Clean possible markdown code blocks if the model includes them
            clean_json = raw_response.strip().replace("```json", "").replace("```", "").strip()
            plan_data = json.loads(clean_json)
            plan = SupervisorPlan(tasks=[TaskDefinition(**t) for t in plan_data])
            return AgentResult(success=True, output=plan)
        except Exception as e:
            logger.error("Failed to parse supervisor plan", error=str(e), raw=raw_response)
            return AgentResult(success=False, output=f"Parsing error: {str(e)}")
    
    async def revise_plan(self, plan: SupervisorPlan, critique: str) -> AgentResult:
        """Revise the plan based on gatekeeper feedback."""
        logger.info("Supervisor revising plan based on critique")
        
        messages = [
            {"role": "system", "content": SUPERVISOR_PROMPT},
            {"role": "user", "content": f"Your previous plan was rejected. Critique: {critique}. Original plan: {plan.json()}. Please provide a revised plan in JSON format."}
        ]
        
        raw_response = await GeminiClient.chat(role="supervisor", messages=messages)
        try:
            clean_json = raw_response.strip().replace("```json", "").replace("```", "").strip()
            plan_data = json.loads(clean_json)
            revised_plan = SupervisorPlan(tasks=[TaskDefinition(**t) for t in plan_data])
            return AgentResult(success=True, output=revised_plan)
        except Exception as e:
            return AgentResult(success=False, output=f"Revision parsing error: {str(e)}")
