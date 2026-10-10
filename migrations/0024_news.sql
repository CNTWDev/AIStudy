-- 0024 每日新闻：每天从新闻源里选一条（小学一条、中学一条），记下来源、选它的理由和候选名单（管理员可以换一篇）。
-- facts 是给 AI 改写用的事实材料（原文摘录），只在服务器上用，不展示给孩子；孩子读的是按级别改写的短文，
-- 存在 contents（kind='news'，topic='news:<id>'，grade=级别 L1–L4），同一级别所有孩子共用，只生成一次。
CREATE TABLE news_picks (
  id {{ID}},
  day TEXT NOT NULL,
  band TEXT NOT NULL,                -- primary 小学 / secondary 中学
  source_id TEXT DEFAULT '',
  source_name TEXT DEFAULT '',
  license TEXT DEFAULT 'copyright',  -- pd / cc-by-nd / copyright
  title TEXT NOT NULL,
  url TEXT DEFAULT '',
  published TEXT DEFAULT '',
  summary TEXT DEFAULT '',
  facts TEXT DEFAULT '',
  why TEXT DEFAULT '',               -- 为什么选它（中文一句）
  topic TEXT DEFAULT '',
  subjects TEXT DEFAULT '[]',        -- 和哪些学科有关
  alts TEXT DEFAULT '[]',            -- 排在后面的候选（换一篇用）
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_news_day_band ON news_picks(day, band);
