-- 0015 游戏乐园（app/arena.py）：按个人水平出题的游戏，经验值账本，公平统计。
-- arena_ability：孩子在每个知识点上的「游戏能力值」θ（logit 尺度）。答对概率 = 1 / (1 + e^-(θ - b))。
--   起点取自掌握度，之后每答一题在线更新（Elo 式），n 是答过几题（越多步长越小）。
CREATE TABLE arena_ability (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kp_id TEXT NOT NULL,
  theta {{FLOAT}} NOT NULL,
  n INTEGER DEFAULT 0,
  updated_at TEXT,
  PRIMARY KEY (user_id, kp_id)
);
-- arena_calib：每个（题族, 级别）的难度 b，由全体孩子的作答在线校准；没有记录时用先验 级别 − 3。
CREATE TABLE arena_calib (
  family TEXT NOT NULL,
  level INTEGER NOT NULL,
  b {{FLOAT}} NOT NULL,
  n INTEGER DEFAULT 0,
  PRIMARY KEY (family, level)
);
-- 一局游戏。result：win / lose / draw / quit；seconds 由服务器按开始时间算（计入每天的游戏时长）。
-- data：局内状态（待答的题、冷冻到什么时候、连对、瞎蒙次数）和结束时客户端报的统计。
CREATE TABLE arena_matches (
  id {{ID}},
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  game TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'solo',
  started_at TEXT NOT NULL,
  ended_at TEXT,
  seconds INTEGER DEFAULT 0,
  result TEXT DEFAULT '',
  data TEXT DEFAULT '{}'
);
CREATE INDEX idx_arena_matches_user ON arena_matches(user_id, started_at);
-- 游戏里的每次作答：p_pred 是出题时预测的答对概率，用来检验「每个水平的孩子答对率都接近目标」（公平看板）。
CREATE TABLE arena_answers (
  id {{ID}},
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  match_id INTEGER,
  kp_id TEXT NOT NULL,
  family TEXT NOT NULL,
  level INTEGER NOT NULL,
  q TEXT DEFAULT '',
  p_pred {{FLOAT}},
  correct INTEGER NOT NULL,
  ms INTEGER,
  xp INTEGER DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_arena_answers_user ON arena_answers(user_id, created_at);
-- 经验值账本：只追加。余额 = 求和。
CREATE TABLE arena_ledger (
  id {{ID}},
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  delta INTEGER NOT NULL,
  reason TEXT NOT NULL,
  match_id INTEGER,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_arena_ledger_user ON arena_ledger(user_id, created_at);
