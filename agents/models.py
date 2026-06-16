from typing import Literal, Optional
from pydantic import BaseModel

class GateConfig(BaseModel):
    """Policy as Code for model hierarchy."""
    executor_model: str = "deepseek/deepseek-v4-pro"
    verifier_model: str = "deepseek/deepseek-chat"
    planner_model: str = "deepseek/deepseek-chat"
    cheap_model: str = "deepseek/deepseek-v4-pro"
    strong_planner_model: str = "deepseek/deepseek-chat"
    strong_verifier_model: str = "deepseek/deepseek-chat"
    model_policy: Literal["policy_ladder", "best_always", "cost_first"] = "policy_ladder"
    rule_mode: Literal["review_first", "auto_apply", "local_only"] = "review_first"
    max_plan_repairs: int = 2
    max_prompt_rewrites: int = 3
    min_gate_confidence: float = 0.8
    use_git_worktree: bool = True
    max_task_attempts: int = 3
    allow_dependency_install: bool = True
    protect_gate_source_paths: bool = True

class GateReviewReport(BaseModel):
    """Structured output for all Gatekeeper reviews."""
    approved: bool
    primary_failure_mode: Optional[Literal["omission", "systematic", "incoherent", "none"]] = None
    confidence: float
    review_summary: str
    remediation_steps: Optional[str] = None

class VerificationResult(BaseModel):
    success: bool
    reason: str
    used_command: Optional[str] = None
    evidence: Optional[str] = None
