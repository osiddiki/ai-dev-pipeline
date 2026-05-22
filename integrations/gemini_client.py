import os
from litellm import acompletion
import structlog
from dotenv import load_dotenv

load_dotenv()
logger = structlog.get_logger()

class GeminiClient:
    """Wrapper for Gemini model interactions via LiteLLM."""
    
    # We use Flash for speed/cost on workers, Pro for the Gatekeeper reviews.
    MODELS = {
        "worker": "gemini/gemini-2.5-flash",
        "supervisor": "gemini/gemini-2.5-flash",
        "gatekeeper": "gemini/gemini-2.5-pro"
    }

    @classmethod
    async def chat(cls, role: str, messages: list[dict], temperature: float = 0.3):
        model = cls.MODELS.get(role, "gemini/gemini-1.5-flash")
        api_key = os.getenv("GEMINI_API_KEY")
        
        if not api_key:
            logger.warning("No GEMINI_API_KEY found. LLM calls will fail.")
            return "API Key Missing"

        logger.info("Calling Gemini", role=role, model=model)
        
        try:
            response = await acompletion(
                model=model,
                messages=messages,
                temperature=temperature,
                api_key=api_key
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error("Gemini call failed", error=str(e))
            raise e
