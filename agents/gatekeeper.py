from typing import Any, List, Optional
import json
from pydantic import BaseModel
from .base import BaseAgent, AgentResult
from .supervisor import SupervisorPlan, TaskDefinition
from .worker import WorkerResult
from .prompts import GATEKEEPER_PLAN_PROMPT, GATEKEEPER_CODE_PROMPT, GATEKEEPER_DESIGN_PROMPT, GATEKEEPER_SYSTEM_PROMPT
from .models import GateReviewReport
from integrations.gemini_client import LLMClient
from environment.tools import CodebaseTools, CODEBASE_TOOLS_SCHEMA
import structlog

logger = structlog.get_logger()

class GateResult(BaseModel):
    approved: bool
    error_type: Optional[str] = None # systematic, omission, incoherent
    critique: str
    confidence: float = 1.0
    metrics: dict[str, int] = {}

class GatekeeperAgent(BaseAgent):
    name = "gatekeeper"
    
    async def invoke(self, context: dict[str, Any], input_data: Any) -> AgentResult:
        """Base invoke not used directly by gatekeeper."""
        raise NotImplementedError("Use specific gate methods (review_plan, review_design, etc.)")
    
    async def review_plan(self, issue: str, plan: SupervisorPlan, active_rules: str = "") -> GateResult:
        """Gate 1: Shift-left validation of decomposed tasks."""
        logger.info("Gatekeeper running 'review_plan' gate", tasks_count=len(plan.tasks))
        
        messages = [
            {"role": "system", "content": GATEKEEPER_PLAN_PROMPT},
            {"role": "user", "content": f"Requirement: {issue}\nActive learned rules:\n{active_rules or 'None'}\nProposed Plan: {plan.json()}\nDoes this plan have any omissions or logical flaws?"}
        ]
        
        raw_response, metrics = await LLMClient.chat(model_id=self.model_id, messages=messages, response_format=GateReviewReport)
        return self._parse_response(raw_response, metrics)

    def _parse_response(self, raw_response: str, metrics: dict[str, int] = {}) -> GateResult:
        """Extract data from the Gatekeeper's structured JSON response."""
        try:
            clean_json = raw_response.strip()
            
            # 1. Try markdown extraction
            start = clean_json.find("```json")
            if start != -1:
                clean_json = clean_json[start+7:]
                end = clean_json.find("```")
                if end != -1:
                    clean_json = clean_json[:end]
            else:
                start = clean_json.find("```")
                if start != -1:
                    clean_json = clean_json[start+3:]
                    end = clean_json.find("```")
                    if end != -1:
                        clean_json = clean_json[:end]
                        
            # 2. Balanced brace extraction from the end (bulletproof against preceding code snippets)
            clean_json = clean_json.strip()
            end_idx = clean_json.rfind('}')
            if end_idx != -1:
                count = 0
                for i in range(end_idx, -1, -1):
                    if clean_json[i] == '}':
                        count += 1
                    elif clean_json[i] == '{':
                        count -= 1
                    if count == 0:
                        clean_json = clean_json[i:end_idx+1]
                        break

            report_data = json.loads(clean_json)
            
            # Normalize alternative keys for schema resilience
            if "verdict" in report_data and "approved" not in report_data:
                verdict_val = str(report_data["verdict"]).upper().strip()
                report_data["approved"] = verdict_val in {"APPROVED", "APPROVE", "ACCEPT", "TRUE", "PASS", "YES", "OK"}
            if "confidence" not in report_data:
                report_data["confidence"] = 1.0
            if "review_summary" not in report_data and "critique" in report_data:
                report_data["review_summary"] = report_data["critique"]
            if "review_summary" not in report_data:
                report_data["review_summary"] = "No review summary provided."
                
            report = GateReviewReport(**report_data)
            
            critique = report.review_summary
            if report.remediation_steps:
                critique += f"\n\nREMEDIATION:\n{report.remediation_steps}"
                
            return GateResult(
                approved=report.approved, 
                error_type=report.primary_failure_mode if report.primary_failure_mode != "none" else None, 
                critique=critique, 
                confidence=report.confidence,
                metrics=metrics
            )
        except Exception as e:
            logger.error("Failed to parse structured gatekeeper response", error=str(e), raw=raw_response)
            # Fallback for parsing failure
            return GateResult(approved=False, error_type="incoherent", critique=f"Gatekeeper parsing failure: {raw_response}", confidence=0.0, metrics=metrics)

    async def review_design(self, task: TaskDefinition, proposed_design: str) -> GateResult:
        """Gate 2: Pre-code technical approach validation."""
        logger.info("Gatekeeper running 'review_design' gate", task=task.id)
        
        messages = [
            {"role": "system", "content": GATEKEEPER_DESIGN_PROMPT},
            {"role": "user", "content": f"Task Constraints: {task.design_constraints}\nProposed Design:\n{proposed_design}"}
        ]
        
        raw_response, metrics = await LLMClient.chat(model_id=self.model_id, messages=messages, response_format=GateReviewReport)
        return self._parse_response(raw_response, metrics)

    async def codereview(
        self,
        task: TaskDefinition,
        worker_result: WorkerResult,
        active_rules: str = "",
        repo_path: str = ".",
        rag_provider: str | None = None,
        rag_model_id: str | None = None,
    ) -> GateResult:
        """Gate 3: Narrow context file-scoped review."""
        logger.info("Gatekeeper running 'codereview' gate", task=task.id)
        
        tool_handler = CodebaseTools(repo_path, provider=rag_provider, model_id=rag_model_id)

        schema_str = GateReviewReport.schema_json()
        system_prompt = (
            f"{GATEKEEPER_CODE_PROMPT}\n"
            f"You may use tools to explore the codebase. Once you have enough context, you MUST format your output strictly as a JSON object matching this schema:\n{schema_str}\n"
            "You MUST wrap your entire response inside a ```json and ``` markdown block. Do not include any conversational text."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": (
                f"Task: {task.description}\n"
                f"Active learned rules:\n{active_rules or 'None'}\n"
                f"Changed Files: {', '.join(worker_result.changed_files) if (hasattr(worker_result, 'changed_files') and worker_result.changed_files) else ', '.join(task.allowed_files)}\n"
                f"Implementation Diff:\n{worker_result.diff if worker_result.diff else f'[Note: No diff generated because files are already fully implemented. You MUST verify the existing implementation by calling read_file_content on: {task.allowed_files}]'}\n"
                f"Validation Output: {worker_result.linter_output}\n\n"
                "CRITICAL INSTRUCTION: The diff only shows changed lines. Variables, functions, or imports used in the diff but not shown are likely already defined in the existing file. "
                "Before declaring that something is 'missing' or 'not defined', you MUST use the read_file_content tool to inspect the surrounding code of the changed files."
            )}
        ]
        
        return await self._run_tool_loop(messages, tool_handler)

    async def review_code(
        self,
        issue: str,
        plan: SupervisorPlan,
        diffs: List[WorkerResult],
        active_rules: str = "",
        repo_path: str = ".",
        rag_provider: str | None = None,
        rag_model_id: str | None = None,
    ) -> GateResult:
        """Gate 4: Broad context task validation."""
        logger.info("Gatekeeper running 'review_code' gate against original issue.")
        
        tool_handler = CodebaseTools(repo_path, provider=rag_provider, model_id=rag_model_id)

        schema_str = GateReviewReport.schema_json()
        system_prompt = (
            f"{GATEKEEPER_SYSTEM_PROMPT}\n"
            f"You may use tools to explore the codebase. Once you have enough context, you MUST format your output strictly as a JSON object matching this schema:\n{schema_str}\n"
            "You MUST wrap your entire response inside a ```json and ``` markdown block. Do not include any conversational text."
        )
        
        # Context Pruning: Truncate diffs if they are extremely large to save tokens
        pruned_diffs = []
        for d in diffs:
            diff_text = d.diff
            if len(diff_text) > 20000:
                diff_text = diff_text[:20000] + "\n...[TRUNCATED FOR CONTEXT PRUNING]..."
            pruned_diffs.append(f"Task {d.task_id} Diff:\n{diff_text}")
            
        all_diffs_text = "\n".join(pruned_diffs)
        if not all_diffs_text.strip():
            all_allowed = sorted({file for t in plan.tasks for file in t.allowed_files})
            all_diffs_text = f"[Note: No changes were needed across the whole release. You MUST inspect the target files to verify implementation: {', '.join(all_allowed)}]"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": (
                f"Original Requirement: {issue}\n"
                f"Active learned rules:\n{active_rules or 'None'}\n"
                f"Complete Implementation:\n{all_diffs_text}\n\n"
                "CRITICAL INSTRUCTION: Diffs only show changed lines. Variables, functions, or imports used in the diffs but not shown are likely already defined in the existing files. "
                "Before declaring that something is 'missing' or 'not defined', you MUST use tools (like read_file_content) to inspect the surrounding code of the modified files."
            )}
        ]
        
        return await self._run_tool_loop(messages, tool_handler)

    async def _run_tool_loop(self, messages: list, tool_handler: CodebaseTools) -> GateResult:
        for step in range(10):
            response, metrics = await LLMClient.chat(
                model_id=self.model_id, 
                messages=messages, 
                tools=CODEBASE_TOOLS_SCHEMA
            )
            
            if hasattr(response, "tool_calls") and response.tool_calls:
                messages.append(response.model_dump())
                for tool_call in response.tool_calls:
                    tool_name = tool_call.function.name
                    try:
                        args = json.loads(tool_call.function.arguments)
                        tool_result = tool_handler.execute_tool(tool_name, args)
                    except Exception as e:
                        tool_result = f"Error: {str(e)}"
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_name,
                        "content": tool_result
                    })
                continue
                
            raw_response = response
            try:
                clean_json = raw_response.strip().replace("```json", "").replace("```", "").strip()
                return self._parse_response(clean_json, metrics)
            except Exception:
                return self._parse_response(raw_response, metrics)

        return GateResult(approved=False, error_type="incoherent", critique="Gatekeeper exceeded maximum tool calls.", confidence=0.0)
