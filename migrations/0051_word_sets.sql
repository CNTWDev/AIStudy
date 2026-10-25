-- 0051 抓词：独立的背单词入口（app/wordpick.py、app/words.py，docs/DESIGN.md §5.58）。
-- word_usage：一个词的用法卡，按学段（band）一份，AI 写一次、所有人复用（和 word_examples 一样）。
--   data：{phrases:[{en,zh}], family:[{w,pos,zh}], confuse:[{w,zh,tip}], use:[{kind,q,options,answer,explain}], icon, hook}
--   family 里的词也是「联想」那一路新词的来源（学了 decide，过些天学 decision）。hook（记忆钩子）只给反复忘的词，用到时才补写。
-- word_sets：抓词的「一组」（一次 8 个词左右）。words：这一组的卡片 id；done_at：做完；reward_match：做完送的那一局跑酷
--   （arena_matches.id，空 = 还没用），这一局只出这一组的词，不算乐园的游戏时间。
CREATE TABLE word_usage (
  id {{ID}},
  word TEXT NOT NULL,
  band TEXT NOT NULL,
  data TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_word_usage ON word_usage(word, band);
CREATE TABLE word_sets (
  id {{ID}},
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  day TEXT NOT NULL,
  cards TEXT NOT NULL DEFAULT '[]',
  summary TEXT NOT NULL DEFAULT '{}',
  done_at TEXT,
  reward_match INTEGER,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_word_sets_user ON word_sets(user_id, day);
