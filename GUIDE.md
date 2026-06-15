# GATE Pipeline Operational Guide

## Running A Release Arc

Start the orchestrator:

```bash
PYTHONPATH=. .venv/bin/python orchestrator/pipeline.py
```

Provide the target repository, issue id, and task description when prompted. The orchestrator creates or resumes a release arc in `trust_ledger.db`.

By default, GATE creates an isolated git worktree under:

```text
$GATE_WORKTREE_ROOT/gate-worktrees/
```

If `GATE_WORKTREE_ROOT` is not set, the system uses the OS temp directory.

## How A Task Completes

For each task:

1. Supervisor provides an explicit file allowlist.
2. Codex runs through `codex exec --sandbox workspace-write`.
3. GATE checks the git diff.
4. GATE rejects unexpected files and temp artifacts.
5. GATE installs dependencies in Docker when enabled and needed.
6. GATE runs deterministic validation.
7. Failed attempts are classified as plan, prompt, model, scope, dependency, syntax/type, or environment failures.
8. GATE either rewrites the Codex repair brief, repairs the pending plan/allowlist, escalates model routing, or trips a circuit breaker.
9. Gatekeeper reviews the verified diff.
10. GATE stages exact files and commits the task checkpoint.

## Inspecting State

Recent release arcs:

```bash
sqlite3 trust_ledger.db "SELECT id, issue_id, status, updated_at FROM release_arcs ORDER BY id DESC LIMIT 10;"
```

Task status:

```bash
sqlite3 trust_ledger.db "SELECT task_id, status, substr(commit_sha,1,8), updated_at FROM tasks ORDER BY id DESC LIMIT 20;"
```

Verification evidence:

```bash
sqlite3 trust_ledger.db "SELECT task_id, status, reason FROM verification_runs ORDER BY id DESC LIMIT 10;"
```

Gatekeeper rejections:

```bash
sqlite3 trust_ledger.db "SELECT task_id, gate_name, error_type, critique_summary FROM gate_reviews WHERE status = 'rejected' ORDER BY id DESC LIMIT 10;"
```

Failure classifications:

```bash
sqlite3 trust_ledger.db "SELECT task_id, failure_class, recommended_action, confidence FROM failure_analyses ORDER BY id DESC LIMIT 20;"
```

Prompt and model route history:

```bash
sqlite3 trust_ledger.db "SELECT task_id, attempt_number, substr(prompt_hash,1,12), model_route FROM prompt_rewrites ORDER BY id DESC LIMIT 20;"
```

Rule proposals:

```bash
sqlite3 trust_ledger.db "SELECT id, scope, confidence, status, rule_text FROM rule_proposals ORDER BY id DESC LIMIT 20;"
```

Approve a proposed rule:

```bash
sqlite3 trust_ledger.db "UPDATE rule_proposals SET status = 'approved', updated_at = CURRENT_TIMESTAMP WHERE id = 1;"
```

Replay self-improvement analysis without invoking Codex or LLMs:

```bash
PYTHONPATH=. .venv/bin/python scripts/replay_self_improvement.py 1 --project test-data-generator
```

## Recovery

Failed attempts are reset inside the isolated worktree, not the source checkout. Successful task checkpoints remain as commits on the arc branch.

If a worktree becomes invalid, remove it manually after confirming you do not need its uncommitted contents, then rerun the pipeline. GATE will recreate it from the source repository.

## Tuning

Primary settings are in `GateConfig`:

- `max_task_attempts`: increase for difficult tasks.
- `allow_dependency_install`: disable if the sandbox must never touch registries.
- `use_git_worktree`: keep enabled for safety.
- `codex_command`: set if Codex is installed under a different command name.
- `model_policy`: use `policy_ladder` for adaptive cost/quality routing.
- `rule_mode`: keep `review_first` when mined rules should require approval.
- `max_prompt_rewrites` and `max_plan_repairs`: bound self-improvement loops.
