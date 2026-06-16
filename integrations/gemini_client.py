import os

from litellm import acompletion
import structlog
from dotenv import load_dotenv

load_dotenv()
logger = structlog.get_logger()

class LLMClient:
    """Wrapper for LLM interactions via LiteLLM supporting multiple providers."""

    @classmethod
    def _gemini_safety_mode(cls) -> str:
        return os.environ.get("GATE_GEMINI_SAFETY_MODE", "default").strip().lower()

    @classmethod
    async def chat(cls, model_id: str, messages: list[dict], temperature: float = 0.3, response_format=None, tools=None):
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
                "max_tokens": 8192,
            }
            if model_id.startswith("gemini/") and cls._gemini_safety_mode() == "block_none":
                kwargs["safety_settings"] = [
                    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
                ]
            # Inject the Pydantic schema directly into the prompt so any model can follow it
            if response_format:
                schema_str = response_format.schema_json() if hasattr(response_format, "schema_json") else str(response_format)
                messages.append({
                    "role": "user",
                    "content": f"Please format your output strictly as a JSON object matching this schema:\n{schema_str}\nYou MUST wrap your entire response inside a ```json and ``` markdown block. Do not include any conversational text."
                })
            if tools:
                kwargs["tools"] = tools
                
            response = await acompletion(**kwargs)
            
            if not response.choices:
                raise ValueError(f"LLM API returned an empty response. This is likely due to a content filter. Raw response: {response}")

            # If the model returns tool calls, we return the raw message object so the caller can process them
            message = response.choices[0].message
            if getattr(message, "tool_calls", None):
                usage = response.usage
                metrics = {
                    "prompt_tokens": usage.prompt_tokens if usage else 0,
                    "completion_tokens": usage.completion_tokens if usage else 0
                }
                return message, metrics

            content = message.content
            usage = response.usage
            metrics = {
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0
            }
            return content, metrics
        except Exception as e:
            logger.error("LLM call failed", model=model_id, error=str(e))
            raise e
