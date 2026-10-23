-- 0047 积少成多（app/growth.py，docs/DESIGN.md §5.50）：只增不减的记录。
-- kind='kp'：每个知识点第一次学会的时候（以后又不稳了也不删：「学会过」只增不减，那个点回到「再练练」）；
-- kind='milestone'：庆祝过的里程碑（ref 如 'answers:1000'），每个只庆祝一次，换设备也不会再放。
-- 已经学会的知识点按掌握表回填（时间用最近一次证据的时间）。
CREATE TABLE gains (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  ref TEXT NOT NULL,
  at TEXT NOT NULL,
  PRIMARY KEY (user_id, kind, ref)
);
INSERT INTO gains(user_id, kind, ref, at)
  SELECT user_id, 'kp', kp_id, COALESCE(updated_at, '') FROM mastery
  WHERE status='mastered' AND user_id IN (SELECT id FROM users);
