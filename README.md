# GATE AI Development Pipeline

GATE (Gate Analysis & Trust Engineering) is an autonomous, agentic release controller for software engineering tasks. It manages a team of AI agents that plan, test, implement, and review code in an isolated git worktree before proposing it for merge.

## Architecture

The pipeline consists of specialized agents operating in an orchestration loop:

1. **Supervisor Agent**: Uses fast keyword search, AST-search (`grep-ast`), and optional semantic retrieval to explore the codebase and decompose a given issue into a strict JSON array of tasks (with robust multi-model markdown parsing).
2. **Test Writer Agent**: A specialized TDD agent that reads the task and writes failing unit tests.
3. **Aider Worker Agent**: Implements the actual feature code natively using the Aider CLI. 
   - **Parallel Worktrees**: Operates in fully isolated, parallel git worktrees via `asyncio.gather` for simultaneous task execution.
4. **Deterministic Verifier**: Automatically executes project-native validation commands inside **isolated Docker containers** mapped directly to the active worktree.
5. **Gatekeeper Agent**: Acts as a senior architectural reviewer in a dynamic ReAct tool loop.
6. **Self-Improvement Layer**: Extracts historical failures, rewrites prompts, routes models, and proposes durable engineering rules.
7. **Ledger**: Maintains an SQLite audit trail.

## Requirements

- Python 3.10+
- `aider-chat` installed globally
- Docker (for dynamic verifier containers)
- API keys for models configured in `.env` (Defaults to DeepSeek via `DEEPSEEK_API_KEY`)

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

Optional semantic search is controlled by environment variables:

```bash
GATE_RAG_PROVIDER=disabled   # default, cheapest path
GATE_RAG_PROVIDER=local      # local sentence-transformers embeddings
GATE_RAG_PROVIDER=api        # hosted embeddings through LiteLLM
GATE_RAG_MODEL=BAAI/bge-small-en-v1.5
GATE_GEMINI_SAFETY_MODE=default   # or block_none
```

Recommended default: leave semantic search disabled and rely on `rg` plus `grep-ast`, then enable `GATE_RAG_PROVIDER=local` only when repository search quality becomes a real bottleneck.

When semantic search is enabled, GATE stores the vector index in `.gate_rag_cache/` and skips rebuilds when the cached index already matches the repository HEAD commit, provider, and embedding model.

## Trust Model

Aider is trusted to attempt implementation. GATE is trusted to decide whether the implementation is acceptable.

The acceptance chain is:
1. Test Writer drafts failing tests.
2. Aider edits source files to pass the tests.
3. GATE runs deterministic verification (`pytest`, `npm test`).
4. Failed attempts are classified and routed to prompt repair or model escalation.
5. Gatekeeper agentically explores the codebase to review the verified diff.
6. GATE stages exact files and commits a checkpoint.
