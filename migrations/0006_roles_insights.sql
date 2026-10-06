-- 0006 网站管理员独立角色（role='admin'）、站点设置、做题用时、自动发现的问题（insights）
-- 已有的「家长 + is_admin」账号保持不变（同时是家长和管理员）；新装系统第一个账号是独立的网站管理员。
-- users.role 增加 admin：PostgreSQL 直接换约束；SQLite 不能改约束，只能重建表（SQLite 只用于本地试用和测试）
-- @postgres
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check CHECK (role IN ('admin','parent','kid'));
-- @end
-- @sqlite
PRAGMA foreign_keys=OFF;
CREATE TABLE users_new (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE NOT NULL,
  pw_hash TEXT NOT NULL,
  name TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('admin','parent','kid')),
  parent_id INTEGER REFERENCES users(id),
  grade TEXT DEFAULT 'G3',
  school TEXT DEFAULT '',
  daily_minutes INTEGER DEFAULT 60,
  settings TEXT DEFAULT '{}',
  is_admin INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active',
  failed_logins INTEGER NOT NULL DEFAULT 0,
  locked_until TEXT,
  last_login_at TEXT,
  pw_changed_at TEXT,
  created_at TEXT NOT NULL,
  invited_by INTEGER,
  invite_code TEXT,
  approved_at TEXT,
  approved_by INTEGER,
  apply_note TEXT DEFAULT '',
  last_active_at TEXT
);
INSERT INTO users_new SELECT id, email, pw_hash, name, role, parent_id, grade, school, daily_minutes, settings, is_admin,
  status, failed_logins, locked_until, last_login_at, pw_changed_at, created_at, invited_by, invite_code, approved_at,
  approved_by, apply_note, last_active_at FROM users;
DROP TABLE users;
ALTER TABLE users_new RENAME TO users;
CREATE INDEX idx_users_parent ON users(parent_id);
CREATE INDEX idx_users_invited_by ON users(invited_by);
CREATE INDEX idx_users_status ON users(status);
PRAGMA foreign_keys=ON;
-- @end
CREATE TABLE site_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT
);
ALTER TABLE attempts ADD COLUMN ms INTEGER;
CREATE TABLE insights (
  id {{ID}},
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  key TEXT NOT NULL,
  kp_id TEXT,
  title TEXT NOT NULL,
  detail TEXT DEFAULT '',
  severity INTEGER DEFAULT 1,
  for_kid INTEGER DEFAULT 1,
  action TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  resolved_at TEXT
);
CREATE INDEX idx_insights_user ON insights(user_id, resolved_at);
CREATE INDEX idx_attempts_user_time ON attempts(user_id, created_at);
CREATE INDEX idx_ask_threads_kp ON ask_threads(user_id, kp_id);
