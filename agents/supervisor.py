import json
from typing import Any, List, Optional
from pydantic import BaseModel
from .base import BaseAgent, AgentResult
from .prompts import SUPERVISOR_PROMPT
from integrations.gemini_client import LLMClient
import structlog

logger = structlog.get_logger()

class TaskDefinition(BaseModel):
    id: str
    description: str
    target_file: Optional[str] = None
    dependencies: List[str] = []

class SupervisorPlan(BaseModel):
    tasks: List[TaskDefinition]

class SupervisorAgent(BaseAgent):
    name = "supervisor"
    
    async def invoke(self, context: dict[str, Any], input_data: str, temperature: float = 0.3) -> AgentResult:
        """
        Input: Task description.
        Output: A decomposed list of atomic tasks.
        """
        logger.info("Supervisor decomposing task", task=input_data[:50])
        
        guidelines = context.get("guidelines", "Follow professional best practices for the given task domain.")
        repo_context = context.get("repo_context", "No repository context provided.")
        discovery_report = context.get("discovery_report", "No discovery report provided.")
        system_prompt = SUPERVISOR_PROMPT.format(
            guidelines=guidelines, 
            repo_context=repo_context, 
            discovery_report=discovery_report
        )
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Please decompose this requirement into tasks: {input_data}"}
        ]
        
        raw_response, metrics = await LLMClient.chat(model_id=self.model_id, messages=messages, temperature=temperature)
        
        try:
            # Clean possible markdown code blocks if the model includes them
            clean_json = raw_response.strip().replace("```json", "").replace("```", "").strip()
            plan_data = json.loads(clean_json)
            plan = SupervisorPlan(tasks=[TaskDefinition(**t) for t in plan_data])
            return AgentResult(success=True, output=plan)
        except Exception as e:
            logger.error("Failed to parse supervisor plan", error=str(e), raw=raw_response)
            return AgentResult(success=False, output=f"Parsing error: {str(e)}")
    
    async def revise_plan(self, plan: SupervisorPlan, critique: str, context: dict[str, Any] = {}, temperature: float = 0.3) -> AgentResult:
        """Revise the plan based on gatekeeper feedback."""
        logger.info("Supervisor revising plan based on critique")
        
        guidelines = context.get("guidelines", "Follow professional best practices for the given task domain.")
        repo_context = context.get("repo_context", "No repository context provided.")
        discovery_report = context.get("discovery_report", "No discovery report provided.")
        system_prompt = SUPERVISOR_PROMPT.format(
            guidelines=guidelines, 
            repo_context=repo_context, 
            discovery_report=discovery_report
        )
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Your previous plan was rejected. Critique: {critique}. Original plan: {plan.json()}. Please provide a revised plan in JSON format."}
        ]
        
        raw_response, metrics = await LLMClient.chat(model_id=self.model_id, messages=messages, temperature=temperature)
        try:
            clean_json = raw_response.strip().replace("```json", "").replace("```", "").strip()
            plan_data = json.loads(clean_json)
            revised_plan = SupervisorPlan(tasks=[TaskDefinition(**t) for t in plan_data])
            return AgentResult(success=True, output=revised_plan)
        except Exception as e:
            return AgentResult(success=False, output=f"Revision parsing error: {str(e)}")
