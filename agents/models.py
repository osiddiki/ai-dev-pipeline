from typing import List, Dict, Literal, Optional
from pydantic import BaseModel

class GateConfig(BaseModel):
    """Policy as Code for model hierarchy."""
    executor_model: str = "deepseek/deepseek-chat"
    verifier_model: str = "gemini/gemini-3.1-pro-preview"
    planner_model: str = "gemini/gemini-2.5-pro"
    min_gate_confidence: float = 0.8

class GateReviewReport(BaseModel):
    """Structured output for all Gatekeeper reviews."""
    approved: bool
    primary_failure_mode: Optional[Literal["omission", "systematic", "incoherent", "none"]] = None
    confidence: float
    review_summary: str
    remediation_steps: Optional[str] = None

class VerificationPlan(BaseModel):
    commands: List[str]  # ordered by confidence
    success_criteria: Dict  # e.g., {"type": "exit_code_zero", "additional_pattern": "OK"}
    fallback_mode: Literal["syntax_check", "static_analysis", "fail"]

class VerificationResult(BaseModel):
    success: bool
    reason: str
    used_command: Optional[str] = None
    evidence: Optional[str] = None
