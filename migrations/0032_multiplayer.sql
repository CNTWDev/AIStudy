-- 0032 联网游戏（docs/DESIGN.md 5.24）：同一个家庭的孩子各用自己的设备进一个房间一起玩（双人闯关、泡泡堂、贪吃蛇……）。
-- 房间：家里一个孩子开房间，兄弟姐妹加入；实时消息走 WebSocket（app/arena/mp/bus.py），这里只记谁在哪个房间、玩哪一关。
CREATE TABLE mp_rooms (
  id {{ID}},
  game TEXT NOT NULL,
  family_id INTEGER NOT NULL,
  host_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  guest_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  host_role TEXT NOT NULL DEFAULT '',
  level INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'open',
  created_at TEXT NOT NULL,
  seen_at TEXT NOT NULL
);
CREATE INDEX idx_mp_rooms_family ON mp_rooms(family_id, status);
-- 每次过关一条。队伍 = 两个孩子（a_id < b_id，谁当什么角色都算同一队），用来算队伍纪录、星星、开下一关。
CREATE TABLE mp_runs (
  id {{ID}},
  game TEXT NOT NULL,
  room_id INTEGER,
  a_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  b_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  level INTEGER NOT NULL,
  seconds {{FLOAT}} NOT NULL,
  deaths INTEGER DEFAULT 0,
  score INTEGER DEFAULT 0,
  stars INTEGER DEFAULT 0,
  data TEXT DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX idx_mp_runs_team ON mp_runs(game, a_id, b_id, level);
