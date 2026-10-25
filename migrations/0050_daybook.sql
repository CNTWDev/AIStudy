-- 0050 我的日记（app/daybook.py，docs/DESIGN.md §5.57）：孩子自己的本子，只有本人看得到。
-- 一天一行。data 是加密过的 JSON（心情、几段文字或语音、当天在哪个城市、天气），服务器上的钥匙在 DATA_DIR 里、不进仓库；
-- 直接翻表只看得到「哪天记了」。marked：这天点过心情或写过东西（家长页「这周记了几天」只数这个）；
-- shared：孩子自己点了「给爸爸妈妈看这篇」，家长才能打开这一天，孩子随时能收回。
CREATE TABLE daybook (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  day TEXT NOT NULL,
  data TEXT NOT NULL DEFAULT '',
  marked INTEGER NOT NULL DEFAULT 0,
  shared INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (user_id, day)
);
-- 城市每天的天气（公开数据，同城的人共用，一天查一次）。
CREATE TABLE sky (
  city TEXT NOT NULL,
  day TEXT NOT NULL,
  data TEXT NOT NULL,
  PRIMARY KEY (city, day)
);
