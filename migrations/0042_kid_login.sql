-- 0042 孩子用「登录名 + 4 位数字」登录，不用邮箱（docs/DESIGN.md §5.37）。
-- users.login：登录名（孩子自己记得住的小名，比如「果果」；全站不重复，存小写）。家长添加的成员都有，大人可以没有。
-- users.email 改成可以不填：家长、自学者还是用邮箱登录；孩子没有邮箱，长大自己管账号时再加。
-- device_logins：这台设备上登录过的孩子。登录页先显示他们的头像，点头像输 4 位数字就换过来（共用一台设备的家庭）。
--   device_hash 是设备 cookie 里随机令牌的 sha256。
-- 已有的孩子：登录名先用名字，重名的在后面加「-编号」；原来的邮箱和密码照样能登录，家长改成 4 位数字以后用数字。
-- @postgres
ALTER TABLE users ALTER COLUMN email DROP NOT NULL;
ALTER TABLE users ADD COLUMN login TEXT;
-- @end
-- @sqlite
CREATE TABLE users_new (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE,
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
  last_active_at TEXT,
  must_change_pw INTEGER NOT NULL DEFAULT 0,
  school_type TEXT DEFAULT '',
  preset TEXT DEFAULT '',
  nickname TEXT NOT NULL DEFAULT '',
  city TEXT NOT NULL DEFAULT '',
  login TEXT
);
INSERT INTO users_new SELECT id, email, pw_hash, name, role, parent_id, grade, school, daily_minutes, settings, is_admin,
  status, failed_logins, locked_until, last_login_at, pw_changed_at, created_at, invited_by, invite_code, approved_at,
  approved_by, apply_note, last_active_at, must_change_pw, school_type, preset, nickname, city, NULL FROM users;
DROP TABLE users;
ALTER TABLE users_new RENAME TO users;
CREATE INDEX idx_users_parent ON users(parent_id);
CREATE INDEX idx_users_invited_by ON users(invited_by);
CREATE INDEX idx_users_status ON users(status);
-- @end
UPDATE users SET login = lower(replace(trim(name), ' ', '')) || CASE WHEN (
    SELECT COUNT(*) FROM users u2 WHERE u2.role = 'kid' AND u2.parent_id IS NOT NULL
      AND lower(replace(trim(u2.name), ' ', '')) = lower(replace(trim(users.name), ' ', ''))) > 1
  THEN '-' || CAST(id AS TEXT) ELSE '' END
WHERE role = 'kid' AND parent_id IS NOT NULL;
CREATE UNIQUE INDEX idx_users_login ON users(login);
CREATE TABLE device_logins (
  device_hash TEXT NOT NULL,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  last_at TEXT NOT NULL,
  PRIMARY KEY (device_hash, user_id)
);
