from typing import Any
import asyncio
from pydantic import BaseModel
from .base import BaseAgent, AgentResult
from .supervisor import TaskDefinition
from .prompts import WORKER_PROMPT
from integrations.gemini_client import LLMClient
from environment.sandbox import DockerSandbox
from orchestrator.verifier import VerifierEngine
import structlog

logger = structlog.get_logger()

class WorkerResult(BaseModel):
    task_id: str
    diff: str
    linter_output: str

class WorkerAgent(BaseAgent):
    name = "worker"
    
    async def invoke(self, context: dict[str, Any], input_data: TaskDefinition, temperature: float = 0.3) -> AgentResult:
        """
        Input: A specific, bounded task.
        Output: The proposed code diff and validation output from the sandbox.
        """
        logger.info("Worker executing task", task_id=input_data.id)
        repo_path = context.get("repo_path", ".")
        sandbox = DockerSandbox(repo_path)
        
        # 1. Use the sandbox to gather context
        discovery_report = context.get("discovery_report", "")
        
        # Use target_file from task or try to find it in discovery report / description
        file_to_read = input_data.target_file
        
        if not file_to_read:
            # SEARCH FOR PATH: Try to find a path in the description or discovery report
            import re
            # Match common file paths in the description
            paths = re.findall(r'[a-zA-Z0-9_\-\./]+\.[a-z]{2,4}', input_data.description)
            if paths:
                file_to_read = paths[0]
                logger.info("Inferred file to read from description", path=file_to_read)
            elif "SOURCE OF TRUTH:" in discovery_report:
                try:
                    file_to_read = discovery_report.split("SOURCE OF TRUTH:")[1].split("\n")[0].strip().replace("`", "")
                    logger.info("Inferred file to read from Discovery Report", path=file_to_read)
                except: pass
            
        current_content = "[File not selected or does not exist yet]"
        if file_to_read and file_to_read != "README.md":
            current_content = sandbox.read_file(file_to_read)
            if "Error" in current_content:
                current_content = "[File not found in sandbox]"

        repo_state, _ = await asyncio.to_thread(sandbox.execute_command, "ls -R")
        
        # 2. Call the LLM to generate the solution
        task_memory = context.get("task_memory", "")
        approved_design = context.get("approved_design", "")
        
        user_msg = f"TECHNICAL DISCOVERY REPORT:\n{discovery_report}\n\nPROJECT MEMORY (PREVIOUS ANALYSES):\n{task_memory}\n\n"
        if approved_design:
            user_msg += f"APPROVED DESIGN:\n{approved_design}\n\n"
            
        user_msg += f"Repo State:\n{repo_state}\n\nCurrent File Content ({file_to_read}):\n{current_content}\n\nTask: {input_data.description}"
        
        messages = [
            {"role": "system", "content": WORKER_PROMPT},
            {"role": "user", "content": user_msg}
        ]
        
        raw_response, metrics = await LLMClient.chat(model_id=self.model_id, messages=messages, temperature=temperature)
        
        # 3. Validation Phase (Real autonomous verification)
        validation_output = "No specific file targeted for validation."
        if file_to_read:
            logger.info("Worker running local verification", file=file_to_read)
            verifier = VerifierEngine(sandbox=sandbox)
            # We treat the raw_response as the 'diff' for verification context
            verification = await verifier.verify(input_data.description, discovery_report, [file_to_read])
            if verification.success:
                validation_output = f"VERIFICATION SUCCESS: {verification.reason}"
            else:
                validation_output = f"VERIFICATION FAILURE: {verification.reason}\nEVIDENCE:\n{verification.evidence}"

        return AgentResult(
            success=True,
            output=WorkerResult(
                task_id=input_data.id, 
                diff=raw_response, 
                linter_output=validation_output
            )
        )
