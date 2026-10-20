-- 苏州四上试点包（math-sujiao-g4-pilot）并回数学主干：它的 5 个主题点在主干里都已经有了，
-- 学习记录挪到主干的知识点上；以后再有知识点合并，也在 kp_renames 里记一笔（知识点 id 本身仍然永不修改）。
CREATE TABLE kp_renames (
  old_id TEXT PRIMARY KEY,
  new_id TEXT NOT NULL,
  note TEXT DEFAULT '',
  created_at TEXT NOT NULL
);
INSERT INTO kp_renames(old_id, new_id, note, created_at) VALUES
  ('SZ-M4-PERIMETER', 'MSH-GEO-09', '苏州试点并回数学主干', '2026-10-20'),
  ('SZ-M4-AREA', 'MSH-GEO-10', '苏州试点并回数学主干', '2026-10-20'),
  ('SZ-M4-LARGE-NUMBER', 'MSH-NUM-23', '苏州试点并回数学主干', '2026-10-20'),
  ('SZ-M4-QUANTITY', 'MSH-APP-05', '苏州试点并回数学主干', '2026-10-20'),
  ('SZ-M4-LARGE-CALC', 'MSH-NUM-24', '苏州试点并回数学主干', '2026-10-20');

UPDATE events SET target_id = (SELECT new_id FROM kp_renames WHERE old_id = events.target_id)
  WHERE target = 'kp' AND target_id IN (SELECT old_id FROM kp_renames);
UPDATE attempts SET kp_id = (SELECT new_id FROM kp_renames WHERE old_id = attempts.kp_id) WHERE kp_id IN (SELECT old_id FROM kp_renames);
UPDATE cards SET kp_id = (SELECT new_id FROM kp_renames WHERE old_id = cards.kp_id) WHERE kp_id IN (SELECT old_id FROM kp_renames);
UPDATE items SET kp_id = (SELECT new_id FROM kp_renames WHERE old_id = items.kp_id) WHERE kp_id IN (SELECT old_id FROM kp_renames);
UPDATE items SET kp_ids = REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(kp_ids,
  '"SZ-M4-PERIMETER"', '"MSH-GEO-09"'), '"SZ-M4-AREA"', '"MSH-GEO-10"'), '"SZ-M4-LARGE-NUMBER"', '"MSH-NUM-23"'),
  '"SZ-M4-QUANTITY"', '"MSH-APP-05"'), '"SZ-M4-LARGE-CALC"', '"MSH-NUM-24"')
  WHERE kp_ids LIKE '%SZ-M4-%';
UPDATE paper_items SET kp_id = (SELECT new_id FROM kp_renames WHERE old_id = paper_items.kp_id) WHERE kp_id IN (SELECT old_id FROM kp_renames);
UPDATE ask_threads SET kp_id = (SELECT new_id FROM kp_renames WHERE old_id = ask_threads.kp_id) WHERE kp_id IN (SELECT old_id FROM kp_renames);
UPDATE insights SET kp_id = (SELECT new_id FROM kp_renames WHERE old_id = insights.kp_id) WHERE kp_id IN (SELECT old_id FROM kp_renames);
UPDATE probes SET kp_id = (SELECT new_id FROM kp_renames WHERE old_id = probes.kp_id) WHERE kp_id IN (SELECT old_id FROM kp_renames);
UPDATE arena_answers SET kp_id = (SELECT new_id FROM kp_renames WHERE old_id = arena_answers.kp_id) WHERE kp_id IN (SELECT old_id FROM kp_renames);
UPDATE traces SET kp_id = (SELECT new_id FROM kp_renames WHERE old_id = traces.kp_id) WHERE kp_id IN (SELECT old_id FROM kp_renames);
UPDATE open_reviews SET kp_id = (SELECT new_id FROM kp_renames WHERE old_id = open_reviews.kp_id) WHERE kp_id IN (SELECT old_id FROM kp_renames);
UPDATE discussions SET kp_id = (SELECT new_id FROM kp_renames WHERE old_id = discussions.kp_id) WHERE kp_id IN (SELECT old_id FROM kp_renames);
UPDATE enrollments SET progress_kp = (SELECT new_id FROM kp_renames WHERE old_id = enrollments.progress_kp) WHERE progress_kp IN (SELECT old_id FROM kp_renames);
-- 讲解和导入是按试点的粗主题写的：退役，主干的知识点用自己的
UPDATE contents SET status = 'retired' WHERE kp_id IN (SELECT old_id FROM kp_renames);

-- 一人一行的表：主干的点已经有记录就留主干的
UPDATE item_kps SET kp_id = (SELECT new_id FROM kp_renames WHERE old_id = item_kps.kp_id)
  WHERE kp_id IN (SELECT old_id FROM kp_renames)
  AND NOT EXISTS (SELECT 1 FROM item_kps x WHERE x.item_id = item_kps.item_id AND x.kp_id = (SELECT new_id FROM kp_renames WHERE old_id = item_kps.kp_id));
DELETE FROM item_kps WHERE kp_id IN (SELECT old_id FROM kp_renames);
UPDATE kp_taught SET kp_id = (SELECT new_id FROM kp_renames WHERE old_id = kp_taught.kp_id)
  WHERE kp_id IN (SELECT old_id FROM kp_renames)
  AND NOT EXISTS (SELECT 1 FROM kp_taught x WHERE x.user_id = kp_taught.user_id AND x.kp_id = (SELECT new_id FROM kp_renames WHERE old_id = kp_taught.kp_id));
DELETE FROM kp_taught WHERE kp_id IN (SELECT old_id FROM kp_renames);
UPDATE lights SET kp_id = (SELECT new_id FROM kp_renames WHERE old_id = lights.kp_id)
  WHERE kp_id IN (SELECT old_id FROM kp_renames)
  AND NOT EXISTS (SELECT 1 FROM lights x WHERE x.user_id = lights.user_id AND x.kp_id = (SELECT new_id FROM kp_renames WHERE old_id = lights.kp_id));
DELETE FROM lights WHERE kp_id IN (SELECT old_id FROM kp_renames);
UPDATE arena_ability SET kp_id = (SELECT new_id FROM kp_renames WHERE old_id = arena_ability.kp_id)
  WHERE kp_id IN (SELECT old_id FROM kp_renames)
  AND NOT EXISTS (SELECT 1 FROM arena_ability x WHERE x.user_id = arena_ability.user_id AND x.kp_id = (SELECT new_id FROM kp_renames WHERE old_id = arena_ability.kp_id));
DELETE FROM arena_ability WHERE kp_id IN (SELECT old_id FROM kp_renames);
-- 掌握度按学习事件重算（概念变了，下次打开会自动重放）
DELETE FROM mastery WHERE kp_id IN (SELECT old_id FROM kp_renames);
DELETE FROM concept_mastery WHERE concept_id IN ('math-core-perimeter', 'math-core-area', 'math-core-large-number')
  OR concept_id IN (SELECT 'kp:' || old_id FROM kp_renames);

-- 选过试点包的孩子：换成苏教版数学目录（已经选了就只留一条）
UPDATE enrollments SET pack_id = 'math-sujiao', stage = 'G4'
  WHERE pack_id = 'math-sujiao-g4-pilot'
  AND NOT EXISTS (SELECT 1 FROM enrollments x WHERE x.user_id = enrollments.user_id AND x.pack_id = 'math-sujiao');
DELETE FROM enrollments WHERE pack_id = 'math-sujiao-g4-pilot';
UPDATE mix_sets SET pack_id = 'math-sujiao' WHERE pack_id = 'math-sujiao-g4-pilot'
  AND NOT EXISTS (SELECT 1 FROM mix_sets x WHERE x.user_id = mix_sets.user_id AND x.day = mix_sets.day AND x.pack_id = 'math-sujiao');
DELETE FROM mix_sets WHERE pack_id = 'math-sujiao-g4-pilot';
