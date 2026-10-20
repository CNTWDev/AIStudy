-- 统编语文拆成主干（chn-cn）+ 五四学制目录（chn-tongbian，上海）+ 六三学制目录（chn-tongbian-63，江苏等）。
-- 用江苏学校模板的孩子换到六三学制目录；学习记录按知识点 id 记，不用挪。
UPDATE enrollments SET pack_id = 'chn-tongbian-63'
  WHERE pack_id = 'chn-tongbian'
  AND user_id IN (SELECT id FROM users WHERE preset LIKE 'js-%')
  AND NOT EXISTS (SELECT 1 FROM enrollments x WHERE x.user_id = enrollments.user_id AND x.pack_id = 'chn-tongbian-63');
UPDATE mix_sets SET pack_id = 'chn-tongbian-63'
  WHERE pack_id = 'chn-tongbian'
  AND user_id IN (SELECT id FROM users WHERE preset LIKE 'js-%')
  AND NOT EXISTS (SELECT 1 FROM mix_sets x WHERE x.user_id = mix_sets.user_id AND x.day = mix_sets.day AND x.pack_id = 'chn-tongbian-63');
