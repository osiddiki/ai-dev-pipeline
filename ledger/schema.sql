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
    target_files TEXT, -- JSON list of allowed files for this implementation slice
    dependencies TEXT, -- JSON list of task IDs
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (arc_id, task_id),
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

CREATE TABLE IF NOT EXISTS verification_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    arc_id INTEGER NOT NULL,
    task_id TEXT,
    status TEXT NOT NULL,
    reason TEXT,
    used_command TEXT,
    evidence TEXT,
    changed_files TEXT,
    attempt_number INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (arc_id) REFERENCES release_arcs(id)
);

CREATE TABLE IF NOT EXISTS failure_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    arc_id INTEGER NOT NULL,
    task_id TEXT,
    attempt_number INTEGER DEFAULT 1,
    failure_class TEXT NOT NULL,
    confidence REAL DEFAULT 0,
    evidence TEXT,
    recommended_action TEXT,
    changed_files TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (arc_id) REFERENCES release_arcs(id)
);

CREATE TABLE IF NOT EXISTS plan_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    arc_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    plan_json TEXT NOT NULL,
    reason TEXT,
    parent_version INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (arc_id) REFERENCES release_arcs(id)
);

CREATE TABLE IF NOT EXISTS rule_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_text TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'global',
    source_failures TEXT,
    confidence REAL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'proposed', -- proposed, approved, rejected, retired
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS prompt_rewrites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    arc_id INTEGER NOT NULL,
    task_id TEXT,
    attempt_number INTEGER DEFAULT 1,
    prompt_hash TEXT NOT NULL,
    rewrite_summary TEXT,
    active_rules_used TEXT,
    model_route TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (arc_id) REFERENCES release_arcs(id)
);
