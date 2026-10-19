-- 0038 思辨题（app/discuss.py，docs/DESIGN.md §4.7）：没有标准答案的题不判对错，AI 陪学员把问题想透。
-- 一次思辨一条记录。twist：过几天换一个条件再问时的那个条件（第一次为空）；turns：学员和 AI 的每一轮；
-- moves：这次用到的思考方式 {id: "self" 自己想到 / "prompted" 被追问后想到}；conclusion / action：学员最后写的结论和动作；
-- next_due：下一次换条件再想的日期（没有就为空）。
CREATE TABLE discussions (
  id {{ID}},
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  item_id TEXT NOT NULL,
  kp_id TEXT NOT NULL,
  twist TEXT NOT NULL DEFAULT '',
  turns TEXT NOT NULL DEFAULT '[]',
  moves TEXT NOT NULL DEFAULT '{}',
  conclusion TEXT NOT NULL DEFAULT '',
  action TEXT NOT NULL DEFAULT '',
  done INTEGER NOT NULL DEFAULT 0,
  next_due TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_discussions_user ON discussions(user_id, item_id);
CREATE INDEX idx_discussions_due ON discussions(user_id, next_due);
