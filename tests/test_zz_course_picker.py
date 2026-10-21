"""选课清单（DESIGN 5.39）：三处表单同一个清单，一门课一行一个开关；有几套教材才有下拉框，资格考试才有考试日期。"""
import re

from fastapi.testclient import TestClient

from app import auth, db
from app.main import app


def _login(c, email, pw="secret1"):
    c.post("/logout")
    c.post("/login", data={"email": email, "password": pw})


def test_course_list_is_one_switch_per_subject():
    with TestClient(app) as c:
        s = auth.create_user("cp-s@x.com", "secret1", "自学", "kid", grade="ADULT")
        db.run("INSERT INTO enrollments(user_id,pack_id,stage,active,track) VALUES(?,?,?,1,?)", s, "ceo-core", "CEO-1", "")
        _login(c, "cp-s@x.com")
        page = c.get("/me/courses").text
        assert "暂不安排" not in page and 'class="cpick-list"' in page
        rows = re.findall(r'<div class="cpick" id="course-(\w+)"', page)
        assert len(rows) == page.count('role="switch"') >= 4
        # 只有一套的课：开关本身就是 subj_ 字段；开着的打勾
        assert re.search(r'checked\s+name="subj_ceo" value="ceo-core"', page)
        assert 'name="subj_psy" value="psy-core"' in page and not re.search(r'checked\s+name="subj_psy"', page)
        # 考试日期只给资格考试；自编课没有
        assert 'name="exam_fund-law"' in page and 'name="exam_ceo-core"' not in page and 'name="exam_psy-core"' not in page
        assert "不确定是哪一版" not in page  # 成人没有教材版本要核对

        pa = auth.create_user("cp-p@x.com", "secret1", "爸爸", "parent")
        kid = auth.create_user("cp-k@x.com", "2580", "小宝", "kid", parent_id=pa, grade="G8")
        db.run("INSERT INTO enrollments(user_id,pack_id,stage,active,track) VALUES(?,?,?,1,?)", kid, "math-shanghai", "G8", "")
        _login(c, "cp-p@x.com")
        for page in (c.get(f"/parent/kids/{kid}/edit").text, c.get("/parent/kids/new").text):
            assert "暂不安排" not in page and 'class="cpick-list"' in page
            # 几套教材的科目：下拉框叫 subj_，没开的下拉框不提交
            assert re.search(r'<select name="subj_math" class="cp-pack"', page)
            assert re.search(r'<select name="subj_physics" class="cp-pack" disabled', page)
        edit = c.get(f"/parent/kids/{kid}/edit").text
        assert re.search(r'<select name="subj_math" class="cp-pack" >', edit) and 'value="math-shanghai" data-start' in edit
