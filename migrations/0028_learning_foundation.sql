-- Canonical concepts are projections; existing kp IDs and historical evidence remain intact.
CREATE TABLE concept_mastery (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  concept_id TEXT NOT NULL,
  score {{FLOAT}} DEFAULT 0,
  attempts INTEGER DEFAULT 0,
  correct INTEGER DEFAULT 0,
  status TEXT DEFAULT 'unknown',
  source TEXT DEFAULT '',
  updated_at TEXT,
  stability {{FLOAT}} DEFAULT 0,
  difficulty {{FLOAT}} DEFAULT 5,
  last_ev TEXT,
  evidence TEXT DEFAULT '{}',
  model TEXT DEFAULT '',
  PRIMARY KEY(user_id, concept_id)
);
CREATE TABLE answer_receipts (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  request_id TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  response TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(user_id, request_id)
);
CREATE TABLE llm_calls (
  id {{ID}},
  user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  task TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  status TEXT NOT NULL,
  elapsed_ms INTEGER NOT NULL,
  input_tokens INTEGER,
  output_tokens INTEGER,
  error_code TEXT DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX idx_llm_calls_day ON llm_calls(created_at, task);
