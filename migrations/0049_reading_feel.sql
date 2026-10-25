-- 0049 每日短文读完的感觉（docs/每日短文话题多样化-讨论稿.md，app/passage_topics.py）：
-- 'like' 喜欢 / 'meh' 一般 / '' 没选。喜欢过的领域以后多出现一些（兴趣来源），一般的不加权。
ALTER TABLE readings ADD COLUMN feel TEXT NOT NULL DEFAULT '';
