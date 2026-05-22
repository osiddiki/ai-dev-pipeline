from typing import Any
from pydantic import BaseModel
from .base import BaseAgent, AgentResult
from .supervisor import TaskDefinition
from .prompts import WORKER_PROMPT
from integrations.gemini_client import GeminiClient
from environment.sandbox import DockerSandbox
import structlog

logger = structlog.get_logger()

class WorkerResult(BaseModel):
    task_id: str
    diff: str
    linter_output: str

class WorkerAgent(BaseAgent):
    name = "worker"
    
    async def invoke(self, context: dict[str, Any], input_data: TaskDefinition) -> AgentResult:
        """
        Input: A specific, bounded task.
        Output: The proposed code diff and linter output from the sandbox.
        """
        logger.info("Worker executing task", task_id=input_data.id)
        
        # 1. Use the sandbox to gather context (read files)
        sandbox = DockerSandbox(context.get("repo_path", "."))
        repo_state = sandbox.execute_command("ls -R")
        
        # 2. Call Gemini to generate the solution
        messages = [
            {"role": "system", "content": WORKER_PROMPT},
            {"role": "user", "content": f"Repo State:\n{repo_state}\nTask: {input_data.description}"}
        ]
        
        raw_response = await GeminiClient.chat(role="worker", messages=messages)
        
        # 3. Simulate testing the diff in the sandbox
        # In a real version, we'd apply the diff and run `sandbox.run_tests()`
        linter_res = sandbox.run_linter(input_data.id)
        
        return AgentResult(
            success=True,
            output=WorkerResult(
                task_id=input_data.id, 
                diff=raw_response, 
                linter_output=linter_res
            )
        )
