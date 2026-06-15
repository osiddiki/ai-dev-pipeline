# GATE AI Development Pipeline

GATE (Gate Analysis & Trust Engineering) is an autonomous release controller for software engineering tasks. The current architecture uses Codex as the implementation worker and keeps GATE responsible for planning, policy, verification, audit history, and exact git checkpoints.

## Architecture

```text
Supervisor -> reviewed implementation plan
Codex Worker -> direct file edits in an isolated git worktree
Deterministic Verifier -> schema, syntax, type, build, lint, and test checks
Gatekeeper -> adversarial model review of the verified diff
Self-Improvement Layer -> failure classification, repair prompts, plan repair, model routing, rule proposals
Ledger -> task, gate, verification, and commit audit trail
```

## What Changed

- The Worker no longer emits fragile SEARCH/REPLACE patches.
- Codex edits files directly through `codex exec`.
- Each release arc runs in an isolated git worktree by default.
- GATE rejects unexpected files, temp artifacts, prose-as-code, invalid JSON, failed TypeScript checks, and failed local project scripts.
- Commits stage exact verified files only. The pipeline never uses `git add .`.
- Dependency installation is controller-owned, not worker-owned, and happens only when enabled by config.
- The ledger records gate reviews and deterministic verification results.
- Failed attempts are classified into durable failure classes before retrying.
- Repeated failures produce concise Codex repair briefs, bounded plan repairs, model-route escalation, or a deterministic stop.
- Historical failures are mined into inactive rule proposals; approved rules are injected into future Supervisor, Codex, Gatekeeper, and verifier context.

## Requirements

- Python 3.10+
- Docker Desktop
- Codex CLI authenticated and available as `codex`
- API keys for the planner/reviewer models configured in `.env`

Install Python dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
PYTHONPATH=. .venv/bin/python orchestrator/pipeline.py
```

The pipeline will ask for:

- target repository
- issue id
- task description, unless a plan file already contains the original requirement

Plans and project policy live under:

```text
metadata/<project-name>/
```

## Configuration

Project-specific policy can be stored in `metadata/<project-name>/gate.yml`.

Runtime policy defaults live in `agents/models.py`:

- `codex_command`: command used to run Codex CLI
- `codex_sandbox`: Codex sandbox mode, default `workspace-write`
- `use_git_worktree`: run each arc in an isolated worktree
- `max_task_attempts`: retry budget per task
- `allow_dependency_install`: allow GATE to run dependency installs in Docker
- `model_policy`: `policy_ladder`, `best_always`, or `cost_first`
- `rule_mode`: defaults to `review_first`, so learned rules are proposed but not activated
- `max_plan_repairs`: bounded plan/allowlist repair budget per task
- `max_prompt_rewrites`: bounded prompt repair budget per task
- `cheap_model`, `strong_planner_model`, `strong_verifier_model`: model-routing ladder

## Trust Model

Codex is trusted to attempt implementation. GATE is trusted to decide whether the implementation is acceptable.

The acceptance chain is:

1. Codex edits files.
2. GATE reads the git diff.
3. GATE rejects files outside the task allowlist.
4. GATE runs deterministic verification.
5. Failed attempts are classified and routed to prompt repair, plan repair, model escalation, or a circuit breaker.
6. Gatekeeper reviews the already-verified diff.
7. GATE stages exact files and commits a checkpoint.

## Learned Rules

Approved durable rules can be stored in `metadata/<project-name>/rules.yml`:

```yaml
rules:
  - id: json-parse-after-edit
    scope: json
    text: After editing JSON, parse the exact file before declaring the task complete.
```

The `rule_proposals` table stores mined rules as `proposed` by default. Set a proposal to `approved` to activate it for future arcs.
