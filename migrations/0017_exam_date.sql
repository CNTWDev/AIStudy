-- 0017 考试日期：每门课可以设一个考试日期（成人备考职业资格考试、IGCSE 大考……），计划按剩余天数把没学的内容排完，
-- 最后两周留给冲刺复习（见 app/plan.py 的 src_exam）。空 = 没有考试日期，按学校进度走。
ALTER TABLE enrollments ADD COLUMN exam_date TEXT DEFAULT '';
