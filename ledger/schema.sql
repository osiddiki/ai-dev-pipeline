CREATE TABLE IF NOT EXISTS release_arcs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id TEXT NOT NULL,
    repository TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'planning', -- planning, in_progress, completed, failed
    repo_context TEXT,
    discovery_report TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    arc_id INTEGER NOT NULL,
    task_id TEXT, -- e.g. 'task_1'
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', -- pending, in_progress, code_review, completed, failed
    commit_sha TEXT, -- The Git HEAD SHA after this task was finalized
    dependencies TEXT, -- JSON list of task IDs
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (arc_id) REFERENCES release_arcs(id)
);

CREATE TABLE IF NOT EXISTS gate_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    arc_id INTEGER NOT NULL,
    task_id TEXT, -- The string ID like 'task_1'
    gate_name TEXT NOT NULL, -- review_plan, review_design, codereview, review_code
    model_id TEXT NOT NULL,
    status TEXT NOT NULL, -- approved, rejected
    error_type TEXT, -- omission, systematic, incoherent
    critique_summary TEXT,
    attempt_number INTEGER DEFAULT 1,
    verification_method TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (arc_id) REFERENCES release_arcs(id)
);


CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gate_review_id INTEGER,
    agent_id TEXT,
    model_id TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    latency_ms INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (gate_review_id) REFERENCES gate_reviews(id)
);
