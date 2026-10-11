-- 0027 英文短文 / 新闻可以「先听后读」：只听不看原文做小题，交卷后再看原文。
-- mode：read（看着原文做）/ listen（交卷时原文还没打开过，练的是听力）。以后比较「听得懂」和「读得懂」用。
ALTER TABLE readings ADD COLUMN mode TEXT NOT NULL DEFAULT 'read';
