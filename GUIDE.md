# GATE Pipeline Operational Guide

This guide explains how to use and optimize your autonomous AI Development Pipeline.

## 1. How to Use
To trigger a new "Release Arc" (a unit of work), follow these steps:

1. **Configure the Task:** Open `orchestrator/pipeline.py`.
2. **Set the Target:** Change `target_repo` to the directory name of the project you want to modify (e.g., `sevicare-app`).
3. **Describe the Issue:** Update the `process_issue` call with a clear ID and a detailed description of what you want.
4. **Run it:**
   ```bash
   cd ai-dev-pipeline
   PYTHONPATH=. .venv/bin/python orchestrator/pipeline.py
   ```

## 2. Optimization Strategies

### Quality vs. Cost
- **Current Setup:** Flash (Worker) + Pro (Gatekeeper). This is the best balance for most tasks.
- **For High-Value Code:** If you are modifying billing or clinical logic, change the Gatekeeper in `integrations/gemini_client.py` to `gemini/gemini-2.0-pro-thinking-exp` (if available) or `openai/o1-preview`.
- **For Repetitive Tasks:** For simple refactors, you can use Flash for both roles, but you MUST increase the Gatekeeper's `temperature` to 0.7 to encourage it to find errors.

### Handling "High Demand" (503 Errors)
If Gemini Pro is busy:
1. Wait 60 seconds and retry.
2. The pipeline is stateful—it saves your progress in `trust_ledger.db`. You can modify the code to "resume" an arc rather than starting over.

## 3. Monitoring "System Trust"
The `trust_ledger.db` is your primary tool for monitoring. Run these queries to check your team's performance:

**Check why the Gatekeeper is rejecting work:**
```bash
sqlite3 trust_ledger.db "SELECT critique_summary FROM gate_reviews WHERE status = 'rejected' ORDER BY id DESC LIMIT 5;"
```

**Check which model is the most "expensive":**
```bash
sqlite3 trust_ledger.db "SELECT model_id, SUM(prompt_tokens + completion_tokens) as total_tokens FROM metrics GROUP BY model_id;"
```

## 4. Sandbox Maintenance
- The sandbox runs in **Linux**. If your target project requires specific tools (like `go` or `pnpm`), update the `image` parameter in `environment/sandbox.py` to a more robust image (e.g., `sevicare-dev-env`).
- Always ensure Docker Desktop is running before starting the pipeline.
