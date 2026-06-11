# GATE AI Development Pipeline (v6.5)

An industrial-grade, autonomous software engineering platform grounded in Michael Rothrock's **GATE (Gate Analysis & Trust Engineering) Framework**. 

This pipeline forges a self-improving, multi-agent team capable of executing long-term autonomous development arcs (Tier 3 work) while maintaining a strict, human-aligned "Trust Topology."

---

## 🚀 Key Features

### 1. The 4-Gate Architecture
Every atomic task must pass through a rigorous, multi-stage validation pipeline:
- **Gate 1: Plan Review** (Architectural decomposition & requirement coverage).
- **Gate 2: Design Review** (Pre-code validation of technical approach & OOP patterns).
- **Gate 3: Code Review** (File-scoped surgical verification & syntax checks).
- **Gate 4: System Review** (Final cross-file consistency & dependency validation).

### 2. Transactional "Verify-then-Commit"
The system utilizes hidden `.tmp` staging and atomic renames. Production source files are **never** overwritten until the new code has been 100% verified by both language-specific linters and empirical sandbox tests.

### 3. Bulletproof Surgical Patching
An advanced **State-Machine Patch Parser** combined with a **Fuzzy/Normalized Whitespace Fallback** ensures that AI-generated SEARCH/REPLACE blocks are applied with 100% accuracy, even when minor formatting glitches occur.

### 4. Meta-Analysis Loop (Continuous Learning)
The pipeline includes an active intelligence engine that queries the **Trust Ledger** (SQLite) to identify historical failure patterns. It dynamically injects "Meta-Warnings" into current runs to prevent the AI from repeating past mistakes.

### 5. Multi-Model Adversarial Strategy
Strictly separates roles between specialized model families:
- **Executor (Worker):** DeepSeek-Chat (Highly efficient coding specialist).
- **Verifier (Gatekeeper):** Gemini 3.1 Pro (Max-reasoning for adversarial review).
- **Planner (Supervisor):** Gemini 2.5 Pro (High-context architectural steering).

---

## 🛠 Technical Stack
- **Orchestration:** Python (Asyncio)
- **Database:** SQLite (The Trust Ledger via `aiosqlite`)
- **Sandbox:** Docker (Isolated Node.js/TypeScript environments)
- **LLM Interface:** LiteLLM (Universal provider support)
- **Configuration:** YAML-based decoupled project directives

---

## 📂 Project Structure
```text
ai-dev-pipeline/
├── agents/             # specialized AI agents (Supervisor, Worker, etc.)
├── environment/        # Docker sandbox & container management
├── integrations/       # LLM client & API wrappers
├── ledger/             # Database schema & initialization
├── metadata/           # Local vault for plans, rulings, and repo indexes
├── orchestrator/       # The core GATE engine & verification logic
└── scripts/            # Offline RAG indexing & utility tools
```

---

## 🚦 Getting Started

### 1. Prerequisites
- Docker Desktop (Running)
- Python 3.10+
- API Keys for Gemini and DeepSeek (configured in `.env`)

### 2. Installation
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Usage
**Initialize a Project Index:**
```bash
python3 scripts/build_repo_index.py /path/to/target-repo
```

**Launch the Pipeline:**
```bash
PYTHONPATH=. .venv/bin/python orchestrator/pipeline.py
```

---

## 📜 Principles
This pipeline adheres to the **Trust Topology** research: 
1. **Never trust the AI.** Trust the Gates.
2. **Context is the Enemy.** Use amnesiac agents for atomic tasks.
3. **Shift-Left.** Catch logical errors in planning and design before a single line of code is written.

---
*Based on the research at [michael.roth.rocks](https://michael.roth.rocks/research/gate-analysis/)*
