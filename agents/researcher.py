from typing import Any
from .base import BaseAgent, AgentResult
from .prompts import RESEARCHER_PROMPT
from integrations.gemini_client import LLMClient
from environment.sandbox import DockerSandbox
import structlog

logger = structlog.get_logger()

class ResearcherAgent(BaseAgent):
    name = "researcher"
    
    async def invoke(self, context: dict[str, Any], task_description: str) -> AgentResult:
        """
        Input: Task description and repo context.
        Output: A Technical Discovery Report.
        """
        logger.info("Researcher hunting for context", task=task_description[:50])
        
        repo_path = context.get("repo_path", ".")
        repo_context = context.get("repo_context", "")
        sandbox = DockerSandbox(repo_path)
        
        # 1. Step 1: Broad Search
        # We run a multi-keyword grep to find likely candidates
        keywords = task_description.split()
        search_terms = "|".join([k for k in keywords if len(k) > 4][:5]) # Top 5 long words
        grep_results = sandbox.execute_command(f"grep -rEi '{search_terms}' . --exclude-dir={{.git,node_modules,dist,build}} | head -n 30")
        
        # 2. Step 2: Agentic Synthesis
        # We ask the LLM to analyze the task and the grep results to build the report
        messages = [
            {"role": "system", "content": RESEARCHER_PROMPT},
            {"role": "user", "content": f"REPO STRUCTURE:\n{repo_context}\n\nGREP HITS:\n{grep_results}\n\nMISSION:\n{task_description}\n\nPlease generate the Technical Discovery Report."}
        ]
        
        report = await LLMClient.chat(model_id=self.model_id, messages=messages)
        
        return AgentResult(success=True, output=report)
