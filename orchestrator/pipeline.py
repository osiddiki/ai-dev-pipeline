import asyncio
import structlog
import json
import sys
import os
import re
from typing import Any, Optional, List
from agents.supervisor import SupervisorAgent, SupervisorPlan, TaskDefinition
from agents.worker import WorkerAgent, WorkerResult
from agents.gatekeeper import GatekeeperAgent
from agents.researcher import ResearcherAgent
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
3. DOCUMENTATION: Ensure all new components or changes are documented in ONBOARDING.md.
4. TESTING: Always consider how changes will be tested across the federated system.
5. SECURITY: Maintain strict HIPAA compliance protocols.
6. SERIALIZATION: Tasks MUST be strictly sequential.
"""

class ReleaseArcOrchestrator:
    """Manages the full GATE pipeline execution (The Release Arc)."""
    
    def __init__(
        self, 
        target_repo: str, 
        guidelines: Optional[str] = None,
        supervisor_model: str = "gemini/gemini-2.5-pro",
        worker_model: str = "deepseek/deepseek-chat",
        gatekeeper_model: str = "gemini/gemini-3.1-pro-preview"
    ):
        self.target_repo = target_repo
        self.guidelines = guidelines or "Follow professional best practices."
        self.supervisor = SupervisorAgent(model_id=supervisor_model)
        self.worker = WorkerAgent(model_id=worker_model)
        self.gatekeeper = GatekeeperAgent(model_id=gatekeeper_model)
        self.researcher = ResearcherAgent(model_id=supervisor_model)
        
    async def gather_context(self) -> str:
        """Gather structural, textual, and symbolic context from the repository."""
        logger.info("Gathering repository context", repo=self.target_repo)
        sandbox = DockerSandbox(self.target_repo)
        structure = sandbox.execute_command("find . -maxdepth 2 -not -path '*/.*' 2>/dev/null || ls -F")
        with open("agents/repo_map.py", "r") as f:
            script_content = f.read()
        sandbox.write_file("repo_map_tool.py", script_content)
        repo_map = sandbox.execute_command("python3 repo_map_tool.py .")
        doc_files = ["AGENTS.md", "sevicare-app/AGENTS.md", "README.md", "CONTRIBUTING.md", "sevicare-app/README.md"]
        docs_content = []
        for doc in doc_files:
            is_file = sandbox.execute_command(f"[ -f {doc} ] && echo 'yes' || echo 'no'").strip()
            if is_file == "yes":
                content = sandbox.read_file(doc)
                if "Error" not in content and len(content.strip()) > 0:
                    docs_content.append(f"--- FILE: {doc} ---\n{content[:2000]}")
        repowise_check = sandbox.execute_command("find . -name '*repowise*' -type f -maxdepth 4 2>/dev/null")
        if repowise_check.strip():
            for rw_file in repowise_check.strip().split("\n")[:3]:
                rw_content = sandbox.read_file(rw_file)
                if "Error" not in rw_content:
                    docs_content.append(f"--- REPOWISE: {rw_file} ---\n{rw_content[:3000]}")
        full_context = f"REPOSITORY STRUCTURE:\n{structure}\n\nSYMBOL MAP:\n{repo_map}\n\nCRITICAL DOCUMENTATION:\n" + "\n".join(docs_content)
        return full_context

    def apply_patches(self, current_content: str, response: str) -> str:
        """Parse and apply SEARCH/REPLACE blocks to the current content."""
        # Pattern to match SEARCH/REPLACE blocks
        pattern = re.compile(r"<<<< SEARCH\n(.*?)\n====\n(.*?)\n>>>> REPLACE", re.DOTALL)
        blocks = pattern.findall(response)
        if not blocks:
            # Fallback: if no blocks found, maybe it's analysis or a full-file block
            if "```" in response:
                parts = response.split("```")
                if len(parts) >= 3:
                    inner = parts[1]
                    return inner.split("\n", 1)[1].strip() if "\n" in inner else inner.strip()
            return response
            
        new_content = current_content
        for search, replace in blocks:
            if not search.strip() and not current_content.strip():
                # New file case: Empty search, empty current content
                new_content = replace
            else:
                # Surgical edit case
                if search in new_content:
                    new_content = new_content.replace(search, replace)
                else:
                    logger.error("SEARCH block not found in file content", search_preview=search[:100])
                    raise ValueError(f"Surgical patch failed: Could not find exact match for SEARCH block in the file.")
        return new_content

    async def process_issue(self, issue_id: str, issue_description: str, manual_plan: Optional[SupervisorPlan] = None) -> bool:
        db = await get_db()
        repo_context = None
        discovery_report = None
        async with db.execute(
            "SELECT id, status, repo_context, discovery_report FROM release_arcs WHERE issue_id = ? AND status != 'completed' ORDER BY id DESC LIMIT 1", 
            (issue_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                arc_id, _, repo_context, discovery_report = row
                logger.info("Resuming existing Release Arc", arc_id=arc_id, issue_id=issue_id)
                await db.execute("UPDATE release_arcs SET status = 'in_progress', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (arc_id,))
            else:
                logger.info("Starting New Release Arc", issue_id=issue_id, repo=self.target_repo)
                cursor = await db.execute("INSERT INTO release_arcs (issue_id, repository, status) VALUES (?, ?, 'planning')", (issue_id, self.target_repo, 'planning'))
                arc_id = cursor.lastrowid
            await db.commit()

        repo_context = await self.gather_context()
        await db.execute("UPDATE release_arcs SET repo_context = ? WHERE id = ?", (repo_context, arc_id))
        await db.commit()

        force_research = False
        async with db.execute("SELECT error_type FROM gate_reviews WHERE arc_id = ? ORDER BY id DESC LIMIT 1", (arc_id,)) as cursor:
            last_error = await cursor.fetchone()
            if last_error and last_error[0] == 'incoherent':
                logger.info("Forcing research refresh due to previous incoherence")
                force_research = True

        if not discovery_report or force_research:
            research_result = await self.researcher.invoke({"repo_path": self.target_repo, "repo_context": repo_context}, issue_description)
            discovery_report = research_result.output
            await db.execute("UPDATE release_arcs SET discovery_report = ? WHERE id = ?", (discovery_report, arc_id))
            await db.commit()
        else:
            logger.info("Using cached Technical Discovery Report")
        
        context = {"guidelines": self.guidelines, "repo_path": self.target_repo, "repo_context": repo_context, "discovery_report": discovery_report}
        
        plan = None
        if manual_plan:
            logger.info("Using Manual Override Plan.")
            plan = manual_plan
        else:
            plan_result = await self.supervisor.invoke(context, issue_description)
            if not plan_result.success: return False
            plan = plan_result.output
            max_revisions = 3
            for attempt in range(max_revisions + 1):
                plan_gate = await self.gatekeeper.review_plan(issue_description, plan)
                await self._log_gate(db, arc_id, None, "review_plan", plan_gate.approved, plan_gate.critique, error_type=plan_gate.error_type, attempt=attempt+1)
                if plan_gate.approved:
                    logger.info("Plan approved by Gatekeeper", attempt=attempt+1)
                    break
                if attempt < max_revisions:
                    logger.warning("Plan rejected. Requesting autonomous revision.", attempt=attempt+1)
                    revision_result = await self.supervisor.revise_plan(plan, plan_gate.critique, context)
                    if not revision_result.success: return False
                    plan = revision_result.output
                else: return False

        all_diffs = []
        for task in plan.tasks:
            async with db.execute("SELECT status FROM tasks WHERE arc_id = ? AND description = ? AND status = 'completed'", (arc_id, task.description)) as cursor:
                if await cursor.fetchone():
                    logger.info("Skipping already completed task", task_id=task.id)
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
                logger.info("Executing Task", task_id=task.id, attempt=task_attempt)
                mission_desc = current_task_desc
                if task_attempt > 1:
                    logger.warning("Retrying task with feedback", task_id=task.id)
                    mission_desc += f"\n\nPREVIOUS FAILURE FEEDBACK:\n{last_error_feedback}"
                temp_task = TaskDefinition(id=task.id, description=mission_desc, target_file=task.target_file)
                design_gate = await self.gatekeeper.review_design(temp_task, f"Design for {temp_task.id}")
                if not design_gate.approved: continue
                worker_res = await self.worker.invoke(context, temp_task)
                w_output: WorkerResult = worker_res.output
                cr_gate = await self.gatekeeper.codereview(temp_task, w_output)
                
                verification_output = ""
                is_test_task = any(word in temp_task.description.lower().split() for word in ["test", "tests"]) or "run test" in temp_task.description.lower()
                is_check_task = any(word in temp_task.description.lower().split() for word in ["verify", "check"])
                if cr_gate.approved and (is_test_task or is_check_task):
                    logger.info("Running empirical verification", task_id=temp_task.id)
                    sandbox = DockerSandbox(self.target_repo)
                    if is_test_task:
                        has_pkg = "yes" in sandbox.execute_command("[ -f test-data-generator/package.json ] && echo 'yes' || echo 'no'")
                        has_test_script = "yes" in sandbox.execute_command("grep '\"test\":' test-data-generator/package.json && echo 'yes' || echo 'no'") if has_pkg else False
                        if not has_pkg or not has_test_script:
                            logger.warning("Skipping real-world test: Project foundation not ready.")
                            verification_output = "SKIPPED: package.json or test script missing"
                        else:
                            has_pnpm = "yes" in sandbox.execute_command("command -v pnpm && echo 'yes' || echo 'no'")
                            cmd = f"cd test-data-generator && {'pnpm test' if has_pnpm else 'npm test'}"
                            verification_output = sandbox.execute_command(cmd)
                    else:
                        cmd = f"ls {temp_task.target_file}" if temp_task.target_file else "ls -R"
                        verification_output = sandbox.execute_command(cmd)
                    if "fail" in verification_output.lower() or "error" in verification_output.lower():
                        cr_gate.approved = False
                        cr_gate.error_type = "systematic"
                        cr_gate.critique += f"\n\nEMPIRICAL FAILURE:\n{verification_output}"

                await self._log_gate(db, arc_id, task.id, "codereview", cr_gate.approved, cr_gate.critique, error_type=cr_gate.error_type, attempt=task_attempt)
                
                if cr_gate.approved:
                    all_diffs.append(w_output)
                    if task.target_file:
                        PROTECTED_PATHS = ["provider-portal-app", "sevicare-app", "admin-portal", "vendor-portal"]
                        if any(p in task.target_file for p in PROTECTED_PATHS):
                            logger.warning("SOURCE GUARD BLOCKED WRITE", path=task.target_file)
                        else:
                            filepath = f"{self.target_repo}/{task.target_file}"
                            logger.info("Applying surgical patches", file=filepath)
                            os.makedirs(os.path.dirname(filepath), exist_ok=True)
                            existing_content = ""
                            if os.path.exists(filepath):
                                with open(filepath, "r") as f: existing_content = f.read()
                            try:
                                updated_content = self.apply_patches(existing_content, w_output.diff)
                                with open(filepath, "w") as f: f.write(updated_content)
                            except Exception as e:
                                logger.error("Patching failed", error=str(e))
                                last_error_feedback = str(e)
                                cr_gate.approved = False
                                continue
                    await db.execute("UPDATE tasks SET status = 'completed', updated_at = CURRENT_TIMESTAMP WHERE arc_id = ? AND description = ?", (arc_id, task.description))
                    await db.commit()
                    task_success = True
                    break
                else:
                    last_error_feedback = cr_gate.critique
                    logger.error("Task attempt failed", task_id=task.id, attempt=task_attempt, error_type=cr_gate.error_type)
                    if task_attempt == max_task_attempts:
                        print("\n" + "!"*60 + f"\n🚨 SELF-HEALING FAILED for {task.id}\nCRITIQUE: {last_error_feedback[:300]}\n" + "!"*60)
                        user_hint = input("\nThe AI is stuck. Provide a hint to fix this, or type 'skip': ").strip()
                        if user_hint.lower() == 'skip':
                            task_success = True
                            break
                        elif user_hint:
                            logger.info("Retrying with human guidance...")
                            current_task_desc += f"\n\nUSER GUIDANCE: {user_hint}"
                            max_task_attempts += 1 
            if not task_success: return False
                
        final_gate = await self.gatekeeper.review_code(issue_description, plan, all_diffs)
        await self._log_gate(db, arc_id, None, "review_code", final_gate.approved, final_gate.critique, error_type=final_gate.error_type)
        status = "completed" if final_gate.approved else "failed"
        await db.execute("UPDATE release_arcs SET status = ? WHERE id = ?", (status, arc_id))
        await db.commit()
        logger.info("Release Arc finished", status=status)
        return final_gate.approved

    async def _log_gate(self, db, arc_id, task_id, gate_name, approved, critique, error_type=None, attempt=1):
        status = "approved" if approved else "rejected"
        await db.execute(
            """INSERT INTO gate_reviews (arc_id, task_id, gate_name, model_id, status, error_type, critique_summary, attempt_number)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (arc_id, task_id, gate_name, self.gatekeeper.model_id, status, error_type, critique, attempt)
        )
        await db.commit()

if __name__ == "__main__":
    async def run():
        orch = ReleaseArcOrchestrator(target_repo="/Users/omarsiddiki/sevisolutions", guidelines=SEVI_GUIDELINES)
        print("\n" + "="*50 + "\n🚀 GATE PIPELINE: SEVI TEST DATA GENERATOR\n" + "="*50)
        issue_id = input("\nEnter Issue ID [GEN-001]: ").strip() or "GEN-001"
        print("\nDescribe the task (Press Enter for Phase 1 default):")
        default_task = "Analyze provider-portal-app/e2e/patient-data.ts and create a standalone types.ts file in a new directory test-data-generator/src/ that includes all necessary interfaces for a Unified Patient Model. Ensure the directory is initialized with a basic package.json."
        print(f"[Default]: {default_task}")
        issue_desc = input("\nTask Description: ").strip() or default_task
        await orch.process_issue(issue_id, issue_desc)
    asyncio.run(run())
