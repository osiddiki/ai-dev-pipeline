import asyncio
import structlog
import json
import sys
import os
import re
import traceback
import yaml
from typing import Any, Optional, List
from agents.supervisor import SupervisorAgent, SupervisorPlan, TaskDefinition
from agents.worker import WorkerAgent, WorkerResult
from agents.gatekeeper import GatekeeperAgent
from agents.researcher import ResearcherAgent
from agents.meta_analyzer import MetaAnalyzerAgent
from agents.models import GateConfig
from orchestrator.verifier import VerifierEngine
from ledger.database import get_db
from environment.sandbox import DockerSandbox
from integrations.gemini_client import LLMClient

logger = structlog.get_logger()

class ReleaseArcOrchestrator:
    """Manages the full GATE pipeline execution (The Release Arc)."""
    
    def __init__(
        self, 
        target_repo: str, 
        guidelines: Optional[str] = None,
        config: Optional[GateConfig] = None,
        protected_paths: Optional[List[str]] = None
    ):
        self.target_repo = target_repo
        # Centralized metadata storage
        self.project_name = os.path.basename(self.target_repo.rstrip("/"))
        self.metadata_dir = f"metadata/{self.project_name}"
        os.makedirs(self.metadata_dir, exist_ok=True)
        
        self.guidelines = guidelines or "Follow professional best practices."
        self.config = config or GateConfig()
        self.protected_paths = protected_paths or ["provider-portal-app", "sevicare-app", "admin-portal", "vendor-portal"]
        
        self.supervisor = SupervisorAgent(model_id=self.config.planner_model)
        self.worker = WorkerAgent(model_id=self.config.executor_model)
        self.gatekeeper = GatekeeperAgent(model_id=self.config.verifier_model)
        self.researcher = ResearcherAgent(model_id=self.config.planner_model)
        self.meta_analyzer = MetaAnalyzerAgent(model_id=self.config.planner_model)
        
    async def gather_context(self) -> str:
        """Gather structural, textual, and symbolic context from the repository."""
        sandbox = DockerSandbox(self.target_repo)
        structure_out, _ = await asyncio.to_thread(sandbox.execute_command, "find . -maxdepth 2 -not -path '*/.*' 2>/dev/null || ls -F")
        
        # Load offline RAG index
        index_path = f"{self.metadata_dir}/repo_index.txt"
        repo_map_out = ""
        if os.path.exists(index_path):
            with open(index_path, "r") as f:
                repo_map_out = f.read()
        else:
            print("⚠️  No offline RAG index found. Run 'python scripts/build_repo_index.py <repo_path>' to improve context retrieval.")

        doc_files = ["AGENTS.md", "sevicare-app/AGENTS.md", "README.md", "CONTRIBUTING.md", "sevicare-app/README.md"]
        docs_content = []
        for doc in doc_files:
            content = sandbox.read_file(doc)
            if "[File does not exist]" not in content and len(content.strip()) > 0:
                docs_content.append(f"--- FILE: {doc} ---\n{content[:2000]}")
        
        repowise_out, _ = await asyncio.to_thread(sandbox.execute_command, "find . -name '*repowise*' -type f -maxdepth 4 2>/dev/null")
        if repowise_out.strip():
            for rw_file in repowise_out.strip().split("\n")[:3]:
                rw_content = sandbox.read_file(rw_file)
                if "[File does not exist]" not in rw_content:
                    docs_content.append(f"--- REPOWISE: {rw_file} ---\n{rw_content[:3000]}")

        # 5. HUMAN RULINGS & PROJECT SOPs (Context Priming)
        # Read from centralized metadata storage
        rulings_local_path = f"{self.metadata_dir}/RULINGS.md"
        if os.path.exists(rulings_local_path):
            with open(rulings_local_path, "r") as f:
                rulings_content = f.read()
            if rulings_content.strip():
                docs_content.append(f"\n--- HUMAN RULINGS & PROJECT SOPs ---\n{rulings_content}")

        full_context = f"REPOSITORY STRUCTURE:\n{structure_out}\n\nSYMBOL MAP:\n{repo_map_out}\n\nCRITICAL DOCUMENTATION:\n" + "\n".join(docs_content)
        return full_context

    def apply_patches(self, current_content: str, response: str) -> str:
        """Surgically extract and apply SEARCH/REPLACE blocks using strict line-based markers."""
        new_content = current_content
        
        # Strict pattern: markers must be at the start of a line
        # Regex explanation:
        # ^<<<< SEARCH\s*(.*?)\s*   - SEARCH tag at start of line
        # \n={4,}\s*(.*?)\s*        - Separator line
        # \n>>>> REPLACE            - REPLACE tag at start of line
        pattern = re.compile(r"^<<<< SEARCH\s*(.*?)\s*\n={4,}\s*(.*?)\s*\n>>>> REPLACE", re.DOTALL | re.MULTILINE)
        blocks = pattern.findall(response)
        
        if not blocks:
            # Fallback 1: Manual string splitting if regex fails (e.g. indentation issues)
            if "<<<< SEARCH" in response and ">>>> REPLACE" in response:
                logger.warning("Regex failed to find blocks but tags exist. Falling back to string splitting.")
                raw_blocks = response.split(">>>> REPLACE")
                applied_any = False
                for raw_block in raw_blocks:
                    if "<<<< SEARCH" not in raw_block: continue
                    try:
                        parts = raw_block.split("<<<< SEARCH")
                        block_body = parts[1]
                        sections = re.split(r"\n={4,}\n", block_body)
                        if len(sections) < 2: sections = re.split(r"={4,}", block_body)
                        if len(sections) >= 2:
                            search_text, replace_text = sections[0].strip(), sections[1].strip()
                            if not search_text and not new_content.strip():
                                new_content = replace_text
                                applied_any = True
                            elif search_text in new_content:
                                new_content = new_content.replace(search_text, replace_text)
                                applied_any = True
                    except: pass
                if applied_any: return new_content

            # Fallback 2: Extract clean markdown block
            content = response
            if "```" in response:
                parts = response.split("```")
                if len(parts) >= 3:
                    content = parts[1]
                    if "\n" in content: content = content.split("\n", 1)[1]
            
            # Failsafe: Forcefully strip tags
            content = re.sub(r"<<<< SEARCH\s*", "", content)
            content = re.sub(r"={4,}\s*", "", content)
            content = re.sub(r">>>> REPLACE\s*", "", content)
            return content.strip()

        for search_text, replace_text in blocks:
            search_text = search_text.strip()
            replace_text = replace_text.strip()
            if not search_text and not new_content.strip():
                new_content = replace_text
            elif search_text in new_content:
                new_content = new_content.replace(search_text, replace_text)
            else:
                logger.error("SEARCH block match failed", search_preview=search_text[:50])
                raise ValueError(f"Surgical patch failed: Could not find exact match for SEARCH block in file.")
                
        return new_content

    async def process_issue(self, issue_id: str, issue_description: str, manual_plan: Optional[SupervisorPlan] = None) -> bool:
        """Process a task through the GATE framework with Checkpoint & Resume."""
        db = await get_db()
        arc_id = None
        
        try:
            # CHECKPOINT: Look for existing arc
            repo_context = None
            discovery_report = None
            async with db.execute(
                "SELECT id, status, repo_context, discovery_report FROM release_arcs WHERE issue_id = ? AND status != 'completed' ORDER BY id DESC LIMIT 1", 
                (issue_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    arc_id, _, repo_context, discovery_report = row
                    print(f"🔄 Resuming existing mission: {issue_id}")
                    await db.execute("UPDATE release_arcs SET status = 'in_progress', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (arc_id,))
                else:
                    print(f"🎯 Starting new mission: {issue_id}")
                    cursor = await db.execute("INSERT INTO release_arcs (issue_id, repository, status) VALUES (?, ?, 'planning')", (issue_id, self.target_repo))
                    arc_id = cursor.lastrowid
                await db.commit()

            print(f"🔍 The Scout is mapping the repository...")
            repo_context = await self.gather_context()
            await db.execute("UPDATE release_arcs SET repo_context = ? WHERE id = ?", (repo_context, arc_id))
            await db.commit()

            force_research = False
            async with db.execute("SELECT error_type FROM gate_reviews WHERE arc_id = ? ORDER BY id DESC LIMIT 1", (arc_id,) ) as cursor:
                last_error = await cursor.fetchone()
                if last_error and last_error[0] == 'incoherent':
                    print("⚠️  The team is confused. Re-performing deep research...")
                    force_research = True

            if not discovery_report or force_research:
                print("🧠 The Researcher is studying your requirements...")
                research_result = await self.researcher.invoke({"repo_path": self.target_repo, "repo_context": repo_context}, issue_description)
                discovery_report = research_result.output
                await db.execute("UPDATE release_arcs SET discovery_report = ? WHERE id = ?", (discovery_report, arc_id))
                await db.commit()
            else:
                print("📂 Using cached project intelligence.")
                
            # META-ANALYSIS LOOP
            print("🔬 The Meta-Analyzer is reviewing historical data...")
            meta_result = await self.meta_analyzer.invoke({}, db)
            if meta_result.success and meta_result.output:
                print(f"⚠️  META-WARNING INJECTED: {meta_result.output}")
                # We inject this directly into the guidelines so all agents see it
                self.guidelines += f"\n\nCRITICAL META-WARNING FROM PAST FAILURES:\n{meta_result.output}\n"
            
            context = {"guidelines": self.guidelines, "repo_path": self.target_repo, "repo_context": repo_context, "discovery_report": discovery_report}
            
            plan = None
            if manual_plan:
                print("📋 Using manual override plan.")
                plan = manual_plan
            else:
                # TIER 1: PLANNING PHASE
                plan_file = f"{self.metadata_dir}/PLAN_{issue_id}.md"
                
                # Check if an approved plan already exists
                if os.path.exists(plan_file):
                    print(f"📄 Found existing plan for {issue_id}. Loading...")
                    with open(plan_file, "r") as f:
                        plan_json = f.read()
                    try:
                        # Find the JSON block in the markdown
                        json_str = plan_json.split("```json")[1].split("```")[0].strip()
                        plan_data = json.loads(json_str)
                        plan = SupervisorPlan(tasks=[TaskDefinition(**t) for t in plan_data])
                    except Exception as e:
                        logger.error("Failed to parse existing plan file. Generating new one.", error=str(e))
                        plan = None
                
                if not plan:
                    print("🏗️  The Architect is decomposing the requirements into a draft plan...")
                    plan_result = await self.supervisor.invoke(context, issue_description)
                    if not plan_result.success: return False
                    plan = plan_result.output
                    
                    max_revisions = 3
                    for attempt in range(max_revisions + 1):
                        print(f"🛡️  The Gatekeeper is reviewing the draft plan (Attempt {attempt+1})...")
                        plan_gate = await self.gatekeeper.review_plan(issue_description, plan)
                        await self._log_gate(db, arc_id, None, "review_plan", plan_gate.approved, plan_gate.critique, error_type=plan_gate.error_type, attempt=attempt+1, metrics=plan_gate.metrics)
                        if plan_gate.approved:
                            print("✅ Draft Plan approved by Gatekeeper.")
                            break
                        if attempt < max_revisions:
                            print(f"❌ Plan rejected: {plan_gate.error_type}. The Architect is revising...")
                            revision_result = await self.supervisor.revise_plan(plan, plan_gate.critique, context)
                            if not revision_result.success: return False
                            plan = revision_result.output
                        else:
                            print("🚫 Plan failed final review.")
                            return False

                    # Save the plan to disk for human review
                    os.makedirs(os.path.dirname(plan_file), exist_ok=True)
                    with open(plan_file, "w") as f:
                        f.write(f"# Implementation Plan for {issue_id}\n\n")
                        f.write("## Original Requirement\n")
                        f.write(f"{issue_description}\n\n")
                        f.write("## Execution Tasks\n")
                        f.write("Review this plan. If correct, run the pipeline again to execute. If incorrect, edit the JSON below.\n\n")
                        f.write("```json\n")
                        f.write(json.dumps([t.dict() for t in plan.tasks], indent=2))
                        f.write("\n```\n")
                    
                    print("\n" + "="*60)
                    print(f"🛑 PLANNING PAUSE: Draft plan saved to {plan_file}")
                    print("1. Please open the file and review the tasks.")
                    print("2. Make any manual edits to the JSON block if the AI hallucinated.")
                    print("3. Run the pipeline again to begin Tier 3 execution.")
                    print("="*60 + "\n")
                    return True # Stop execution here for human review

            all_diffs = []
            task_memory = "" # PERSISTENT CONTEXT FOR THE WHOLE ARC
            
            # TOPOLOGICAL SORT
            task_dict = {t.id: t for t in plan.tasks}
            resolved = []
            visited = set()
            visiting = set()
            
            def visit(task_id):
                if task_id in visited: return True
                if task_id in visiting: return False # Circular dependency
                visiting.add(task_id)
                task = task_dict.get(task_id)
                if task:
                    for dep in task.dependencies:
                        if not visit(dep): return False
                    resolved.append(task)
                visiting.remove(task_id)
                visited.add(task_id)
                return True

            for task in plan.tasks:
                if not visit(task.id):
                    logger.error("Circular dependency detected in plan")
                    return False
                    
            print(f"🚦 Execution order resolved ({len(resolved)} tasks).")

            for task in resolved:
                async with db.execute("SELECT status FROM tasks WHERE arc_id = ? AND description = ? AND status = 'completed'", (arc_id, task.description)) as cursor:
                    if await cursor.fetchone():
                        print(f"⏭️  Skipping completed task: {task.id}")
                        continue

                max_task_attempts = 3
                task_attempt = 0
                task_success = False
                last_error_feedback = ""
                current_task_desc = task.description

                await db.execute("INSERT INTO tasks (arc_id, description, status) VALUES (?, ?, 'in_progress')", (arc_id, task.description))
                await db.commit()

                while task_attempt < max_task_attempts:
                    task_attempt += 1
                    
                    # GIT ROLLBACK: Ensure pristine state for each attempt (Graceful fallback if not a git repo)
                    print(f"🧹 Resetting workspace to pristine state...")
                    rollback_cmd = "if git rev-parse --is-inside-work-tree > /dev/null 2>&1; then git reset --hard HEAD && git clean -fd; else echo 'Not a git repo, skipping rollback'; fi"
                    await asyncio.to_thread(DockerSandbox(self.target_repo).execute_command, rollback_cmd)

                    # Strike-Two Breakout: Ask human for hint after 2 fails
                    if task_attempt == 3 and not task_success:
                        print("\n" + "!"*60 + f"\n🚨 STRIKE TWO: The AI is stuck on {task.id}\nCRITIQUE: {last_error_feedback[:500]}\n" + "!"*60)
                        user_hint = input("\nProvide a strategic hint to break the loop, or type 'skip': ").strip()
                        if user_hint.lower() == 'skip':
                            task_success = True
                            break
                        elif user_hint:
                            current_task_desc += f"\n\nSTRATEGIC HINT: {user_hint}"
                            # PERSISTENT LEARNING: Save rule to RULINGS.md
                            rulings_path = f"{self.metadata_dir}/RULINGS.md"
                            with open(rulings_path, "a") as rf:
                                rf.write(f"\n- **Rule from {task.id}**: {user_hint}\n")
                            print("🧠 Hint permanently saved to Project Rulings.")

                        print(f"🔨 The Engineer is working on {task.id} (Attempt {task_attempt})...")

                    mission_desc = current_task_desc
                    if task_attempt > 1:
                        print(f"🔄 Retrying {task.id} with self-healing feedback...")
                        mission_desc += f"\n\nPREVIOUS FAILURE FEEDBACK:\n{last_error_feedback}"
                    
                    temp_task = TaskDefinition(id=task.id, description=mission_desc, target_file=task.target_file, dependencies=task.dependencies, design_constraints=task.design_constraints, acceptance_criteria=task.acceptance_criteria)
                    
                    # Ensure Worker proposes a design before execution
                    design_request = f"Task: {temp_task.description}\nConstraints: {temp_task.design_constraints}\n"
                    design_request += "Provide a detailed technical design proposal for this task. Include specific class names, function signatures, and logic steps. "
                    design_request += "Your proposal will be reviewed by a Senior Architect, and then used by a Worker to implement the code."
                    
                    design_proposal, _ = await LLMClient.chat(model_id=self.config.executor_model, messages=[{"role": "user", "content": design_request}])
                    
                    design_gate = await self.gatekeeper.review_design(temp_task, design_proposal)
                    if not design_gate.approved: 
                        print(f"⚠️  Design check failed for {task.id}. Self-healing...")
                        last_error_feedback = f"Design Rejected: {design_gate.critique}"
                        continue
                    
                    # Inject approved design into context for the worker
                    context["approved_design"] = design_proposal
                    
                    # TEMPERATURE ESCALATION
                    retry_temp = 0.3 + (task_attempt - 1) * 0.2
                    worker_res = await self.worker.invoke(context, temp_task, temperature=retry_temp)
                    w_output: WorkerResult = worker_res.output
                    
                    # TASK MEMORY
                    if not task.target_file:
                        task_memory += f"\n--- PERSISTENT ANALYSIS ({task.id}) ---\n{w_output.diff}\n"
                    context["task_memory"] = task_memory
                    
                    print(f"🛡️  The Gatekeeper is performing code review...")
                    cr_gate = await self.gatekeeper.codereview(temp_task, w_output)
                    
                    if cr_gate.approved and cr_gate.confidence < self.config.min_gate_confidence:
                        print(f"⚠️  Gatekeeper approved, but confidence ({cr_gate.confidence}) is below threshold ({self.config.min_gate_confidence}). Forcing retry.")
                        cr_gate.approved = False
                        cr_gate.error_type = "incoherent"
                        cr_gate.critique += f"\n\nSYSTEM OVERRIDE: Approval confidence ({cr_gate.confidence}) too low."

                    verification_method_used = None
                    if cr_gate.approved:
                        # STAGE THE CHANGE
                        if task.target_file:
                            if any(p in task.target_file for p in self.protected_paths):
                                print(f"🛑 SOURCE GUARD BLOCKED WRITE: {task.target_file}")
                            else:
                                filepath = f"{self.target_repo}/{task.target_file}"
                                print(f"💾 Pre-flight staging: {task.target_file}...")
                                os.makedirs(os.path.dirname(filepath), exist_ok=True)
                                existing_content = ""
                                if os.path.exists(filepath):
                                    with open(filepath, "r") as f: existing_content = f.read()
                                
                                try:
                                    # 1. APPLY PATCH IN MEMORY
                                    updated_content = self.apply_patches(existing_content, w_output.diff)
                                    
                                    # 2. LINTER PRE-FLIGHT
                                    dir_name = os.path.dirname(filepath)
                                    base_name = os.path.basename(filepath)
                                    tmp_path = os.path.join(dir_name, f".tmp.{base_name}")
                                    tmp_rel_path = os.path.join(os.path.dirname(task.target_file), f".tmp.{base_name}")

                                    with open(tmp_path, "w") as f: f.write(updated_content)
                                    
                                    ext = os.path.splitext(task.target_file)[1]
                                    linter_cmd = None
                                    if ext == ".ts": linter_cmd = f"npx tsc --noEmit {tmp_rel_path}"
                                    elif ext == ".py": linter_cmd = f"python3 -m py_compile {tmp_rel_path}"
                                    
                                    if linter_cmd:
                                        print(f"🔍 Running linter pre-flight on {task.target_file}...")
                                        lint_out, lint_code = await asyncio.to_thread(DockerSandbox(self.target_repo).execute_command, linter_cmd)
                                        if lint_code != 0:
                                            os.remove(tmp_path)
                                            raise ValueError(f"Linter pre-flight failed:\n{lint_out}")

                                    # 3. ATOMIC COMMIT
                                    os.rename(tmp_path, filepath)
                                except Exception as e:
                                    last_error_feedback = f"Surgical patch or linter failed: {str(e)}"
                                    print(f"❌ Pre-flight failed: {str(e)}")
                                    cr_gate.approved = False
                                    continue

                        print(f"🧪 Running autonomous reality check...")
                        verifier = VerifierEngine(sandbox=DockerSandbox(self.target_repo))
                        changed_files = [task.target_file] if task.target_file else []
                        verification = await verifier.verify(temp_task.description, repo_context, changed_files)
                        
                        if not verification.success:
                            print(f"❌ Reality check failed: {verification.reason}")
                            cr_gate.approved = False
                            cr_gate.error_type = "systematic"
                            cr_gate.critique += f"\n\nEMPIRICAL FAILURE:\n{verification.reason}"
                            last_error_feedback = cr_gate.critique
                        else:
                            print(f"✨ Task {task.id} passed all gates.")
                            all_diffs.append(w_output)
                            await db.execute("UPDATE tasks SET status = 'completed', updated_at = CURRENT_TIMESTAMP WHERE arc_id = ? AND description = ?", (arc_id, task.description))
                            await db.commit()
                            
                            # GIT CHECKPOINT (LOCAL ONLY)
                            print(f"📦 Creating local Git checkpoint for {task.id}...")
                            checkpoint_cmd = f"git add . && git commit -m 'GATE Checkpoint: {task.id} - {task.description[:50]}'"
                            await asyncio.to_thread(DockerSandbox(self.target_repo).execute_command, checkpoint_cmd)
                            
                            task_success = True
                            break

                    await self._log_gate(db, arc_id, task.id, "codereview", cr_gate.approved, cr_gate.critique, error_type=cr_gate.error_type, attempt=task_attempt, verification_method=verification_method_used, metrics=cr_gate.metrics)
                    
                    if not cr_gate.approved:
                        last_error_feedback = cr_gate.critique
                        print(f"❌ Gate review failed: {cr_gate.error_type}. Self-healing...")
                
                if not task_success:
                    print(f"🚫 Task {task.id} failed after {max_task_attempts} attempts.")
                    return False
                    
            print(f"🏁 Finalizing mission review...")
            final_gate = await self.gatekeeper.review_code(issue_description, plan, all_diffs)
            await self._log_gate(db, arc_id, None, "review_code", final_gate.approved, final_gate.critique, error_type=final_gate.error_type, metrics=final_gate.metrics)
            status = "completed" if final_gate.approved else "failed"
            await db.execute("UPDATE release_arcs SET status = ? WHERE id = ?", (status, arc_id))
            await db.commit()
            print(f"\n🚀 Mission Status: {status.upper()}")
            return final_gate.approved
            
        except Exception as e:
            print(f"\n💥 PIPELINE CRASHED: {str(e)}")
            traceback.print_exc()
            if arc_id:
                await db.execute("UPDATE release_arcs SET status = 'failed' WHERE id = ?", (arc_id,))
                await self._log_gate(db, arc_id, None, "INTERNAL_CRASH", False, f"Python Exception: {str(e)}\n\n{traceback.format_exc()}", error_type="systematic")
                await db.commit()
            return False

    async def _log_gate(self, db, arc_id, task_id, gate_name, approved, critique, error_type=None, attempt=1, verification_method=None, metrics=None):
        status = "approved" if approved else "rejected"
        metrics = metrics or {}
        prompt_t = metrics.get("prompt_tokens", 0)
        comp_t = metrics.get("completion_tokens", 0)
        try:
            await db.execute(
                """INSERT INTO gate_reviews (arc_id, task_id, gate_name, model_id, status, error_type, critique_summary, attempt_number, verification_method, prompt_tokens, completion_tokens)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (arc_id, task_id, gate_name, self.gatekeeper.model_id, status, error_type, critique, attempt, verification_method, prompt_t, comp_t)
            )
        except Exception as e:
             logger.warning("Failed to log rich gate metrics, falling back to simple log", error=str(e))
             await db.execute(
                """INSERT INTO gate_reviews (arc_id, task_id, gate_name, model_id, status, error_type, critique_summary, attempt_number)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (arc_id, task_id, gate_name, self.gatekeeper.model_id, status, error_type, critique, attempt)
            )
        await db.commit()

if __name__ == "__main__":
    async def run():
        try:
            print("\n" + "="*50 + "\n🚀 GATE PIPELINE ORCHESTRATOR\n" + "="*50)
            
            # SESSION PERSISTENCE: Check for last used repo and ID
            session_file = "metadata/LAST_SESSION.json"
            target_repo = None
            issue_id = None
            
            if os.path.exists(session_file):
                with open(session_file, "r") as f:
                    last_session = json.load(f)
                    
                resume_input = input(f"\n🔄 Continue from last session?\n   Repo: {last_session.get('repo')}\n   Issue: {last_session.get('issue_id')}\n   [Y/n]: ").strip().lower()
                
                if resume_input != 'n':
                    target_repo = last_session.get('repo')
                    issue_id = last_session.get('issue_id')
            
            if not target_repo:
                target_repo = input(f"\nEnter Target Repo Path [current dir]: ").strip() or "."
            
            project_name = os.path.basename(target_repo.rstrip("/"))
            metadata_dir = f"metadata/{project_name}"
            os.makedirs(metadata_dir, exist_ok=True)
            
            # Decoupled Configuration Loading (Centralized)
            config_path = os.path.join(metadata_dir, "gate.yml")
            project_guidelines = "Follow standard engineering best practices."
            orch_protected_paths = None
            if os.path.exists(config_path):
                print(f"📄 Loaded project configuration from {config_path}")
                with open(config_path, "r") as f:
                    gate_cfg = yaml.safe_load(f)
                    
                project_guidelines = f"PROJECT GOAL:\n{gate_cfg.get('project_goal', '')}\n\n"
                project_guidelines += f"TECHNICAL STACK:\n{yaml.dump(gate_cfg.get('technical_stack', []))}\n\n"
                project_guidelines += f"ARCHITECTURE:\n{gate_cfg.get('architecture', '')}\n\n"
                project_guidelines += f"CONSTRAINTS:\n{gate_cfg.get('constraints', '')}\n\n"
                project_guidelines += f"PROJECT GUIDELINES:\n{gate_cfg.get('guidelines', '')}"
                
                orch_protected_paths = gate_cfg.get('protected_paths', ["provider-portal-app", "sevicare-app", "admin-portal", "vendor-portal"])
            else:
                print(f"⚠️  No gate.yml found in {metadata_dir}. Operating with generic default guidelines.")

            orch = ReleaseArcOrchestrator(target_repo=target_repo, guidelines=project_guidelines, protected_paths=orch_protected_paths)
            
            if not issue_id:
                issue_id = input("\nEnter Issue ID [e.g. TICKET-123]: ").strip()
                if not issue_id:
                    print("❌ Issue ID is required.")
                    sys.exit(1)
            
            # SAVE CURRENT SESSION
            with open(session_file, "w") as f:
                json.dump({"repo": target_repo, "issue_id": issue_id}, f)
            
            # Check if resuming before asking for description
            plan_file = os.path.join(metadata_dir, f"PLAN_{issue_id}.md")
            issue_desc = ""
            
            if os.path.exists(plan_file):
                print(f"📄 Existing plan found. Resuming execution for {issue_id}...")
                # We extract the original description from the markdown file
                with open(plan_file, "r") as f:
                    content = f.read()
                    if "## Original Requirement\n" in content:
                        issue_desc = content.split("## Original Requirement\n")[1].split("## Execution Tasks")[0].strip()
            else:
                print("\nDescribe the overarching task or feature requirements:")
                issue_desc = input("Task Description: ").strip()
                if not issue_desc:
                    print("❌ Task Description is required.")
                    sys.exit(1)
                
            await orch.process_issue(issue_id, issue_desc)
        except KeyboardInterrupt:
            print("\n👋 Shutdown requested by user.")
        except Exception as e:
            print(f"\n💥 STARTUP ERROR: {str(e)}")
        finally:
            # CLEAN SHUTDOWN: Wait for background tasks to finish
            try:
                pending = asyncio.all_tasks()
                for task in pending:
                    if task is not asyncio.current_task(): task.cancel()
                
                # Give tasks a moment to cancel
                if pending:
                    await asyncio.gather(*[p for p in pending if p is not asyncio.current_task()], return_exceptions=True)
                
                loop = asyncio.get_event_loop()
                await loop.shutdown_asyncgens()
                # Do NOT close the loop here, asyncio.run handles that
                print("✨ Resources released.")
            except Exception as shutdown_err:
                # Silence final shutdown noise
                pass
    asyncio.run(run())
