-- 0035 联网房间不只限一家人（docs/DESIGN.md 5.28）：房间分 family（家里）、public（全站公开）、code（房间号 + 4 位密码）。
-- 跨家庭的房间里只显示昵称（users.nickname），真名不出现。
ALTER TABLE game_rooms ADD COLUMN kind TEXT NOT NULL DEFAULT 'family';
ALTER TABLE game_rooms ADD COLUMN pin TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN nickname TEXT NOT NULL DEFAULT '';
CREATE INDEX idx_game_rooms_kind ON game_rooms(game, kind, status);
