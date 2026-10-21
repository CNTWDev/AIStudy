-- 0045 单词模块（app/words.py，docs/DESIGN.md §5.41）。
-- word_packs：学习者挂着的单词包。按年级、教材自动挂的包也有一行（state='auto'，用的时候才建），记每天加新词的日子；
--   家长加上的 state='on'，停掉的 state='off'。per_day：每天几个新词（空 = 按学段的默认值）；last_day：最近一次加新词的日子。
-- word_examples：单词例句，按学段（band：primary / secondary / adult）一个词一份，AI 写一次、所有人复用。
--   sentences：[{en, zh}]，en 里这个词用 [[ ]] 标出来（句子补洞就是把它挖掉）。
-- 原来的「每天新词」（tracks.kind='words'，家长选一张词表、每天按顺序加 N 个）搬进 word_packs，原来的那一行停用。
CREATE TABLE word_packs (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  pack_id TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'auto',
  per_day INTEGER,
  last_day TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  PRIMARY KEY (user_id, pack_id)
);
CREATE TABLE word_examples (
  id {{ID}},
  word TEXT NOT NULL,
  band TEXT NOT NULL,
  sentences TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_word_examples ON word_examples(word, band);
INSERT INTO word_packs(user_id, pack_id, state, per_day, last_day, created_at)
  SELECT user_id, ref, 'on', daily_amount, COALESCE(last_day, ''), created_at FROM tracks WHERE kind='words' AND active=1 AND ref<>'';
UPDATE tracks SET active=0 WHERE kind='words';
