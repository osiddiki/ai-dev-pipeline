import json
from typing import Any, List, Optional
from pydantic import BaseModel, Field
from .base import BaseAgent, AgentResult
from integrations.gemini_client import LLMClient
from environment.tools import CodebaseTools, CODEBASE_TOOLS_SCHEMA
import structlog

logger = structlog.get_logger()

class TaskDefinition(BaseModel):
    id: str
    description: str
    target_file: Optional[str] = None
    target_files: List[str] = Field(default_factory=list)
    dependencies: List[str] = Field(default_factory=list)
    design_constraints: str = "Follow project guidelines."
    acceptance_criteria: str = "Code compiles and passes linting."
    requires_tests: bool = True

    @property
    def allowed_files(self) -> List[str]:
        if self.target_files:
            return self.target_files
        if self.target_file:
            return [self.target_file]
        return []

class SupervisorPlan(BaseModel):
    tasks: List[TaskDefinition]

class SupervisorAgent(BaseAgent):
    name = "supervisor"
    
    async def invoke(self, context: dict[str, Any], input_data: str, temperature: float = 0.3) -> AgentResult:
        """
        Input: Task description.
        Output: A decomposed list of atomic tasks.
        """
        logger.info("Supervisor planning task", task=input_data[:50])
        
        repo_path = context.get("repo_path", ".")
        tool_handler = CodebaseTools(repo_path)
        tool_handler.rag.build_index()
        guidelines = context.get("guidelines", "Follow professional best practices.")
        
        system_prompt = f"""You are the Supervisor Agent for the GATE autonomous pipeline.
Your job is to explore the codebase using the provided tools and decompose the requirement into a list of atomic tasks.
Project guidelines: {guidelines}

Explore the codebase carefully. When you have gathered enough information, output your final plan in JSON format.
Your final response must be ONLY a valid JSON array of tasks matching this schema:
[{{ "id": "task-1", "description": "...", "target_files": ["src/app.py"], "dependencies": [], "design_constraints": "...", "acceptance_criteria": "...", "requires_tests": true }}]
You can set "requires_tests": false for tasks involving static content, basic CSS/UI updates, or simple documentation.
"""
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Requirement: {input_data}\n\nPlease explore the codebase and output the final JSON plan."}
        ]
        
        for step in range(15):
            response, metrics = await LLMClient.chat(
                model_id=self.model_id, 
                messages=messages, 
                temperature=temperature,
                tools=CODEBASE_TOOLS_SCHEMA
            )
            
            if hasattr(response, "tool_calls") and response.tool_calls:
                # Add assistant message with tool calls
                messages.append(response.model_dump())
                
                for tool_call in response.tool_calls:
                    tool_name = tool_call.function.name
                    logger.info("Supervisor using tool", tool=tool_name)
                    try:
                        args = json.loads(tool_call.function.arguments)
                        tool_result = tool_handler.execute_tool(tool_name, args)
                    except Exception as e:
                        tool_result = f"Error executing tool: {str(e)}"
                        
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_name,
                        "content": tool_result
                    })
                continue
            
            raw_response = response
            try:
                clean_json = raw_response.strip()
                if '[' in clean_json and ']' in clean_json:
                    clean_json = clean_json[clean_json.find('['):clean_json.rfind(']')+1]
                else:
                    clean_json = clean_json.replace("```json", "").replace("```", "").strip()
                plan_data = json.loads(clean_json)
                plan = SupervisorPlan(tasks=[TaskDefinition(**t) for t in plan_data])
                return AgentResult(success=True, output=plan)
            except Exception as e:
                logger.error("Failed to parse supervisor plan", error=str(e), raw=raw_response)
                return AgentResult(success=False, output=f"Parsing error: {str(e)}")
                
        return AgentResult(success=False, output="Supervisor exceeded maximum tool calls (15) without finalizing a plan.")
    
    async def revise_plan(self, plan: SupervisorPlan, critique: str, context: dict[str, Any] = {}, temperature: float = 0.3) -> AgentResult:
        logger.info("Supervisor revising plan based on critique")
        
        repo_path = context.get("repo_path", ".")
        tool_handler = CodebaseTools(repo_path)
        tool_handler.rag.build_index()
        guidelines = context.get("guidelines", "Follow professional best practices.")
        
        system_prompt = f"""You are the Supervisor Agent.
Your job is to revise a previously rejected plan. Use tools to investigate why it was rejected if necessary.
Project guidelines: {guidelines}

Output your final revised plan as a JSON array.
"""
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Your previous plan was rejected.\nCritique: {critique}\nOriginal plan: {plan.model_dump_json()}\nPlease explore the repo if needed and provide a revised plan."}
        ]
        
        for step in range(10):
            response, metrics = await LLMClient.chat(
                model_id=self.model_id, 
                messages=messages, 
                temperature=temperature,
                tools=CODEBASE_TOOLS_SCHEMA
            )
            
            if hasattr(response, "tool_calls") and response.tool_calls:
                messages.append(response.model_dump())
                for tool_call in response.tool_calls:
                    tool_name = tool_call.function.name
                    try:
                        args = json.loads(tool_call.function.arguments)
                        tool_result = tool_handler.execute_tool(tool_name, args)
                    except Exception as e:
                        tool_result = f"Error executing tool: {str(e)}"
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_name,
                        "content": tool_result
                    })
                continue
            
            raw_response = response
            try:
                clean_json = raw_response.strip()
                if '[' in clean_json and ']' in clean_json:
                    clean_json = clean_json[clean_json.find('['):clean_json.rfind(']')+1]
                else:
                    clean_json = clean_json.replace("```json", "").replace("```", "").strip()
                plan_data = json.loads(clean_json)
                revised_plan = SupervisorPlan(tasks=[TaskDefinition(**t) for t in plan_data])
                return AgentResult(success=True, output=revised_plan)
            except Exception as e:
                return AgentResult(success=False, output=f"Revision parsing error: {str(e)}")
        
        return AgentResult(success=False, output="Supervisor exceeded maximum tool calls during revision.")
