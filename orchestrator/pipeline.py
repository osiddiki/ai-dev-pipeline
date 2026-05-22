import asyncio
import structlog
from typing import Any
from agents.supervisor import SupervisorAgent, SupervisorPlan
from agents.worker import WorkerAgent, WorkerResult
from agents.gatekeeper import GatekeeperAgent
from ledger.database import get_db

logger = structlog.get_logger()

class ReleaseArcOrchestrator:
    """Manages the full GATE pipeline execution (The Release Arc)."""
    
    def __init__(self, target_repo: str):
        self.target_repo = target_repo
        # Models are explicitly split. Worker generates, Gatekeeper validates.
        self.supervisor = SupervisorAgent(model_id="gpt-4o")
        self.worker = WorkerAgent(model_id="gpt-4o")
        self.gatekeeper = GatekeeperAgent(model_id="gemini-2.0-pro")
        
    async def process_issue(self, issue_id: str, issue_description: str) -> bool:
        """Process a full issue through the GATE framework."""
        db = await get_db()
        logger.info("Starting Release Arc", issue_id=issue_id, repo=self.target_repo)
        
        # Insert initial ledger record
        cursor = await db.execute(
            "INSERT INTO release_arcs (issue_id, repository) VALUES (?, ?)", 
            (issue_id, self.target_repo)
        )
        arc_id = cursor.lastrowid
        await db.commit()
        
        # 1. Planning Phase
        plan_result = await self.supervisor.invoke({}, issue_description)
        plan: SupervisorPlan = plan_result.output
        
        # Gate 1: Plan Review (Strict 2-attempt limit + HITL Escalation)
        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            plan_gate = await self.gatekeeper.review_plan(issue_description, plan)
            await self._log_gate(db, arc_id, None, "review_plan", plan_gate.approved, plan_gate.critique, attempt=attempt)
            
            if plan_gate.approved:
                break
                
            if attempt < max_attempts:
                logger.warning(f"Plan rejected (Attempt {attempt}). Requesting autonomous revision.")
                plan_result = await self.supervisor.revise_plan(plan, plan_gate.critique)
                plan = plan_result.output
            else:
                # HITL Escalation: The "Reasoning Loop" protection
                print("\n" + "="*80)
                print("🚨 GATEKEEPER CRITIQUE (REPEATED FAILURE):")
                print(plan_gate.critique)
                print("="*80)
                user_hint = input("\nThe Gatekeeper is still not satisfied. Provide a strategic hint or clarification to guide the Supervisor: ")
                
                if user_hint.lower() in ["exit", "quit", "stop"]:
                    logger.error("Release Arc cancelled by user.")
                    return False
                
                logger.info("Human Steering received. Attempting final guided revision.")
                plan_result = await self.supervisor.revise_plan(plan, f"USER GUIDANCE: {user_hint}\n\nPREVIOUS CRITIQUE: {plan_gate.critique}")
                plan = plan_result.output
                
                # Final check after human steering
                plan_gate = await self.gatekeeper.review_plan(issue_description, plan)
                await self._log_gate(db, arc_id, None, "review_plan", plan_gate.approved, plan_gate.critique, attempt=attempt+1)
                
                if not plan_gate.approved:
                    logger.error("Release Arc failed even with Human Steering. Aborting.")
                    return False

        # Persist tasks to ledger
        for t in plan.tasks:
            await db.execute(
                "INSERT INTO tasks (arc_id, description) VALUES (?, ?)", 
                (arc_id, t.description)
            )
        await db.commit()

        # 2. Execution Phase
        all_diffs = []
        for task in plan.tasks:
            logger.info("Starting Task", task_id=task.id)
            
            # Simulated Design Phase
            proposed_design = f"Design for {task.id}"
            design_gate = await self.gatekeeper.review_design(task, proposed_design)
            # Log gate... (simplified)
            
            if not design_gate.approved:
                logger.error("Task failed design gate", task=task.id)
                continue
                
            # Simulated Code Phase
            worker_res = await self.worker.invoke({}, task)
            w_output: WorkerResult = worker_res.output
            
            # Gate 3: Code Review
            cr_gate = await self.gatekeeper.codereview(task, w_output)
            # Log gate...
            
            if cr_gate.approved:
                all_diffs.append(w_output)
                
        # 3. Final Verification Phase
        final_gate = await self.gatekeeper.review_code(issue_description, plan, all_diffs)
        await self._log_gate(db, arc_id, None, "review_code", final_gate.approved, final_gate.critique)
        
        status = "completed" if final_gate.approved else "failed"
        await db.execute("UPDATE release_arcs SET status = ? WHERE id = ?", (status, arc_id))
        await db.commit()
        
        logger.info("Release Arc finished", status=status)
        return final_gate.approved

    async def _log_gate(self, db, arc_id, task_id, gate_name, approved, critique, attempt=1):
        """Helper to write to the Trust Ledger."""
        status = "approved" if approved else "rejected"
        await db.execute(
            """INSERT INTO gate_reviews (arc_id, task_id, gate_name, model_id, status, critique_summary, attempt_number)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (arc_id, task_id, gate_name, self.gatekeeper.model_id, status, critique, attempt)
        )
        await db.commit()

# Simple test harness
if __name__ == "__main__":
    async def run():
        # FINAL MISSION: Developer Onboarding & Audit
        # This requires the AI to read the sevicare-app monorepo and generate docs + hunt bugs.
        orch = ReleaseArcOrchestrator(target_repo="sevicare-app")
        await orch.process_issue(
            "ONBOARDING-001", 
            """
            Create a comprehensive ONBOARDING.md for new developers.
            REQUIREMENTS:
            1. Explain the full system architecture (Monorepo, 21 services, Federation).
            2. Document the primary database models and relationships.
            3. Provide a 'Quick Start' for running the full stack and tests.
            4. Audit the codebase: Find 3 'Good First Issues' (small bugs, lint errors, or missing tests) 
               that a new dev can fix to learn the ropes.
            """
        )
    asyncio.run(run())
