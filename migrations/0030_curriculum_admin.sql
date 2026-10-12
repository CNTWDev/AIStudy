-- Administrator edits are database overrides; repository curricula remain the baseline.
CREATE TABLE curriculum_revisions (
  id {{ID}},
  payload TEXT NOT NULL,
  note TEXT NOT NULL,
  actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE curriculum_workspace (
  id INTEGER PRIMARY KEY,
  draft TEXT NOT NULL DEFAULT '{}',
  version INTEGER NOT NULL DEFAULT 0,
  published_id INTEGER REFERENCES curriculum_revisions(id),
  actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  updated_at TEXT NOT NULL DEFAULT ''
);
INSERT INTO curriculum_workspace(id) VALUES(1);
