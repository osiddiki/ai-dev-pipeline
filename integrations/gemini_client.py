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
    async def chat(cls, model_id: str, messages: list[dict], temperature: float = 0.3, response_format=None):
        """
        Generic chat method. 
        model_id examples:
        - gemini/gemini-1.5-pro
        - deepseek/deepseek-chat
        - openai/gpt-4o
        """
        logger.info("Calling LLM", model=model_id)
        
        try:
            kwargs = {
                "model": model_id,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": 8192
            }
            if response_format:
                kwargs["response_format"] = response_format
                
            response = await acompletion(**kwargs)
            content = response.choices[0].message.content
            usage = response.usage
            metrics = {
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0
            }
            return content, metrics
        except Exception as e:
            logger.error("LLM call failed", model=model_id, error=str(e))
            raise e
