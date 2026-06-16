# GATE AI Development Pipeline

GATE (Gate Analysis & Trust Engineering) is an autonomous, agentic release controller for software engineering tasks. It manages a team of AI agents that plan, test, implement, and review code in an isolated git worktree before proposing it for merge.

## Architecture

The pipeline consists of specialized agents operating in an orchestration loop:

1. **Supervisor Agent**: Uses RAG (Retrieval-Augmented Generation) and MCP tools to explore the codebase and decompose a given issue into a JSON array of strict atomic tasks.
2. **Test Writer Agent**: A specialized TDD (Test-Driven Development) agent that reads the task and writes failing unit tests for the expected behavior using Aider.
3. **Aider Worker Agent**: Implements the actual feature/bugfix code natively using the Aider CLI inside an isolated git worktree, aiming to pass the newly written tests.
4. **Deterministic Verifier**: Automatically executes project-native validation commands (`npm run test`, `pytest`, `python -m py_compile`) via a local bash MCP server to ensure code correctness.
5. **Gatekeeper Agent**: Acts as a senior architectural reviewer. It runs in a dynamic 10-step ReAct tool loop, exploring the codebase to ensure the worker's diff did not break any surrounding dependencies.
6. **Self-Improvement Layer**: Extracts historical failures, rewrites prompts, routes models, and proposes durable engineering rules.
7. **Ledger**: Maintains an SQLite audit trail of all task attempts, gates, and verification results.

## Requirements

- Python 3.10+
- `aider-chat` installed globally
- API keys for models configured in `.env` (e.g. `GEMINI_API_KEY`, `OPENAI_API_KEY`)

Install Python dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
python -m orchestrator.main --repo /path/to/repo --issue "Fix the auth bug"
```

The pipeline will automatically branch into a safe git worktree, plan the implementation, and begin executing the agent loop.

## Configuration

Project-specific policy can be stored in `metadata/<project-name>/gate.yml`.

Runtime policy defaults live in `agents/models.py`.

## Trust Model

Aider is trusted to attempt implementation. GATE is trusted to decide whether the implementation is acceptable.

The acceptance chain is:
1. Test Writer drafts failing tests.
2. Aider edits source files to pass the tests.
3. GATE runs deterministic verification (`pytest`, `npm test`).
4. Failed attempts are classified and routed to prompt repair or model escalation.
5. Gatekeeper agentically explores the codebase to review the verified diff.
6. GATE stages exact files and commits a checkpoint.
