-- 0031 学校进度按校历自动推演 + 每门课一组混合练习（docs/DESIGN.md 5.23）。
-- kp_taught.source：manual（孩子 / 家长报的、试卷考到的）/ inferred（按校历推演的，每天重算，纠正后会撤回）。
ALTER TABLE kp_taught ADD COLUMN source TEXT DEFAULT 'manual';
-- 家长纠正「学校比估计的快 / 慢几章」：只在这一学期有效（progress_shift_term = 学年-学期，换学期自动归零）。
ALTER TABLE enrollments ADD COLUMN progress_shift INTEGER DEFAULT 0;
ALTER TABLE enrollments ADD COLUMN progress_shift_term TEXT DEFAULT '';
-- 混合练习：一个孩子一天一门课一组。slots：要出的题位（知识点、成分、作答模式），pos：出到第几个，served：已经出过的题 id。
CREATE TABLE mix_sets (
  id {{ID}},
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  day TEXT NOT NULL,
  pack_id TEXT NOT NULL,
  slots TEXT DEFAULT '[]',
  pos INTEGER DEFAULT 0,
  served TEXT DEFAULT '[]',
  right_n INTEGER DEFAULT 0,
  answered INTEGER DEFAULT 0,
  done_at TEXT,
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_mix_sets_day ON mix_sets(user_id, day, pack_id);
