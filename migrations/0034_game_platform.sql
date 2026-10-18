-- 0034 乐园游戏平台（docs/DESIGN.md 5.26）：单机和联网合成一套——房间（1～N 人）和设备、每支队伍的进度、每一局的记录。
-- 房间：单机是一个人的房间（每个孩子每款游戏一个，一直留着），联网是家里几个孩子的房间。session_id 是正在玩的这一局。
CREATE TABLE game_rooms (
  id {{ID}},
  game TEXT NOT NULL,
  family_id INTEGER NOT NULL DEFAULT 0,
  host_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  multi INTEGER NOT NULL DEFAULT 0,
  level INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'open',
  session_id INTEGER,
  created_at TEXT NOT NULL,
  seen_at TEXT NOT NULL
);
CREATE INDEX idx_game_rooms_family ON game_rooms(family_id, status);
CREATE INDEX idx_game_rooms_host ON game_rooms(host_id, game);
-- 房间里的人：角色、什么时候进来的（房主走了由最早进来的接任）、现在用哪台设备在玩、这台设备最近一次存档的时间。
CREATE TABLE room_members (
  room_id INTEGER NOT NULL REFERENCES game_rooms(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role TEXT NOT NULL DEFAULT '',
  joined_at TEXT NOT NULL,
  device TEXT NOT NULL DEFAULT '',
  seen_at TEXT,
  PRIMARY KEY (room_id, user_id)
);
CREATE INDEX idx_room_members_user ON room_members(user_id);
-- 每一局（Record）：哪款游戏、哪一关（没有关卡是 0）、哪支队伍（孩子 id 排好序，逗号连起来）、随机种子、结果和用时、星星、
-- 游戏报的数据（data）；state 是玩的时候的存档（刷新、换设备接着玩），结束就清空。每个人的答题在 arena_matches.session_id。
CREATE TABLE game_sessions (
  id {{ID}},
  game TEXT NOT NULL,
  level INTEGER NOT NULL DEFAULT 0,
  room_id INTEGER,
  team TEXT NOT NULL,
  seed INTEGER NOT NULL DEFAULT 0,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  result TEXT NOT NULL DEFAULT '',
  seconds {{FLOAT}} NOT NULL DEFAULT 0,
  stars INTEGER NOT NULL DEFAULT 0,
  data TEXT NOT NULL DEFAULT '{}',
  state TEXT
);
CREATE INDEX idx_game_sessions_team ON game_sessions(team, started_at);
CREATE INDEX idx_game_sessions_room ON game_sessions(room_id);
ALTER TABLE arena_matches ADD COLUMN session_id INTEGER;
-- 每支队伍在每款游戏每一关的最好成绩：星星、纪录（用时或分数）、玩了几次、赢了几次、第一次过关的时间。
CREATE TABLE game_runs (
  game TEXT NOT NULL,
  team TEXT NOT NULL,
  level INTEGER NOT NULL,
  stars INTEGER NOT NULL DEFAULT 0,
  best {{FLOAT}},
  plays INTEGER NOT NULL DEFAULT 0,
  wins INTEGER NOT NULL DEFAULT 0,
  cleared_at TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (game, team, level)
);
-- 旧数据搬过来：单机闯关（arena_levels，一个人一队）、联网游戏的每次过关（mp_runs，两个人一队，纪录都是用时）。
INSERT INTO game_runs(game, team, level, stars, best, plays, wins, cleared_at, updated_at)
  SELECT game, CAST(user_id AS TEXT), level, stars, NULL, plays, wins, cleared_at, updated_at FROM arena_levels;
INSERT INTO game_runs(game, team, level, stars, best, plays, wins, cleared_at, updated_at)
  SELECT game, CAST(a_id AS TEXT) || ',' || CAST(b_id AS TEXT), level, MAX(stars), MIN(seconds), COUNT(*), COUNT(*), MIN(created_at), MAX(created_at)
  FROM mp_runs GROUP BY game, a_id, b_id, level;
INSERT INTO game_sessions(game, level, team, started_at, ended_at, result, seconds, stars, data)
  SELECT game, level, CAST(a_id AS TEXT) || ',' || CAST(b_id AS TEXT), created_at, created_at, 'win', seconds, stars, data FROM mp_runs;
-- 旧的房间是临时的，不搬（升级时正开着的联网房间要重新开）。
DROP TABLE mp_rooms;
DROP TABLE mp_runs;
DROP TABLE arena_levels;
