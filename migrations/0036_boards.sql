-- 0036 打卡榜（docs/DESIGN.md 5.29）：家庭所在城市，存在家长账号上（「省·市」，孩子跟着家长），城市榜按它分。
-- 上不上榜存在孩子的 users.settings.board（家长打开才上，默认不上）。
ALTER TABLE users ADD COLUMN city TEXT NOT NULL DEFAULT '';
