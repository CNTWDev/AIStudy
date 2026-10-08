-- 0020 题库自己长、自己把关（见 app/bankflow.py）：
-- 同型题分组（换了数字、说法稍有不同的同一道题算一组）、AI 独立校对答案、按真实作答校准难度（1-5 级）、
-- 家里拍的卷子改编成新题进公共题库（原卷仍只给自己家）。
ALTER TABLE items ADD COLUMN near_key TEXT;
ALTER TABLE items ADD COLUMN verified INTEGER DEFAULT 0;
ALTER TABLE items ADD COLUMN verify_note TEXT DEFAULT '';
ALTER TABLE items ADD COLUMN b {{FLOAT}};
ALTER TABLE items ADD COLUMN level INTEGER;
ALTER TABLE items ADD COLUMN variant_of TEXT;
ALTER TABLE items ADD COLUMN contributor_id INTEGER;
CREATE INDEX idx_items_near ON items(near_key);
CREATE INDEX idx_items_verified ON items(verified, status);
CREATE INDEX idx_items_variant ON items(variant_of);
-- 家长导入试卷时可以选择：题目改编后帮助别的孩子（默认同意；原卷和孩子的作答都不公开）
ALTER TABLE papers ADD COLUMN shared INTEGER DEFAULT 1;
-- 后台任务：多个应用进程时只有一个在跑（租约），并记下上次跑的结果
CREATE TABLE jobs (
  name TEXT PRIMARY KEY,
  locked_until TEXT DEFAULT '',
  last_run TEXT DEFAULT '',
  last_result TEXT DEFAULT '{}',
  day TEXT DEFAULT '',
  ai_calls INTEGER DEFAULT 0
);
