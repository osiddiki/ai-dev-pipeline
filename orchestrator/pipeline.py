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

# --- UI CONSTANTS ---
C_BLUE = "\033[94m"
C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_RED = "\033[91m"
C_BOLD = "\033[1m"
C_END = "\033[0m"

class GateUI:
    """Helper for beautiful, standardized CLI output."""
    @staticmethod
    def header(text: str):
        print(f"\n{C_BOLD}{'='*60}\n{text}\n{'='*60}{C_END}")
    
    @staticmethod
    def step(icon: str, text: str, color: str = C_BLUE):
        print(f"{color}{icon} {text}{C_END}")
    
    @staticmethod
    def success(text: str):
        print(f"{C_GREEN}✅ {text}{C_END}")
    
    @staticmethod
    def warning(text: str):
        print(f"{C_YELLOW}⚠️  {text}{C_END}")
    
    @staticmethod
    def error(text: str):
        print(f"{C_RED}❌ {text}{C_END}")

    @staticmethod
    def gate_result(gate_name: str, approved: bool, critique: str = ""):
        status = f"{C_GREEN}APPROVED{C_END}" if approved else f"{C_RED}REJECTED{C_END}"
        print(f"   🛡️  {C_BOLD}{gate_name.upper()}:{C_END} {status}")
        if not approved and critique:
            wrapped = "\n      ".join(critique.split("\n")[:3])
            print(f"      {C_YELLOW}{wrapped}...{C_END}")

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
        sandbox = DockerSandbox(self.target_repo)
        structure_out, _ = await asyncio.to_thread(sandbox.execute_command, "find . -maxdepth 2 -not -path '*/.*' 2>/dev/null || ls -F")
        
        index_path = f"{self.metadata_dir}/repo_index.txt"
        repo_map_out = ""
        if os.path.exists(index_path):
            with open(index_path, "r") as f: repo_map_out = f.read()

        doc_files = ["AGENTS.md", "sevicare-app/AGENTS.md", "README.md", "CONTRIBUTING.md", "sevicare-app/README.md"]
        docs_content = []
        for doc in doc_files:
            content = sandbox.read_file(doc)
            if "[File does not exist]" not in content and len(content.strip()) > 0:
                docs_content.append(f"--- FILE: {doc} ---\n{content[:1500]}")
        
        rulings_local_path = f"{self.metadata_dir}/RULINGS.md"
        if os.path.exists(rulings_local_path):
            with open(rulings_local_path, "r") as f:
                rulings_content = f.read()
                if rulings_content.strip(): docs_content.append(f"\n--- HUMAN RULINGS ---\n{rulings_content}")

        return f"STRUCTURE:\n{structure_out}\n\nSYMBOL MAP:\n{repo_map_out}\n\nDOCS:\n" + "\n".join(docs_content)

    def apply_patches(self, current_content: str, response: str) -> str:
        # 1. ATOMIC REWRITE DETECTION
        if "<<<< SEARCH" not in response and "```" in response:
            parts = response.split("```")
            if len(parts) >= 3:
                inner = parts[1]
                return inner.split("\n", 1)[1].strip() if "\n" in inner else inner.strip()

        # 2. SURGICAL PATCHING
        new_content = current_content
        lines = response.splitlines()
        in_search, in_replace = False, False
        search_lines, replace_lines = [], []
        applied_any = False
        
        for line in lines:
            stripped = line.strip()
            if "SEARCH" in stripped and ("<" in stripped or ":" in stripped):
                in_search, search_lines = True, []
                continue
            elif in_search and ("====" in stripped or "----" in stripped):
                in_search, in_replace, replace_lines = False, True, []
                continue
            elif in_replace and "REPLACE" in stripped and (">" in stripped or ":" in stripped):
                in_replace = False
                s_text, r_text = "\n".join(search_lines).strip(), "\n".join(replace_lines).strip()
                if not s_text: new_content = (new_content + "\n" + r_text) if applied_any else r_text
                elif s_text in new_content: new_content = new_content.replace(s_text, r_text)
                else:
                    norm_search = re.sub(r"\s+", "", s_text)
                    if norm_search:
                        pattern_str = r"\s*".join(re.escape(c) for c in s_text if not c.isspace())
                        match = re.search(pattern_str, new_content, re.DOTALL)
                        if match:
                            span = match.span()
                            new_content = new_content[:span[0]] + r_text + new_content[span[1]:]
                applied_any = True
                continue
            if in_search: search_lines.append(line)
            elif in_replace: replace_lines.append(line)

        if not applied_any:
            return re.sub(r"<<<< SEARCH\s*|={4,}\s*|>>>> REPLACE\s*", "", response).strip()
        return new_content

    async def process_issue(self, issue_id: str, issue_description: str, manual_plan: Optional[SupervisorPlan] = None) -> bool:
        db = await get_db()
        arc_id, all_diffs, task_memory = None, [], ""
        
        try:
            GateUI.header(f"🚀 GATE MISSION: {issue_id}")
            
            # 1. ARC INITIALIZATION
            async with db.execute("SELECT id FROM release_arcs WHERE issue_id = ? AND status != 'completed' ORDER BY id DESC LIMIT 1", (issue_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    arc_id = row[0]
                    GateUI.step("🔄", f"Resuming mission from trust ledger.")
                else:
                    GateUI.step("🎯", f"Initializing new mission context.")
                    cursor = await db.execute("INSERT INTO release_arcs (issue_id, repository, status) VALUES (?, ?, 'planning')", (issue_id, self.target_repo))
                    arc_id = cursor.lastrowid
                await db.commit()

            # 2. HARDEN GIT REPO & IDENTITY
            git_init_cmd = (
                "git init && "
                "git config --global --add safe.directory /workspace && "
                "git config --global user.email 'gate@sevisolutions.com' && "
                "git config --global user.name 'Gatekeeper' && "
                "git commit --allow-empty -m 'GATE Initial State' || true"
            )
            await asyncio.to_thread(DockerSandbox(self.target_repo).execute_command, git_init_cmd)

            # 3. RESEARCH
            GateUI.step("🔍", "Gathering intelligence...")
            repo_context = await self.gather_context()
            print(f"   📍 Symbols mapped. {len(repo_context)} bytes of context ingested.")
            
            GateUI.step("🧠", "Studying codebase architecture...")
            research_result = await self.researcher.invoke({"repo_path": self.target_repo, "repo_context": repo_context}, issue_description)
            discovery_report = research_result.output
            
            # 4. META-ANALYSIS
            GateUI.step("🔬", "Reviewing historical failure modes...")
            meta_result = await self.meta_analyzer.invoke({}, db)
            if meta_result.success and meta_result.output:
                GateUI.warning(f"META-WARNING INJECTED: {meta_result.output}")
                self.guidelines += f"\n\nCRITICAL META-WARNING:\n{meta_result.output}\n"
            
            context = {"guidelines": self.guidelines, "repo_path": self.target_repo, "repo_context": repo_context, "discovery_report": discovery_report}
            
            # 5. PLANNING
            plan = manual_plan
            plan_file = f"{self.metadata_dir}/PLAN_{issue_id}.md"
            if not plan and os.path.exists(plan_file):
                GateUI.step("📄", "Loading approved blueprint.")
                with open(plan_file, "r") as f: content = f.read()
                try:
                    json_str = content.split("```json")[1].split("```")[0].strip()
                    plan = SupervisorPlan(tasks=[TaskDefinition(**t) for t in json.loads(json_str)])
                except: plan = None
            
            if not plan:
                GateUI.step("🏗️", "Drafting blueprint...")
                plan_result = await self.supervisor.invoke(context, issue_description)
                plan = plan_result.output
                for attempt in range(3):
                    GateUI.step("🛡️", f"Reviewing blueprint (Attempt {attempt+1})...")
                    gate = await self.gatekeeper.review_plan(issue_description, plan)
                    GateUI.gate_result("PLAN_GATE", gate.approved, gate.critique)
                    if gate.approved: break
                    revision = await self.supervisor.revise_plan(plan, gate.critique, context)
                    plan = revision.output
                
                with open(plan_file, "w") as f:
                    f.write(f"# Blueprint: {issue_id}\n\n```json\n{json.dumps([t.model_dump() for t in plan.tasks], indent=2)}\n```\n")
                GateUI.warning(f"PLANNING PAUSE: Review blueprint at {plan_file}")
                return True

            # 6. EXECUTION
            task_dict = {t.id: t for t in plan.tasks}
            resolved = []
            visited, visiting = set(), set()
            def visit(tid):
                if tid in visited: return True
                if tid in visiting: return False
                visiting.add(tid)
                task = task_dict.get(tid)
                if task:
                    for d in task.dependencies:
                        if not visit(d): return False
                    resolved.append(task)
                visiting.remove(tid); visited.add(tid); return True
            for t in plan.tasks:
                if not visit(t.id): return False
            
            GateUI.step("🚦", f"Execution queue: {[t.id for t in resolved]}")

            for i, task in enumerate(resolved):
                GateUI.header(f"⚡ TASK {i+1}/{len(resolved)}: {task.id}")
                async with db.execute("SELECT status FROM tasks WHERE arc_id = ? AND task_id = ? AND status = 'completed'", (arc_id, task.id)) as cursor:
                    if await cursor.fetchone():
                        GateUI.success(f"Skipping completed task: {task.id}")
                        continue

                attempts, success, feedback = 3, False, ""
                while attempts > 0:
                    attempts -= 1
                    rollback = "git config --global --add safe.directory /workspace || true; if git rev-parse --is-inside-work-tree > /dev/null 2>&1; then git reset --hard HEAD && git clean -fdx; fi"
                    await asyncio.to_thread(DockerSandbox(self.target_repo).execute_command, rollback)

                    if attempts == 0 and not success:
                        GateUI.error(f"STRIKE TWO: Stuck on {task.id}")
                        hint = input(f"{C_YELLOW}Strategic hint ('skip' to bypass): {C_END}").strip()
                        if hint.lower() == 'skip': success = True; break
                        elif hint: task.description += f"\n\nHINT: {hint}"

                    GateUI.step("🔨", f"Working... (Attempts left: {attempts+1})")
                    desc = task.description + (f"\n\nFEEDBACK:\n{feedback}" if feedback else "")
                    
                    # Context Trimming
                    pkg_content = ""
                    if os.path.exists(f"{self.target_repo}/test-data-generator/package.json"):
                        with open(f"{self.target_repo}/test-data-generator/package.json", "r") as f: pkg_content = f.read()
                    
                    # Gate 2: Design
                    design_req = f"Task: {task.id}\n{task.description}\n\npkg:\n{pkg_content}\nProvide technical design."
                    design_proposal, _ = await LLMClient.chat(model_id=self.config.executor_model, messages=[{"role": "user", "content": design_req}])
                    d_gate = await self.gatekeeper.review_design(task, design_proposal)
                    GateUI.gate_result("DESIGN", d_gate.approved, d_gate.critique)
                    if not d_gate.approved:
                        feedback = f"Design Rejected: {d_gate.critique}"; continue
                    
                    context["approved_design"] = design_proposal
                    worker_res = await self.worker.invoke(context, task, temperature=0.3 + (2-attempts)*0.2)
                    w_out: WorkerResult = worker_res.output
                    
                    if not task.target_file: task_memory += f"\n--- {task.id} ---\n{w_out.diff}\n"
                    context["task_memory"] = task_memory
                    
                    # Gate 3: Code Review
                    cr_gate = await self.gatekeeper.codereview(task, w_out)
                    GateUI.gate_result("CODE", cr_gate.approved, cr_gate.critique)
                    
                    if cr_gate.approved:
                        if not task.target_file:
                            GateUI.success(f"Analysis {task.id} finished.")
                            await db.execute("INSERT INTO tasks (arc_id, task_id, status) VALUES (?, ?, 'completed')", (arc_id, task.id))
                            await db.commit(); success = True; break
                        
                        filepath = f"{self.target_repo}/{task.target_file}"
                        os.makedirs(os.path.dirname(filepath), exist_ok=True)
                        existing = ""
                        if os.path.exists(filepath):
                            with open(filepath, "r") as f: existing = f.read()
                        
                        try:
                            updated = self.apply_patches(existing, w_out.diff)
                            tmp_host = os.path.join(os.path.dirname(filepath), f".tmp.{os.path.basename(filepath)}")
                            with open(tmp_host, "w") as f: f.write(updated)
                            
                            # Linter
                            ext = os.path.splitext(task.target_file)[1]
                            tmp_rel = os.path.join(os.path.dirname(task.target_file), f".tmp.{os.path.basename(filepath)}")
                            lint_cmd = f"npx tsc --noEmit {tmp_rel}" if ext == ".ts" else None
                            if lint_cmd:
                                GateUI.step("🔍", "Linter pre-flight...")
                                _, code = await asyncio.to_thread(DockerSandbox(self.target_repo).execute_command, lint_cmd)
                                if code != 0: raise ValueError("Linter failed.")

                            os.rename(tmp_host, filepath)
                            DockerSandbox(self.target_repo).write_file(task.target_file, updated)
                            
                            ver = await VerifierEngine(sandbox=DockerSandbox(self.target_repo)).verify(task.description, repo_context, [task.target_file])
                            if not ver.success:
                                GateUI.error(f"Reality Check failed: {ver.reason}")
                                feedback = f"Empirical Failure: {ver.reason}"; continue
                            
                            GateUI.success(f"Task {task.id} finalized.")
                            all_diffs.append(w_out)
                            await db.execute("INSERT INTO tasks (arc_id, task_id, status) VALUES (?, ?, 'completed')", (arc_id, task.id))
                            await db.commit()
                            checkpoint = f"git add . && git commit -m 'GATE: {task.id}'"
                            await asyncio.to_thread(DockerSandbox(self.target_repo).execute_command, checkpoint)
                            success = True; break
                        except Exception as e:
                            feedback = f"Execution error: {str(e)}"; continue
                    else: feedback = cr_gate.critique
                
                if not success: return False
                    
            GateUI.header("🏁 MISSION COMPLETE")
            return True
            
        except Exception as e:
            GateUI.error(f"SYSTEM CRASH: {str(e)}")
            traceback.print_exc(); return False

    async def _log_gate(self, db, arc_id, task_id, gate_name, approved, critique, error_type=None, attempt=1, verification_method=None, metrics=None):
        try:
            await db.execute(
                "INSERT INTO gate_reviews (arc_id, task_id, gate_name, model_id, status, error_type, critique_summary, attempt_number) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (arc_id, task_id, gate_name, self.gatekeeper.model_id, "approved" if approved else "rejected", error_type, critique, attempt)
            )
            await db.commit()
        except: pass

if __name__ == "__main__":
    async def run():
        try:
            session_file = "metadata/LAST_SESSION.json"
            target_repo, issue_id = None, None
            if os.path.exists(session_file):
                with open(session_file, "r") as f: last = json.load(f)
                if input(f"{C_YELLOW}🔄 Resume {last.get('issue_id')}? [Y/n]: {C_END}").lower() != 'n':
                    target_repo, issue_id = last.get('repo'), last.get('issue_id')
            
            if not target_repo: target_repo = input(f"Target Repo: ").strip() or "."
            project_name = os.path.basename(target_repo.rstrip("/"))
            metadata_dir = f"metadata/{project_name}"
            os.makedirs(metadata_dir, exist_ok=True)
            
            config_path = os.path.join(metadata_dir, "gate.yml")
            project_guidelines = "Standard practices."
            if os.path.exists(config_path):
                with open(config_path, "r") as f: cfg = yaml.safe_load(f)
                project_guidelines = f"GOAL: {cfg.get('project_goal', '')}\nSTACK: {cfg.get('technical_stack', '')}\nARCH: {cfg.get('architecture', '')}\nRULES: {cfg.get('guidelines', '')}"
            
            orch = ReleaseArcOrchestrator(target_repo=target_repo, guidelines=project_guidelines)
            if not issue_id: issue_id = input("Issue ID: ").strip()
            with open(session_file, "w") as f: json.dump({"repo": target_repo, "issue_id": issue_id}, f)
            
            plan_file = os.path.join(metadata_dir, f"PLAN_{issue_id}.md")
            desc = ""
            if os.path.exists(plan_file):
                with open(plan_file, "r") as f: content = f.read()
                if "# Original Requirement\n" in content: desc = content.split("# Original Requirement\n")[1].split("## Execution Tasks")[0].strip()
            if not desc: desc = input("Task Description: ").strip()
            await orch.process_issue(issue_id, desc)
        except Exception as e: GateUI.error(f"FATAL ERROR: {str(e)}")
        finally:
            print(f"{C_GREEN}✨ Shutdown complete.{C_END}")
    asyncio.run(run())
