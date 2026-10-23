-- 0046 学过的课（app/lessons.py，docs/DESIGN.md §5.46）：学习者在学习页把一课走完（点「完成这一项」）记一行。
-- first_done：第一次学完的日子；last_done：最近一次（学完或回来复习完）的日子；n：一共几次。
-- 成人课学过的课不再当新课排：回来时不再看一遍讲解，改成先回想、再看一个新案例、最后联系自己（间隔 3 / 7 / 14 天）。
CREATE TABLE lessons (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kp_id TEXT NOT NULL,
  first_done TEXT NOT NULL,
  last_done TEXT NOT NULL,
  n INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (user_id, kp_id)
);
