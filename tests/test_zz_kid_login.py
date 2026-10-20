"""孩子用登录名 + 4 位数字登录（docs/DESIGN.md §5.37）：家长添加时起登录名、设数字，不用邮箱；
登录过的设备记住孩子，登录页点头像输数字；数字连错 5 次一直锁着，家长解锁。"""
from fastapi.testclient import TestClient

from app import auth, db
from app.main import app


def _parent(c, tag):
    pid = auth.create_user(f"kl-p-{tag}@x.com", "secret1", "家长", "parent")
    c.cookies.clear()
    c.post("/login", data={"email": f"kl-p-{tag}@x.com", "password": "secret1"})
    return pid


def test_parent_adds_kid_without_email():
    with TestClient(app) as c:
        _parent(c, "a")
        page = c.get("/parent/kids/new").text
        # 添加时只问名字；登录名按名字自动起，写在登录卡上（编辑页能改）
        assert 'name="login"' not in page and "4 位数字" in page and 'name="email"' not in page
        assert c.get("/parent/kids/login-suggest", params={"name": "果 果"}).json() == {"login": "果果"}
        # 太好猜的数字、不是 4 位的都不行；表单保留填过的内容
        r = c.post("/parent/kids/save", data={"name": "果果", "login": "果果", "password": "1234", "grade": "G8"})
        assert "太好猜" in r.text and 'value="果果"' in r.text
        assert "4 位数字" in c.post("/parent/kids/save", data={"name": "果果", "login": "果果", "password": "abcdef", "grade": "G8"}).text
        r = c.post("/parent/kids/save", data={"name": "果果", "login": "果果", "password": "2580", "grade": "G8"})
        k = auth.by_login("果果")
        assert k and k["email"] is None and auth.uses_pin(k)
        # 登录卡：登录名和数字只显示这一次
        assert "果果 的账号开好了" in r.text and "2580" in r.text and "打印这张卡" in r.text
        assert "登录名 果果" in c.get("/parent").text
        # 登录名重名：提示换一个，建议名字后面加数字
        r = c.post("/parent/kids/save", data={"name": "果果", "login": "果果", "password": "2580", "grade": "G3"})
        assert "已经有人用了" in r.text
        assert auth.suggest_login("果果") == "果果2"
        # 家里的成人成员：普通密码
        assert "至少" in c.post("/parent/kids/save", data={"name": "妈妈", "login": "klmama", "password": "2580", "grade": "ADULT"}).text
        c.post("/parent/kids/save", data={"name": "妈妈", "login": "klmama", "password": "secret1", "grade": "ADULT"})
        assert not auth.uses_pin(auth.by_login("klmama"))


def test_kid_logs_in_and_switches_on_shared_device():
    with TestClient(app) as c:
        pid = _parent(c, "b")
        for name, login, pin in (("姐姐", "klj", "2580"), ("弟弟", "kld", "3691")):
            c.post("/parent/kids/save", data={"name": name, "login": login, "password": pin, "grade": "G4"})
        c.get("/logout")
        # 第一次：登录名 + 4 位数字（登录名不分大小写）
        assert c.post("/login", data={"email": "KLJ", "password": "2580"}).url.path == "/today"
        c.get("/logout")
        c.post("/login", data={"email": "kld", "password": "3691"})
        c.get("/logout")
        # 这台设备记住了两个孩子：登录页先显示头像，大人从小字进普通登录页
        page = c.get("/login").text
        assert "是谁来了" in page and "姐姐" in page and "弟弟" in page and 'data-skin="kid"' in page
        assert "邮箱或登录名" in c.get("/login?full=1").text
        jj, dd = auth.by_login("klj"), auth.by_login("kld")
        r = c.post("/login/pick", data={"uid": jj["id"], "password": "1111"})
        assert "数字不对" in r.text and f'data-pick="{jj["id"]}"' in r.text
        assert c.post("/login/pick", data={"uid": jj["id"], "password": "2580"}).url.path == "/today"
        c.get("/logout")
        # 别的设备上的孩子：点不了
        other = auth.create_user(None, "4826", "别家", "kid", parent_id=pid, grade="G3", login="klother")
        assert c.post("/login/pick", data={"uid": other, "password": "4826"}).url.path == "/login"
        # 连错 5 次：一直锁着（不是 15 分钟），家长解锁
        for _ in range(5):
            r = c.post("/login/pick", data={"uid": dd["id"], "password": "0000"})
        assert "解除锁定" in r.text and 'class="kl-pad"' not in r.text
        assert auth.get_user(dd["id"])["locked_until"] == auth.PIN_LOCKED
        assert "解除锁定" in c.post("/login", data={"email": "kld", "password": "3691"}).text   # 对的数字也进不去
        c.post("/login", data={"email": "kl-p-b@x.com", "password": "secret1"})
        assert "锁住了" in c.get(f"/parent/kids/{dd['id']}/edit").text
        c.post(f"/parent/kids/{dd['id']}/account", data={"action": "unlock"})
        c.get("/logout")
        assert c.post("/login/pick", data={"uid": dd["id"], "password": "3691"}).url.path == "/today"
        c.get("/logout")
        # 不在这台设备上显示
        c.post("/login/forget", data={"uid": dd["id"]})
        page = c.get("/login").text
        assert "弟弟" not in page and "姐姐" in page
        # 大人也在列表里（像电脑的切换用户）：点了输密码
        assert "家长" in page
        par = auth.by_email("kl-p-b@x.com")
        assert "账号或密码不对" in c.post("/login/pick", data={"uid": par["id"], "password": "nope"}).text
        assert c.post("/login/pick", data={"uid": par["id"], "password": "secret1"}).url.path == "/parent"


def test_kid_changes_own_pin_and_old_kids_keep_working():
    with TestClient(app) as c:
        pid = _parent(c, "c")
        c.post("/parent/kids/save", data={"name": "小丙", "login": "klc", "password": "2580", "grade": "G3"})
        c.get("/logout")
        c.post("/login", data={"email": "klc", "password": "2580"})
        assert "换我的 4 位数字" in c.get("/settings").text
        assert "4 位数字" in c.post("/password", data={"old": "2580", "new": "secret1"}).text
        c.post("/password", data={"old": "2580", "new": "4719"})
        assert auth.check_pw("4719", auth.by_login("klc")["pw_hash"])
        c.get("/logout")
        # 以前用邮箱和长密码的孩子照样能登录（家长改成数字以前）
        old = db.insert("INSERT INTO users(email,login,pw_hash,name,role,parent_id,grade,status,created_at) "
                        "VALUES('kl-old@x.com','klold',?,'老账号','kid',?,'G5','active',?)", auth.hash_pw("oldpass1"), pid, db.now())
        assert c.post("/login", data={"email": "kl-old@x.com", "password": "oldpass1"}).url.path == "/today"
        c.get("/logout")
        assert c.post("/login", data={"email": "klold", "password": "oldpass1"}).url.path == "/today"
        assert auth.get_user(old)["login"] == "klold"


def test_add_kid_errors_stay_on_the_form():
    """填错了回到表单上提示，不先建账号，也不把 JSON 原样甩给浏览器（Safari 显示成乱码）。"""
    with TestClient(app) as c:
        _parent(c, "err")
        base = {"name": "小雨", "login": "klxy", "password": "2580", "grade": "G3", "daily_minutes": "60",
                "game_minutes": "20", "game_unlock": "half", "nickname": "小雨", "private": "", "province": "", "city": "",
                "school": "", "preset": "", "method": "", "subj_math": "", "subj_english": ""}
        r = c.post("/parent/kids/save", data=base)
        assert r.status_code == 200 and "text/html" in r.headers["content-type"]
        assert "昵称别用自己的名字" in r.text and 'value="小雨"' in r.text
        assert auth.by_login("klxy") is None  # 账号没建
        r = c.post("/parent/kids/save", data=dict(base, nickname="闪电小队长"))
        assert "小雨 的账号开好了" in r.text
        # 别的表单提交出错：浏览器拿到一个页面，页面脚本拿到 JSON
        kid = auth.by_login("klxy")
        r = c.post(f"/parent/kids/{kid['id']}/tracks", data={}, headers={"accept": "text/html,application/xhtml+xml"})
        assert "text/html" in r.headers["content-type"] and "<h1>" in r.text
        assert c.post(f"/parent/kids/{kid['id']}/tracks", data={}).headers["content-type"].startswith("application/json")
        # 学习安排没选词表：回到安排页，在上面提示
        r = c.post(f"/parent/kids/{kid['id']}/tracks", data={"kind": "words", "ref": ""})
        assert r.url.path.endswith("/plan") and "请选择词表" in r.text


def test_add_kid_short_form_defaults_courses():
    """添加孩子只问名字、年级、在哪上学、4 位数字；课按 地区 + 学校类型 + 年级 自动排好（DESIGN §5.38）。"""
    from app.catalog import catalog
    with TestClient(app) as c:
        pid = _parent(c, "short")
        page = c.get("/parent/kids/new").text
        assert 'name="region"' in page and 'name="school_type"' in page and 'name="school"' not in page
        assert 'name="nickname"' not in page and 'name="game_minutes"' not in page and 'name="email"' not in page
        r = c.get("/parent/kids/defaults", params={"region": "江苏", "school_type": "public", "grade": "G3"}).json()
        assert r["preset"] == "js-public-primary" and [x["pack"] for x in r["courses"]] == ["chn-tongbian-63", "math-sujiao", "eng-yilin"]
        assert c.get("/parent/kids/defaults", params={"region": "江苏", "school_type": "private", "grade": "G3"}).json()["preset"] == "js-public-primary"
        assert c.get("/parent/kids/defaults", params={"region": "其它", "school_type": "international", "grade": "G8"}).json()["preset"] == "intl-cambridge"
        other = c.get("/parent/kids/defaults", params={"region": "其它", "school_type": "public", "grade": "G3"}).json()
        assert other["preset"] == "" and [x["pack"] for x in other["courses"]] == ["chn-tongbian"] and "统编语文" in other["title"]
        # 页面脚本按默认填好选择框后提交；服务器自己重新匹配模板（不信页面传来的），记下学校类型和省份
        _, courses = catalog.default_courses("上海", "public", "G3")
        data = {"name": "小树", "password": "3691", "grade": "G3", "region": "上海", "school_type": "public",
                "preset": "intl-cambridge", **{f"subj_{x['subject']}": x["pack"] for x in courses}}
        assert "小树 的账号开好了" in c.post("/parent/kids/save", data=data).text
        k = auth.by_login("小树")
        assert k["preset"] == "sh-public-primary" and k["school_type"] == "public" and k["daily_minutes"] == 40
        assert sorted(e["pack_id"] for e in db.q("SELECT pack_id FROM enrollments WHERE user_id=? AND active=1", k["id"])) == \
            sorted(x["pack"] for x in courses)
        assert auth.get_user(pid)["city"] == "上海"
        # 再加一个：地区按家里记下的省份预选好，课也预填好
        page = c.get("/parent/kids/new").text
        assert 'value="上海" checked' in page and "按上海公办小学" in page
        # 填错：留在表单上，选好的课和地区都保留
        r = c.post("/parent/kids/save", data={**data, "name": "小树苗", "login": "", "password": "1234", "subj_math": ""})
        assert "太好猜" in r.text and 'value="小树苗"' in r.text and 'value="上海" checked' in r.text and 'value="math-shanghai" selected' not in r.text
