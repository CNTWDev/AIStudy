-- 0001 初始表结构。
-- 占位符由 app/migrate.py 按数据库类型替换：{{ID}} 自增主键，{{FLOAT}} 浮点数。
-- 时间统一存 ISO 8601 文本（带时区），布尔值存 0/1。
CREATE TABLE users (
  id {{ID}},
  email TEXT UNIQUE NOT NULL,
  pw_hash TEXT NOT NULL,
  name TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('parent','kid')),
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
  created_at TEXT NOT NULL
);
CREATE INDEX idx_users_parent ON users(parent_id);
-- 孩子选修的教材包（一个学科一个版本），stage = 该科当前学习进度所在学段
CREATE TABLE enrollments (
  user_id INTEGER NOT NULL REFERENCES users(id),
  pack_id TEXT NOT NULL,
  stage TEXT NOT NULL,
  active INTEGER DEFAULT 1,
  PRIMARY KEY (user_id, pack_id)
);
CREATE TABLE mastery (
  user_id INTEGER NOT NULL,
  kp_id TEXT NOT NULL,
  score {{FLOAT}} DEFAULT 0,
  attempts INTEGER DEFAULT 0,
  correct INTEGER DEFAULT 0,
  status TEXT DEFAULT 'unknown',
  source TEXT DEFAULT '',
  updated_at TEXT,
  PRIMARY KEY (user_id, kp_id)
);
CREATE TABLE items (
  id TEXT PRIMARY KEY,
  kp_id TEXT NOT NULL,
  kp_ids TEXT NOT NULL,
  type TEXT NOT NULL,
  difficulty INTEGER DEFAULT 2,
  data TEXT NOT NULL,
  source TEXT DEFAULT 'seed',
  created_at TEXT NOT NULL
);
CREATE INDEX idx_items_kp ON items(kp_id);
CREATE TABLE attempts (
  id {{ID}},
  user_id INTEGER NOT NULL,
  item_id TEXT,
  kp_id TEXT NOT NULL,
  mode TEXT NOT NULL,
  correct INTEGER,
  answer TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_attempts_user ON attempts(user_id, created_at);
CREATE TABLE cards (
  id {{ID}},
  user_id INTEGER NOT NULL,
  kind TEXT NOT NULL,
  front TEXT NOT NULL,
  back TEXT NOT NULL,
  extra TEXT DEFAULT '{}',
  kp_id TEXT,
  box INTEGER DEFAULT 0,
  due TEXT NOT NULL,
  lapses INTEGER DEFAULT 0,
  reviews INTEGER DEFAULT 0,
  starred INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,
  last_review TEXT,
  UNIQUE(user_id, kind, front)
);
CREATE INDEX idx_cards_due ON cards(user_id, due);
CREATE TABLE diag_sessions (
  id {{ID}},
  user_id INTEGER NOT NULL,
  pack_id TEXT NOT NULL,
  state TEXT NOT NULL,
  status TEXT DEFAULT 'running',
  created_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE TABLE days (
  user_id INTEGER NOT NULL,
  day TEXT NOT NULL,
  plan TEXT NOT NULL,
  minutes INTEGER DEFAULT 0,
  checked_in INTEGER DEFAULT 0,
  reflection TEXT DEFAULT '',
  PRIMARY KEY (user_id, day)
);
CREATE TABLE readings (
  id {{ID}},
  user_id INTEGER NOT NULL,
  lang TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  source TEXT DEFAULT 'paste',
  level TEXT DEFAULT '',
  questions TEXT DEFAULT '[]',
  minutes INTEGER DEFAULT 0,
  finished_at TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE lookups (
  id {{ID}},
  user_id INTEGER NOT NULL,
  reading_id INTEGER,
  query TEXT NOT NULL,
  context TEXT DEFAULT '',
  result TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE sentences (
  id {{ID}},
  user_id INTEGER NOT NULL,
  word TEXT NOT NULL,
  sentence TEXT NOT NULL,
  feedback TEXT NOT NULL,
  ok INTEGER DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE llm_cache (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE llm_usage (
  user_id INTEGER NOT NULL,
  day TEXT NOT NULL,
  calls INTEGER DEFAULT 0,
  PRIMARY KEY (user_id, day)
);

-- ---------------------------------------------------------------- 账号系统
-- 登录会话：cookie 里只放随机令牌，数据库里存它的 sha256。退出、改密码、停用账号都会让会话失效。
CREATE TABLE sessions (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  ip TEXT DEFAULT '',
  user_agent TEXT DEFAULT ''
);
CREATE INDEX idx_sessions_user ON sessions(user_id);
-- 注册邀请码（REGISTRATION=invite 时，新家庭凭邀请码注册）
CREATE TABLE invites (
  code TEXT PRIMARY KEY,
  created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  note TEXT DEFAULT '',
  max_uses INTEGER NOT NULL DEFAULT 1,
  used INTEGER NOT NULL DEFAULT 0,
  expires_at TEXT,
  created_at TEXT NOT NULL
);
-- 一次性重设密码链接（由管理员生成后发给用户；以后接入邮件也用这张表）
CREATE TABLE password_resets (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_by INTEGER,
  expires_at TEXT NOT NULL,
  used_at TEXT,
  created_at TEXT NOT NULL
);
-- 账号安全日志：登录成功/失败、改密码、停用等
CREATE TABLE auth_events (
  id {{ID}},
  user_id INTEGER,
  email TEXT DEFAULT '',
  event TEXT NOT NULL,
  detail TEXT DEFAULT '',
  ip TEXT DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX idx_auth_events_time ON auth_events(created_at);
