from typing import Any
import json
from .base import BaseAgent, AgentResult
from .prompts import META_ANALYZER_PROMPT
from integrations.gemini_client import LLMClient
import structlog

logger = structlog.get_logger()

class MetaAnalyzerAgent(BaseAgent):
    name = "meta_analyzer"
    
    async def invoke(self, context: dict[str, Any], db_connection: Any, temperature: float = 0.3) -> AgentResult:
        """
        Input: Database connection to the Trust Ledger.
        Output: A synthesized string warning based on historical failures.
        """
        logger.info("MetaAnalyzer analyzing historical trust ledger data...")
        
        try:
            # Query 1: Top Failure Modes
            async with db_connection.execute(
                "SELECT error_type, COUNT(*) as count FROM gate_reviews WHERE status = 'rejected' GROUP BY error_type ORDER BY count DESC LIMIT 3"
            ) as cursor:
                failure_modes = await cursor.fetchall()
            
            # Query 2: Stuck Tasks Critiques (Recent)
            async with db_connection.execute(
                "SELECT critique_summary FROM gate_reviews WHERE attempt_number >= 3 AND status = 'rejected' ORDER BY id DESC LIMIT 5"
            ) as cursor:
                stuck_critiques = await cursor.fetchall()
                
            # If no significant failures exist, skip analysis to save tokens
            if not failure_modes and not stuck_critiques:
                return AgentResult(success=True, output="")
                
            stats_payload = f"TOP FAILURE MODES:\n{failure_modes}\n\nRECENT 'STUCK TASK' CRITIQUES:\n{stuck_critiques}"
            
            messages = [
                {"role": "system", "content": META_ANALYZER_PROMPT},
                {"role": "user", "content": f"HISTORICAL FAILURE DATA:\n{stats_payload}\n\nPlease generate the actionable warning."}
            ]
            
            raw_response, metrics = await LLMClient.chat(model_id=self.model_id, messages=messages, temperature=temperature)
            
            if "NO_PATTERN_DETECTED" in raw_response:
                return AgentResult(success=True, output="")
            
            return AgentResult(success=True, output=raw_response.strip())
            
        except Exception as e:
            logger.error("MetaAnalyzer failed to query database", error=str(e))
            return AgentResult(success=False, output="")
