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
        """Robust state-machine parser for SEARCH/REPLACE blocks with normalized whitespace fallback."""
        new_content = current_content
        lines = response.splitlines()
        
        in_search = False
        in_replace = False
        search_lines = []
        replace_lines = []
        applied_any = False
        
        def normalize(s): 
            """Remove all non-essential whitespace for comparison."""
            return re.sub(r"\s+", "", s)
            
        for line in lines:
            stripped = line.strip()
            # Lenient tag detection (supports <<<< SEARCH, <SEARCH, SEARCH:, etc.)
            if "SEARCH" in stripped and ("<" in stripped or ":" in stripped):
                in_search = True
                search_lines = []
                continue
            elif in_search and ("====" in stripped or "----" in stripped):
                in_search = False
                in_replace = True
                replace_lines = []
                continue
            elif in_replace and "REPLACE" in stripped and (">" in stripped or ":" in stripped):
                in_replace = False
                s_text = "\n".join(search_lines).strip()
                r_text = "\n".join(replace_lines).strip()
                
                # Handle creation or total replacement
                if not s_text:
                    if not applied_any:
                        new_content = r_text
                    else:
                        new_content += "\n" + r_text
                    applied_any = True
                elif s_text in new_content:
                    new_content = new_content.replace(s_text, r_text)
                    applied_any = True
                else:
                    # FUZZY/NORMALIZED FALLBACK
                    norm_search = normalize(s_text)
                    if norm_search:
                        # Attempt to find the block by ignoring all whitespace
                        # We use a regex that ignores all whitespace between characters
                        pattern_str = r"\s*".join(re.escape(c) for c in s_text if not c.isspace())
                        try:
                            match = re.search(pattern_str, new_content, re.DOTALL)
                            if match:
                                logger.info("FUZZY MATCH SUCCESS: Applied patch despite whitespace differences.")
                                span = match.span()
                                new_content = new_content[:span[0]] + r_text + new_content[span[1]:]
                                applied_any = True
                                continue
                        except: pass
                    
                    logger.warning("SEARCH block match failed", preview=s_text[:50])
                continue
                
            if in_search:
                search_lines.append(line)
            elif in_replace:
                replace_lines.append(line)

        # FALLBACK: If state machine failed to apply any patches, try to extract a raw code block
        if not applied_any:
            if "```" in response:
                parts = response.split("```")
                if len(parts) >= 3:
                    inner = parts[1]
                    content = inner.split("\n", 1)[1].strip() if "\n" in inner else inner.strip()
                    # SCRUBBER: Forcefully remove tags if they bled into the code block
                    content = re.sub(r"<<<< SEARCH\s*", "", content)
                    content = re.sub(r"={4,}\s*", "", content)
                    content = re.sub(r">>>> REPLACE\s*", "", content)
                    return content.strip()
            return response.strip()

        return new_content

    async def process_issue(self, issue_id: str, issue_description: str, manual_plan: Optional[SupervisorPlan] = None) -> bool:
        """Process a task through the GATE framework with Deterministic Task Tracking."""
        db = await get_db()
        
        # MIGRATION: Add task_id column if it doesn't exist
        try:
            await db.execute("ALTER TABLE tasks ADD COLUMN task_id TEXT")
            await db.commit()
            logger.info("Migrated database: Added task_id column to tasks table.")
        except Exception:
            pass # Column already exists
            
        arc_id = None
        
        try:
            # CHECKPOINT: Arc Loading
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

            print(f"🔍 The Scout is gathering intelligence...")
            repo_context = await self.gather_context()
            await db.execute("UPDATE release_arcs SET repo_context = ? WHERE id = ?", (repo_context, arc_id))
            await db.commit()

            force_research = False
            async with db.execute("SELECT error_type FROM gate_reviews WHERE arc_id = ? ORDER BY id DESC LIMIT 1", (arc_id,) ) as cursor:
                last_error = await cursor.fetchone()
                if last_error and last_error[0] == 'incoherent':
                    print("⚠️  Deep research required...")
                    force_research = True

            if not discovery_report or force_research:
                print("🧠 The Researcher is studying the codebase...")
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
                self.guidelines += f"\n\nCRITICAL META-WARNING FROM PAST FAILURES:\n{meta_result.output}\n"
            
            context = {"guidelines": self.guidelines, "repo_path": self.target_repo, "repo_context": repo_context, "discovery_report": discovery_report}
            
            plan = None
            if manual_plan:
                plan = manual_plan
            else:
                plan_file = f"{self.metadata_dir}/PLAN_{issue_id}.md"
                if os.path.exists(plan_file):
                    print(f"📄 Loading approved plan for {issue_id}...")
                    with open(plan_file, "r") as f:
                        content = f.read()
                    try:
                        json_str = content.split("```json")[1].split("```")[0].strip()
                        plan_data = json.loads(json_str)
                        plan = SupervisorPlan(tasks=[TaskDefinition(**t) for t in plan_data])
                    except Exception as e:
                        logger.error("Plan parsing failed", error=str(e))
                        plan = None
                
                if not plan:
                    print("🏗️  The Architect is drafting the blueprint...")
                    plan_result = await self.supervisor.invoke(context, issue_description)
                    if not plan_result.success: return False
                    plan = plan_result.output
                    
                    max_revisions = 3
                    for attempt in range(max_revisions + 1):
                        print(f"🛡️  The Gatekeeper is reviewing the blueprint (Attempt {attempt+1})...")
                        plan_gate = await self.gatekeeper.review_plan(issue_description, plan)
                        await self._log_gate(db, arc_id, None, "review_plan", plan_gate.approved, plan_gate.critique, error_type=plan_gate.error_type, attempt=attempt+1, metrics=plan_gate.metrics)
                        if plan_gate.approved:
                            print("✅ Blueprint approved.")
                            break
                        if attempt < max_revisions:
                            print(f"❌ Blueprint rejected: {plan_gate.error_type}. Revising...")
                            revision_result = await self.supervisor.revise_plan(plan, plan_gate.critique, context)
                            if not revision_result.success: return False
                            plan = revision_result.output
                        else:
                            print("🚫 Blueprint failed final review.")
                            return False

                    os.makedirs(os.path.dirname(plan_file), exist_ok=True)
                    with open(plan_file, "w") as f:
                        f.write(f"# Implementation Plan for {issue_id}\n\n## Original Requirement\n{issue_description}\n\n## Execution Tasks\n```json\n{json.dumps([t.model_dump() for t in plan.tasks], indent=2)}\n```\n")
                    print("\n" + "="*60 + f"\n🛑 PLANNING PAUSE: Draft plan saved to {plan_file}\n" + "="*60 + "\n")
                    return True

            # TOPOLOGICAL SORT
            task_dict = {t.id: t for t in plan.tasks}
            resolved = []
            visited = set()
            visiting = set()
            def visit(task_id):
                if task_id in visited: return True
                if task_id in visiting: return False
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
                if not visit(task.id): return False
            
            print(f"🚦 Execution queue: {[t.id for t in resolved]}")

            task_memory = ""
            for i, task in enumerate(resolved):
                print(f"\n" + "-"*40 + f"\n⚡ TASK {i+1}/{len(resolved)}: {task.id}\n" + "-"*40)
                
                # Check for completion by ID (Robust deterministic skip)
                async with db.execute("SELECT status FROM tasks WHERE arc_id = ? AND task_id = ? AND status = 'completed'", (arc_id, task.id)) as cursor:
                    if await cursor.fetchone():
                        print(f"⏭️  Skipping completed task: {task.id}")
                        continue

                max_task_attempts = 3
                task_attempt = 0
                task_success = False
                last_error_feedback = ""
                current_task_desc = task.description

                while task_attempt < max_task_attempts:
                    task_attempt += 1
                    
                    # GUARD: Prevent re-running if success was just marked
                    if task_success: break
                    
                    # GIT ROLLBACK
                    rollback_cmd = "git config --global --add safe.directory /workspace || true; if git rev-parse --is-inside-work-tree > /dev/null 2>&1; then git reset --hard HEAD && git clean -fd; fi"
                    await asyncio.to_thread(DockerSandbox(self.target_repo).execute_command, rollback_cmd)

                    # Strike-Two Breakout
                    if task_attempt == 3 and not task_success:
                        print("\n" + "!"*60 + f"\n🚨 STRIKE TWO: Stuck on {task.id}\nCRITIQUE: {last_error_feedback[:1000]}\n" + "!"*60)
                        user_hint = input("\nStrategic hint ('skip' to bypass): ").strip()
                        if user_hint.lower() == 'skip':
                            task_success = True
                            break
                        elif user_hint:
                            current_task_desc += f"\n\nSTRATEGIC HINT: {user_hint}"
                            rulings_path = f"{self.metadata_dir}/RULINGS.md"
                            with open(rulings_path, "a") as rf:
                                rf.write(f"\n- **Rule from {task.id}**: {user_hint}\n")

                    print(f"🔨 Working on {task.id} (Attempt {task_attempt})...")
                    mission_desc = current_task_desc
                    if task_attempt > 1:
                        mission_desc += f"\n\nPREVIOUS FAILURE FEEDBACK:\n{last_error_feedback}"
                    
                    temp_task = TaskDefinition(id=task.id, description=mission_desc, target_file=task.target_file, dependencies=task.dependencies, design_constraints=task.design_constraints, acceptance_criteria=task.acceptance_criteria)
                    
                    # CONTEXT UPGRADE: Always inject package.json if it exists
                    pkg_path = f"{self.target_repo}/test-data-generator/package.json"
                    if os.path.exists(pkg_path):
                        with open(pkg_path, "r") as f:
                            pkg_content = f.read()
                        task_memory += f"\n--- CURRENT package.json ---\n{pkg_content}\n"
                    
                    # Task Record (Initialize attempt)
                    await db.execute("INSERT INTO tasks (arc_id, task_id, description, status) VALUES (?, ?, ?, 'in_progress')", (arc_id, task.id, task.description))
                    await db.commit()

                    # Gate 2: Design
                    design_request = f"Task: {temp_task.description}\nConstraints: {temp_task.design_constraints}\nProvide a detailed technical design proposal."
                    design_proposal, _ = await LLMClient.chat(model_id=self.config.executor_model, messages=[{"role": "user", "content": design_request}])
                    design_gate = await self.gatekeeper.review_design(temp_task, design_proposal)
                    if not design_gate.approved: 
                        last_error_feedback = f"Design Rejected: {design_gate.critique}"
                        await self._log_gate(db, arc_id, task.id, "review_design", False, design_gate.critique, error_type=design_gate.error_type, attempt=task_attempt)
                        continue
                    
                    context["approved_design"] = design_proposal
                    retry_temp = 0.3 + (task_attempt - 1) * 0.2
                    worker_res = await self.worker.invoke(context, temp_task, temperature=retry_temp)
                    w_output: WorkerResult = worker_res.output
                    
                    if not task.target_file:
                        task_memory += f"\n--- ANALYSIS ({task.id}) ---\n{w_output.diff}\n"
                    context["task_memory"] = task_memory
                    
                    # Gate 3: Code Review
                    print(f"🛡️  The Gatekeeper is performing code review...")
                    cr_gate = await self.gatekeeper.codereview(temp_task, w_output)
                    if cr_gate.approved and cr_gate.confidence < self.config.min_gate_confidence:
                        cr_gate.approved = False
                        cr_gate.critique += f"\n\nLOW CONFIDENCE OVERRIDE."

                    if cr_gate.approved:
                        if task.target_file:
                            if any(p in task.target_file for p in self.protected_paths):
                                print(f"🛑 SOURCE GUARD BLOCKED: {task.target_file}")
                            else:
                                filepath = f"{self.target_repo}/{task.target_file}"
                                os.makedirs(os.path.dirname(filepath), exist_ok=True)
                                existing_content = ""
                                if os.path.exists(filepath):
                                    with open(filepath, "r") as f: existing_content = f.read()
                                
                                try:
                                    updated_content = self.apply_patches(existing_content, w_output.diff)
                                    tmp_path_host = os.path.join(os.path.dirname(filepath), f".tmp.{os.path.basename(filepath)}")
                                    with open(tmp_path_host, "w") as f: f.write(updated_content)
                                    
                                    # LINTER PRE-FLIGHT
                                    ext = os.path.splitext(task.target_file)[1]
                                    tmp_rel_path = os.path.join(os.path.dirname(task.target_file), f".tmp.{os.path.basename(filepath)}")
                                    linter_cmd = None
                                    if ext == ".ts": linter_cmd = f"npx tsc --noEmit {tmp_rel_path}"
                                    elif ext == ".py": linter_cmd = f"python3 -m py_compile {tmp_rel_path}"
                                    
                                    if linter_cmd:
                                        print(f"🔍 Running linter pre-flight on {task.target_file}...")
                                        lint_out, lint_code = await asyncio.to_thread(DockerSandbox(self.target_repo).execute_command, linter_cmd)
                                        if lint_code != 0:
                                            if os.path.exists(tmp_path_host): os.remove(tmp_path_host)
                                            raise ValueError(f"Linter failed on .tmp file:\n{lint_out}")

                                    # REALITY CHECK (Rename THEN verify)
                                    os.rename(tmp_path_host, filepath)
                                    # Sync to Sandbox
                                    DockerSandbox(self.target_repo).write_file(task.target_file, updated_content)
                                    
                                    verifier = VerifierEngine(sandbox=DockerSandbox(self.target_repo))
                                    verification = await verifier.verify(temp_task.description, repo_context, [task.target_file])
                                    
                                    if not verification.success:
                                        print(f"❌ Reality check failed: {verification.reason}")
                                        cr_gate.approved = False
                                        cr_gate.critique += f"\n\nEMPIRICAL FAILURE: {verification.reason}"
                                        last_error_feedback = cr_gate.critique
                                    else:
                                        print(f"✨ Task {task.id} passed all gates.")
                                        all_diffs.append(w_output)
                                        await db.execute("UPDATE tasks SET status = 'completed', updated_at = CURRENT_TIMESTAMP WHERE arc_id = ? AND task_id = ?", (arc_id, task.id))
                                        await db.commit()
                                        await asyncio.to_thread(DockerSandbox(self.target_repo).execute_command, f"git add . && git commit -m 'GATE: {task.id}'")
                                        task_success = True
                                        break
                                except Exception as e:
                                    last_error_feedback = f"Execution failed: {str(e)}"
                                    cr_gate.approved = False
                                    continue
                        else:
                            # Analysis task
                            print(f"✨ Analysis {task.id} passed.")
                            task_success = True
                            await db.execute("UPDATE tasks SET status = 'completed', updated_at = CURRENT_TIMESTAMP WHERE arc_id = ? AND task_id = ?", (arc_id, task.id))
                            await db.commit()
                            break

                    if not cr_gate.approved:
                        last_error_feedback = cr_gate.critique
                        print(f"❌ Gate rejected: {cr_gate.error_type}")
                        await self._log_gate(db, arc_id, task.id, "codereview", False, cr_gate.critique, error_type=cr_gate.error_type, attempt=task_attempt, metrics=cr_gate.metrics)
                
                if not task_success: return False
                    
            final_gate = await self.gatekeeper.review_code(issue_description, plan, all_diffs)
            status = "completed" if final_gate.approved else "failed"
            await db.execute("UPDATE release_arcs SET status = ? WHERE id = ?", (status, arc_id))
            await db.commit()
            print(f"\n🚀 Mission: {status.upper()}")
            return final_gate.approved
            
        except Exception as e:
            print(f"\n💥 CRASH: {str(e)}")
            traceback.print_exc()
            return False

    async def _log_gate(self, db, arc_id, task_id, gate_name, approved, critique, error_type=None, attempt=1, verification_method=None, metrics=None):
        status = "approved" if approved else "rejected"
        metrics = metrics or {}
        try:
            await db.execute(
                """INSERT INTO gate_reviews (arc_id, task_id, gate_name, model_id, status, error_type, critique_summary, attempt_number, verification_method, prompt_tokens, completion_tokens)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (arc_id, task_id, gate_name, self.gatekeeper.model_id, status, error_type, critique, attempt, verification_method, metrics.get("prompt_tokens",0), metrics.get("completion_tokens",0))
            )
        except Exception as e:
             logger.warning("Log fail", error=str(e))
             await db.execute("INSERT INTO gate_reviews (arc_id, task_id, gate_name, model_id, status, error_type, critique_summary, attempt_number) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (arc_id, task_id, gate_name, self.gatekeeper.model_id, status, error_type, critique, attempt))
        await db.commit()

if __name__ == "__main__":
    async def run():
        try:
            print("\n" + "="*50 + "\n🚀 GATE PIPELINE ORCHESTRATOR\n" + "="*50)
            session_file = "metadata/LAST_SESSION.json"
            target_repo, issue_id = None, None
            if os.path.exists(session_file):
                with open(session_file, "r") as f: last_session = json.load(f)
                if input(f"\n🔄 Resume {last_session.get('issue_id')}? [Y/n]: ").strip().lower() != 'n':
                    target_repo, issue_id = last_session.get('repo'), last_session.get('issue_id')
            if not target_repo: target_repo = input(f"Target Repo: ").strip() or "."
            
            project_name = os.path.basename(target_repo.rstrip("/"))
            metadata_dir = f"metadata/{project_name}"
            os.makedirs(metadata_dir, exist_ok=True)
            
            config_path = os.path.join(metadata_dir, "gate.yml")
            project_guidelines, orch_protected_paths = "Standard practices.", None
            if os.path.exists(config_path):
                with open(config_path, "r") as f: gate_cfg = yaml.safe_load(f)
                project_guidelines = f"GOAL: {gate_cfg.get('project_goal', '')}\nSTACK: {gate_cfg.get('technical_stack', '')}\nARCH: {gate_cfg.get('architecture', '')}\nRULES: {gate_cfg.get('guidelines', '')}"
                orch_protected_paths = gate_cfg.get('protected_paths')
            
            orch = ReleaseArcOrchestrator(target_repo=target_repo, guidelines=project_guidelines, protected_paths=orch_protected_paths)
            if not issue_id: issue_id = input("Issue ID: ").strip()
            with open(session_file, "w") as f: json.dump({"repo": target_repo, "issue_id": issue_id}, f)
            
            plan_file = os.path.join(metadata_dir, f"PLAN_{issue_id}.md")
            issue_desc = ""
            if os.path.exists(plan_file):
                with open(plan_file, "r") as f: content = f.read()
                if "## Original Requirement\n" in content: issue_desc = content.split("## Original Requirement\n")[1].split("## Execution Tasks")[0].strip()
            else:
                issue_desc = input("Task Description: ").strip()
            if not issue_id or not issue_desc: sys.exit(1)
            await orch.process_issue(issue_id, issue_desc)
        except Exception as e: print(f"\n💥 ERROR: {str(e)}")
        finally:
            try:
                pending = asyncio.all_tasks()
                for t in pending:
                    if t is not asyncio.current_task(): t.cancel()
                loop = asyncio.get_event_loop()
                await loop.shutdown_asyncgens()
                print("✨ Done.")
            except Exception: pass
    asyncio.run(run())
