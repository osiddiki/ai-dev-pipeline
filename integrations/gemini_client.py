import os
from litellm import acompletion
import litellm
import structlog
from dotenv import load_dotenv

load_dotenv()
logger = structlog.get_logger()

class LLMClient:
    """Wrapper for LLM interactions via LiteLLM supporting multiple providers."""

    @classmethod
    async def chat(cls, model_id: str, messages: list[dict], temperature: float = 0.3):
        """
        Generic chat method. 
        model_id examples:
        - gemini/gemini-1.5-pro
        - deepseek/deepseek-chat
        - openai/gpt-4o
        """
        logger.info("Calling LLM", model=model_id)
        
        try:
            response = await acompletion(
                model=model_id,
                messages=messages,
                temperature=temperature,
                max_tokens=8192
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error("LLM call failed", model=model_id, error=str(e))
            raise e
