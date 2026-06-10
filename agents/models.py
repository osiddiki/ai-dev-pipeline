from typing import List, Dict, Literal, Optional
from pydantic import BaseModel

class VerificationPlan(BaseModel):
    commands: List[str]  # ordered by confidence
    success_criteria: Dict  # e.g., {"type": "exit_code_zero", "additional_pattern": "OK"}
    fallback_mode: Literal["syntax_check", "static_analysis", "fail"]

class VerificationResult(BaseModel):
    success: bool
    reason: str
    used_command: Optional[str] = None
    evidence: Optional[str] = None
