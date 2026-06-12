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
from agents.prompts import DESIGNER_PROMPT
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
    @staticmethod
    def header(text: str): print(f"\n{C_BOLD}{'='*60}\n{text}\n{'='*60}{C_END}")
    @staticmethod
    def step(icon: str, text: str, color: str = C_BLUE): print(f"{color}{icon} {text}{C_END}")
    @staticmethod
    def success(text: str): print(f"{C_GREEN}✅ {text}{C_END}")
    @staticmethod
    def warning(text: str): print(f"{C_YELLOW}⚠️  {text}{C_END}")
    @staticmethod
    def error(text: str): print(f"{C_RED}❌ {text}{C_END}")
    @staticmethod
    def gate_result(gate_name: str, approved: bool, critique: str = ""):
        status = f"{C_GREEN}APPROVED{C_END}" if approved else f"{C_RED}REJECTED{C_END}"
        print(f"   🛡️  {C_BOLD}{gate_name.upper()}:{C_END} {status}")
        if not approved and critique:
            wrapped = "\n      ".join(critique.split("\n")[:3])
            print(f"      {C_YELLOW}{wrapped}...{C_END}")

logger = structlog.get_logger()

class ReleaseArcOrchestrator:
    def __init__(self, target_repo: str, guidelines: Optional[str] = None, config: Optional[GateConfig] = None):
        self.target_repo = target_repo
        self.project_name = os.path.basename(self.target_repo.rstrip("/"))
        self.metadata_dir = f"metadata/{self.project_name}"
        os.makedirs(self.metadata_dir, exist_ok=True)
        self.guidelines = guidelines or "Follow professional best practices."
        self.config = config or GateConfig()
        self.supervisor = SupervisorAgent(model_id=self.config.planner_model)
        self.worker = WorkerAgent(model_id=self.config.executor_model)
        self.gatekeeper = GatekeeperAgent(model_id=self.config.verifier_model)
        self.researcher = ResearcherAgent(model_id=self.config.planner_model)
        self.meta_analyzer = MetaAnalyzerAgent(model_id=self.config.planner_model)
        
    async def gather_context(self) -> str:
        sandbox = DockerSandbox(self.target_repo)
        structure_out, _ = await asyncio.to_thread(sandbox.execute_command, "find . -maxdepth 2 -not -path '*/.*' 2>/dev/null || ls -F")
        doc_files = ["AGENTS.md", "README.md", "package.json"]
        docs = []
        for doc in doc_files:
            content = sandbox.read_file(doc)
            if "[File does not exist]" not in content: docs.append(f"--- {doc} ---\n{content[:1000]}")
        return f"STRUCTURE:\n{structure_out}\n\nDOCS:\n" + "\n".join(docs)

    def apply_patches(self, current_content: str, response: str) -> str:
        if "<<<< SEARCH" not in response and "```" in response:
            parts = response.split("```")
            if len(parts) >= 3:
                inner = parts[1]
                return inner.split("\n", 1)[1].strip() if "\n" in inner else inner.strip()
        new_content = current_content
        lines = response.splitlines()
        in_search, in_replace = False, False
        search_lines, replace_lines = [], []
        applied_any = False
        for line in lines:
            stripped = line.strip()
            if "SEARCH" in stripped and ("<" in stripped or ":" in stripped): in_search, search_lines = True, []
            elif in_search and ("====" in stripped or "----" in stripped): in_search, in_replace, replace_lines = False, True, []
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
                        if match: new_content = new_content[:match.span()[0]] + r_text + new_content[match.span()[1]:]
                applied_any = True
                continue
            if in_search: search_lines.append(line)
            elif in_replace: replace_lines.append(line)
        return new_content if applied_any else re.sub(r"<<<< SEARCH\s*|={4,}\s*|>>>> REPLACE\s*", "", response).strip()

    async def process_issue(self, issue_id: str, issue_description: str, manual_plan: Optional[SupervisorPlan] = None) -> bool:
        db = await get_db()
        arc_id, all_diffs, task_memory = None, [], ""
        try:
            GateUI.header(f"🚀 MISSION: {issue_id}")
            async with db.execute("SELECT id FROM release_arcs WHERE issue_id = ? AND status != 'completed' ORDER BY id DESC LIMIT 1", (issue_id,)) as cursor:
                row = await cursor.fetchone()
                if row: arc_id = row[0]; GateUI.step("🔄", "Resuming mission.")
                else:
                    cursor = await db.execute("INSERT INTO release_arcs (issue_id, repository, status) VALUES (?, ?, 'planning')", (issue_id, self.target_repo))
                    arc_id = cursor.lastrowid
                await db.commit()

            git_init_cmd = "git init && git config --global --add safe.directory /workspace && git config --global user.email 'gate@sevisolutions.com' && git config --global user.name 'Gatekeeper' && git commit --allow-empty -m 'GATE Init' || true"
            await asyncio.to_thread(DockerSandbox(self.target_repo).execute_command, git_init_cmd)

            repo_context = await self.gather_context()
            GateUI.step("🧠", "Studying architecture...")
            research_result = await self.researcher.invoke({"repo_path": self.target_repo, "repo_context": repo_context}, issue_description)
            discovery_report = research_result.output

            plan_file = f"{self.metadata_dir}/PLAN_{issue_id}.md"
            plan = manual_plan
            if not plan and os.path.exists(plan_file):
                with open(plan_file, "r") as f:
                    try:
                        json_str = f.read().split("```json")[1].split("```")[0].strip()
                        plan = SupervisorPlan(tasks=[TaskDefinition(**t) for t in json.loads(json_str)])
                    except: plan = None
            
            if not plan:
                GateUI.step("🏗️", "Drafting blueprint...")
                plan_result = await self.supervisor.invoke({"guidelines": self.guidelines, "repo_path": self.target_repo, "repo_context": repo_context, "discovery_report": discovery_report}, issue_description)
                plan = plan_result.output
                with open(plan_file, "w") as f: f.write(f"```json\n{json.dumps([t.model_dump() for t in plan.tasks], indent=2)}\n```\n")
                GateUI.warning(f"PLANNING PAUSE: {plan_file}")
                return True

            for i, task in enumerate(plan.tasks):
                GateUI.header(f"⚡ TASK {i+1}/{len(plan.tasks)}: {task.id}")
                async with db.execute("SELECT status FROM tasks WHERE arc_id = ? AND task_id = ? AND status = 'completed'", (arc_id, task.id)) as cursor:
                    if await cursor.fetchone(): GateUI.success("Skipping completed task."); continue

                attempts, success, feedback = 3, False, ""
                while attempts > 0:
                    attempts -= 1
                    sandbox = DockerSandbox(self.target_repo)
                    await asyncio.to_thread(sandbox.execute_command, "git reset --hard HEAD && git clean -fdx")
                    
                    if attempts == 0 and not success:
                        GateUI.error("STRIKE TWO")
                        hint = input(f"{C_YELLOW}Strategic hint: {C_END}").strip()
                        if hint.lower() == 'skip': success = True; break
                        elif hint: task.description += f"\n\nHINT: {hint}"

                    GateUI.step("🔨", f"Working... ({attempts+1} attempts left)")
                    
                    # Gate 2: DESIGN (Anchored Identity)
                    design_req = f"Task: {task.id}\n{task.description}\nConstraints: {task.design_constraints}\n{feedback}\nProvide TypeScript design."
                    design_messages = [{"role": "system", "content": DESIGNER_PROMPT}, {"role": "user", "content": design_req}]
                    design_proposal, _ = await LLMClient.chat(model_id=self.config.executor_model, messages=design_messages)
                    
                    d_gate = await self.gatekeeper.review_design(task, design_proposal)
                    GateUI.gate_result("DESIGN", d_gate.approved, d_gate.critique)
                    if not d_gate.approved: feedback = f"Design Rejected: {d_gate.critique}"; continue
                    
                    # EXECUTION
                    worker_res = await self.worker.invoke({"guidelines": self.guidelines, "repo_path": self.target_repo, "repo_context": repo_context, "discovery_report": discovery_report, "approved_design": design_proposal, "task_memory": task_memory}, task)
                    w_out = worker_res.output
                    
                    # Gate 3: CODE REVIEW
                    cr_gate = await self.gatekeeper.codereview(task, w_out)
                    GateUI.gate_result("CODE", cr_gate.approved, cr_gate.critique)
                    
                    if cr_gate.approved:
                        if not task.target_file: success = True; break
                        
                        filepath = f"{self.target_repo}/{task.target_file}"
                        os.makedirs(os.path.dirname(filepath), exist_ok=True)
                        existing = ""
                        if os.path.exists(filepath):
                            with open(filepath, "r") as f: existing = f.read()
                        
                        try:
                            updated = self.apply_patches(existing, w_out.diff)
                            
                            # HARDENING: Use .tmp.ts extension so tsc recognizes it
                            # Also place it in the same directory as the target
                            tmp_filename = f".tmp.{os.path.basename(task.target_file)}"
                            tmp_rel = os.path.join(os.path.dirname(task.target_file), tmp_filename)
                            sandbox.write_file(tmp_rel, updated)
                            
                            # Linter Pre-flight
                            if task.target_file.endswith(".ts"):
                                GateUI.step("🔍", f"Linter checking {tmp_filename}...")
                                # We point tsc to the local tsconfig if it exists
                                project_dir = task.target_file.split('/')[0]
                                lint_cmd = f"npx tsc --noEmit --skipLibCheck {tmp_rel}"
                                _, code = await asyncio.to_thread(sandbox.execute_command, lint_cmd)
                                if code != 0: 
                                    # Fallback: simple syntax check if tsc is amnesiac about types
                                    GateUI.warning("Full linter failed, falling back to syntax check.")
                                    syntax_cmd = f"node --check {tmp_rel}" if not task.target_file.endswith(".ts") else f"npx tsc {tmp_rel} --noEmit --target esnext --module commonjs --skipLibCheck"
                                    _, s_code = await asyncio.to_thread(sandbox.execute_command, syntax_cmd)
                                    if s_code != 0: raise ValueError("Code has syntax errors.")

                            # FINAL COMMIT
                            sandbox.write_file(task.target_file, updated)
                            with open(filepath, "w") as f: f.write(updated)
                            
                            ver = await VerifierEngine(sandbox=sandbox).verify(task.description, repo_context, [task.target_file])
                            if not ver.success: feedback = f"Reality Check: {ver.reason}"; continue
                            
                            GateUI.success("Finalized.")
                            await db.execute("INSERT INTO tasks (arc_id, task_id, status) VALUES (?, ?, 'completed')", (arc_id, task.id))
                            await db.commit()
                            await asyncio.to_thread(sandbox.execute_command, f"git add . && git commit -m 'GATE: {task.id}'")
                            success = True; break
                        except Exception as e: feedback = str(e); continue
                    else: feedback = cr_gate.critique
                if not success: return False
            GateUI.header("🏁 MISSION COMPLETE")
            return True
        except Exception as e: GateUI.error(f"CRASH: {str(e)}"); traceback.print_exc(); return False

if __name__ == "__main__":
    async def run():
        try:
            session_file = "metadata/LAST_SESSION.json"
            target_repo, issue_id = None, None
            if os.path.exists(session_file):
                with open(session_file, "r") as f: last = json.load(f)
                if input(f"{C_YELLOW}🔄 Resume {last.get('issue_id')}? [Y/n]: {C_END}").lower() != 'n':
                    target_repo, issue_id = last.get('repo'), last.get('issue_id')
            if not target_repo: target_repo = input("Target Repo: ").strip() or "."
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
                with open(plan_file, "r") as f:
                    content = f.read()
                    if "## Original Requirement\n" in content: desc = content.split("## Original Requirement\n")[1].split("## Execution Tasks")[0].strip()
            if not desc: desc = input("Task Description: ").strip()
            await orch.process_issue(issue_id, desc)
        except Exception as e: GateUI.error(f"FATAL: {str(e)}")
        finally: print(f"{C_GREEN}✨ Shutdown.{C_END}")
    asyncio.run(run())
