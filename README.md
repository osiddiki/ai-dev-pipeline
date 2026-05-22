# AI Development Pipeline (GATE Framework)

A standalone autonomous software engineering team based on the [GATE Analysis Framework](https://michael.roth.rocks/research/gate-analysis/#1).

## Architecture
- **Orchestrator:** Task decomposition and queue management.
- **Agents:** Supervisor, Worker, and Gatekeeper operating on bounded tasks.
- **Ledger:** SQLite database acting as the "Trust Ledger" for all gate reviews.
- **Environment:** Docker-based sandbox execution.

## The 4 Gates
1. **review_plan:** Shift-left validation of decomposed tasks.
2. **review_design:** Pre-code technical approach validation.
3. **codereview:** Narrow context file-scoped review.
4. **review_code:** Broad context task validation.
