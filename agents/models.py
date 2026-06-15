from typing import Literal, Optional
from pydantic import BaseModel

class GateConfig(BaseModel):
    """Policy as Code for model hierarchy."""
    executor_model: str = "deepseek/deepseek-chat"
    verifier_model: str = "gemini/gemini-3.1-pro-preview"
    planner_model: str = "gemini/gemini-2.5-pro"
    cheap_model: str = "deepseek/deepseek-chat"
    strong_planner_model: str = "gemini/gemini-2.5-pro"
    strong_verifier_model: str = "gemini/gemini-3.1-pro-preview"
    model_policy: Literal["policy_ladder", "best_always", "cost_first"] = "policy_ladder"
    rule_mode: Literal["review_first", "auto_apply", "local_only"] = "review_first"
    max_plan_repairs: int = 2
    max_prompt_rewrites: int = 3
    min_gate_confidence: float = 0.8
    codex_command: str = "codex"
    codex_sandbox: str = "workspace-write"
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
