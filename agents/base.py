import abc
import structlog
from pydantic import BaseModel
from typing import Any

logger = structlog.get_logger()

class AgentResult(BaseModel):
    success: bool
    output: Any
    confidence_score: float = 1.0
    metrics: dict[str, int] = {}

class BaseAgent(abc.ABC):
    """Abstract base class for all AI pipeline agents."""
    name: str = "base_agent"
    
    def __init__(self, model_id: str):
        self.model_id = model_id
        
    @abc.abstractmethod
    async def invoke(self, context: dict[str, Any], input_data: Any, temperature: float = 0.3) -> AgentResult:
        """Execute the agent with given context and input."""
        pass
