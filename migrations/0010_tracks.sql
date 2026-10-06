-- 0010 教材体系分层：每门课可以选「方向」（如剑桥英语 0511 / 0510 / 0500、IGCSE Core / Extended）；
-- 孩子记下学校类型和所用的学校模板（上海公办初中、国际学校剑桥路线……），用来预填教材
ALTER TABLE enrollments ADD COLUMN track TEXT DEFAULT '';
ALTER TABLE users ADD COLUMN school_type TEXT DEFAULT '';
ALTER TABLE users ADD COLUMN preset TEXT DEFAULT '';
