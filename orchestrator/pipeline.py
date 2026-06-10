import asyncio
import structlog
import json
import sys
import os
import re
import traceback
from typing import Any, Optional, List
from agents.supervisor import SupervisorAgent, SupervisorPlan, TaskDefinition
from agents.worker import WorkerAgent, WorkerResult
from agents.gatekeeper import GatekeeperAgent
from agents.researcher import ResearcherAgent
from agents.models import GateConfig
from orchestrator.verifier import VerifierEngine
from ledger.database import get_db
from environment.sandbox import DockerSandbox

logger = structlog.get_logger()

# PROJECT DIRECTIVE: Sevi Test Data Generator
DATA_GEN_DIRECTIVE = """
PROJECT GOAL:
Develop a standalone TypeScript CLI utility called test-data-generator to generate medically realistic patient datasets.

TECHNICAL STACK:
- Language: TypeScript (Node.js)
- Source of Truth: provider-portal-app/e2e/patient-data.ts
- Dependencies: faker, date-fns

ARCHITECTURE (Unified Model Pattern):
1. Generator (UPM): Creates a 'Unified Patient Model' in memory.
2. Clinical Profiles: Use profiles (e.g., 'Type 2 Diabetic') for medical coherence (ICD-10, Medications).
3. Exporters: Translate UPM to JSON, FHIR, and HL7.

CONSTRAINTS:
- Medical Integrity: Logically consistent dates (DOB before onset) and gender-matched diagnoses.
- EHR Flavors: Support --flavor flag (epic, cerner).
- ID Consistency: Matching IDs across JSON, HL7, and FHIR outputs in a batch.
"""

SEVI_GUIDELINES = f"""
{DATA_GEN_DIRECTIVE}

GENERAL SEVI STANDARDS:
1. ARCHITECTURE: Respect the Monorepo structure and Federation patterns.
2. INFRASTRUCTURE: When starting a NEW standalone utility, ensure the plan includes necessary project initialization (package.json, tsconfig.json) to make the code executable, but keep implementation tasks strictly scoped to the user's request.
3. STANDARDS: Follow the established coding standards for the 21 microservices.
4. DOCUMENTATION: Ensure all new components or changes are documented in ONBOARDING.md.
5. TESTING: Always consider how changes will be tested across the federated system.
6. SECURITY: Maintain strict HIPAA compliance protocols.
7. SERIALIZATION: Tasks MUST be strictly sequential.
"""

class ReleaseArcOrchestrator:
    """Manages the full GATE pipeline execution (The Release Arc)."""
    
    def __init__(
        self, 
        target_repo: str, 
        guidelines: Optional[str] = None,
        config: Optional[GateConfig] = None
    ):
        self.target_repo = target_repo
        # Centralized metadata storage
        self.project_name = os.path.basename(self.target_repo.rstrip("/"))
        self.metadata_dir = f"metadata/{self.project_name}"
        os.makedirs(self.metadata_dir, exist_ok=True)
        
        self.guidelines = guidelines or "Follow professional best practices."
        self.config = config or GateConfig()
        
        self.supervisor = SupervisorAgent(model_id=self.config.planner_model)
        self.worker = WorkerAgent(model_id=self.config.executor_model)
        self.gatekeeper = GatekeeperAgent(model_id=self.config.verifier_model)
        self.researcher = ResearcherAgent(model_id=self.config.planner_model)
        
    async def gather_context(self) -> str:
        """Gather structural, textual, and symbolic context from the repository."""
        sandbox = DockerSandbox(self.target_repo)
        structure_out, _ = await asyncio.to_thread(sandbox.execute_command, "find . -maxdepth 2 -not -path '*/.*' 2>/dev/null || ls -F")
        with open("agents/repo_map.py", "r") as f:
            script_content = f.read()
        sandbox.write_file("repo_map_tool.py", script_content)
        repo_map_out, _ = await asyncio.to_thread(sandbox.execute_command, "python3 repo_map_tool.py .")
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
        """Parse and apply SEARCH/REPLACE blocks with flexible separator support."""
        pattern = re.compile(r"<<<< SEARCH\n(.*?)\n={4,10}\n(.*?)\n>>>> REPLACE", re.DOTALL)
        blocks = pattern.findall(response)
        if not blocks:
            if "```" in response:
                parts = response.split("```")
                if len(parts) >= 3:
                    inner = parts[1]
                    return inner.split("\n", 1)[1].strip() if "\n" in inner else inner.strip()
            return response
            
        new_content = current_content
        for search, replace in blocks:
            if not search.strip() and not current_content.strip():
                new_content = replace
            else:
                if search in new_content:
                    new_content = new_content.replace(search, replace)
                else:
                    logger.error("SEARCH block match failed", search_preview=search[:50])
                    raise ValueError(f"Surgical patch failed: Could not find exact match for SEARCH block in the file.")
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
                    cursor = await db.execute("INSERT INTO release_arcs (issue_id, repository, status) VALUES (?, ?, 'planning')", (issue_id, self.target_repo, 'planning'))
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
                    
                    # GIT ROLLBACK: Ensure pristine state for each attempt
                    print(f"🧹 Resetting workspace to pristine state...")
                    await asyncio.to_thread(DockerSandbox(self.target_repo).execute_command, "git reset --hard HEAD && git clean -fd")

                    # Strike-Two Breakout: Ask human for hint after 2 fails
                    if task_attempt == 3 and not task_success:
                        print("\n" + "!"*60 + f"\n🚨 STRIKE TWO: The AI is stuck on {task.id}\nCRITIQUE: {last_error_feedback[:300]}\n" + "!"*60)
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
                    
                    temp_task = TaskDefinition(id=task.id, description=mission_desc, target_file=task.target_file, dependencies=task.dependencies)
                    design_gate = await self.gatekeeper.review_design(temp_task, f"Design for {temp_task.id}")
                    if not design_gate.approved: 
                        print(f"⚠️  Design check failed for {task.id}. Self-healing...")
                        continue
                    
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
                            PROTECTED_PATHS = ["provider-portal-app", "sevicare-app", "admin-portal", "vendor-portal"]
                            if any(p in task.target_file for p in PROTECTED_PATHS):
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
        except Exception:
             await db.execute(
                """INSERT INTO gate_reviews (arc_id, task_id, gate_name, model_id, status, error_type, critique_summary, attempt_number)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (arc_id, task_id, gate_name, self.gatekeeper.model_id, status, error_type, critique, attempt)
            )
        await db.commit()

if __name__ == "__main__":
    async def run():
        try:
            print("\n" + "="*50 + "\n🚀 GATE PIPELINE: SEVI TEST DATA GENERATOR\n" + "="*50)
            default_repo = "/Users/omarsiddiki/sevisolutions"
            repo_input = input(f"\nEnter Target Repo Path [{default_repo}]: ").strip()
            target_repo = repo_input if repo_input else default_repo
            orch = ReleaseArcOrchestrator(target_repo=target_repo, guidelines=SEVI_GUIDELINES)
            issue_id = input("\nEnter Issue ID [GEN-001]: ").strip() or "GEN-001"
            print("\nDescribe the task (Press Enter for Phase 1 default):")
            default_task = "Analyze provider-portal-app/e2e/patient-data.ts and create a standalone types.ts file in a new directory test-data-generator/src/ that includes all necessary interfaces for a Unified Patient Model. Ensure the directory is initialized with a basic package.json."
            print(f"[Default]: {default_task}")
            issue_desc = input("\nTask Description: ").strip() or default_task
            await orch.process_issue(issue_id, issue_desc)
        except KeyboardInterrupt:
            print("\n👋 Shutdown requested by user.")
        except Exception as e:
            print(f"\n💥 STARTUP ERROR: {str(e)}")
        finally:
            pending = asyncio.all_tasks()
            for task in pending:
                if task is not asyncio.current_task(): task.cancel()
            try:
                await asyncio.wait_for(asyncio.gather(*[p for p in pending if p is not asyncio.current_task()], return_exceptions=True), timeout=2.0)
            except Exception: pass
            loop = asyncio.get_event_loop()
            await loop.shutdown_asyncgens()
            print("✨ Resources released.")
    asyncio.run(run())
