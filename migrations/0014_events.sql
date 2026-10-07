-- 0014 学习事件表：所有学习证据（做题、复习卡片、单词摸底、推断、自评、导入）按时间记成一条条事件。
-- 掌握状态（mastery）是「当前学习方式」把这些事件依次算一遍的结果：换学习方式、调参数、升级算法后，
-- 按事件重放就能得到新结果，不丢历史（见 app/evidence.py）。
--   target / target_id：kp + 知识点 id；word + 「词表:单词」；card + 卡片 id
--   kind：answer 作答 / review 复习卡片 / infer 没有作答的推断（value 是给的概率）
--   fmt：交叉验证用的题型（choice / recall / calc / explain / other）
CREATE TABLE events (
  id {{ID}},
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  target TEXT NOT NULL,
  target_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  correct INTEGER,
  value {{FLOAT}},
  weight {{FLOAT}} DEFAULT 1,
  mode TEXT DEFAULT '',
  fmt TEXT DEFAULT '',
  item_id TEXT,
  dont_know INTEGER DEFAULT 0,
  data TEXT DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX idx_events_target ON events(user_id, target, target_id);
-- 掌握状态是哪个模型（含版本和参数）算出来的；和当前学习方式对不上就按事件重算
ALTER TABLE mastery ADD COLUMN model TEXT DEFAULT '';

-- 把已有的记录补进事件表：做过的题（自评「会」的诊断记成推断）
INSERT INTO events(user_id, target, target_id, kind, correct, value, weight, mode, fmt, item_id, dont_know, created_at)
SELECT a.user_id, 'kp', a.kp_id,
       CASE WHEN a.mode = 'diagnose-self' AND a.correct = 1 THEN 'infer' ELSE 'answer' END,
       CASE WHEN a.mode = 'diagnose-self' AND a.correct = 1 THEN NULL ELSE a.correct END,
       CASE WHEN a.mode = 'diagnose-self' AND a.correct = 1 THEN 0.7 ELSE NULL END,
       1, a.mode,
       CASE i.type WHEN 'mcq' THEN 'choice' WHEN 'fill' THEN 'recall' WHEN 'num' THEN 'calc' WHEN 'short' THEN 'explain' ELSE 'other' END,
       a.item_id, COALESCE(a.dont_know, 0), a.created_at
FROM attempts a LEFT JOIN items i ON i.id = a.item_id
ORDER BY a.id;
-- 没有作答记录的掌握度（推断、导入、很早以前的版本留下的）：记成一条推断，保留当时的概率
INSERT INTO events(user_id, target, target_id, kind, value, mode, created_at)
SELECT m.user_id, 'kp', m.kp_id, 'infer', m.score, m.source, COALESCE(m.updated_at, '2026-01-01T00:00:00')
FROM mastery m
WHERE m.score > 0 AND NOT EXISTS (SELECT 1 FROM attempts a WHERE a.user_id = m.user_id AND a.kp_id = m.kp_id);
-- 单词摸底
INSERT INTO events(user_id, target, target_id, kind, correct, mode, fmt, created_at)
SELECT user_id, 'word', kp_id || ':' || ref, 'answer', result, 'probe',
       CASE WHEN reason = 'recheck' THEN 'recall' ELSE 'choice' END, COALESCE(done_at, created_at)
FROM probes WHERE kind = 'word' AND status = 'done';
