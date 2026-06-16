import structlog
from typing import Any
from .base import BaseAgent, AgentResult
from .worker import AiderWorkerAgent
from .prompts import TEST_WRITER_PROMPT

logger = structlog.get_logger()

class TestWriterAgent(BaseAgent):
    name = "test_writer"
    
    def __init__(self, model_id: str = "deepseek/deepseek-v4-pro"):
        super().__init__(model_id)
        self.worker = AiderWorkerAgent(model_id=model_id)

    async def invoke(self, context: dict[str, Any], input_data: Any) -> AgentResult:
        """
        Input: TaskDefinition
        Output: WorkerResult
        """
        task = input_data
        logger.info("Test Writer starting", task=task.id)

        original_build_prompt = self.worker.build_prompt
        
        def custom_build_prompt(ctx: dict, t) -> str:
            base_prompt = original_build_prompt(ctx, t)
            return f"{TEST_WRITER_PROMPT}\n\n{base_prompt}"
            
        self.worker.build_prompt = custom_build_prompt
        
        try:
            result = await self.worker.invoke(context, task)
            return result
        finally:
            self.worker.build_prompt = original_build_prompt
