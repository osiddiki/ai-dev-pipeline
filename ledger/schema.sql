CREATE TABLE IF NOT EXISTS release_arcs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id TEXT NOT NULL,
    repository TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'planning', -- planning, in_progress, completed, failed
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    arc_id INTEGER NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', -- pending, in_progress, code_review, completed, failed
    dependencies TEXT, -- JSON list of task IDs
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (arc_id) REFERENCES release_arcs(id)
);

CREATE TABLE IF NOT EXISTS gate_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER,
    arc_id INTEGER,
    gate_name TEXT NOT NULL, -- review_plan, review_design, codereview, review_code
    model_id TEXT NOT NULL,
    status TEXT NOT NULL, -- approved, rejected
    critique_summary TEXT,
    attempt_number INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES tasks(id),
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
