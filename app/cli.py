"""命令行工具（install.sh 也通过它做迁移和检测）。用法：

  python -m app.cli migrate                         执行数据库迁移（升级后必须执行，install.sh 会自动执行）
  python -m app.cli migrate-status                  查看迁移状态
  python -m app.cli check [--llm]                   检测数据库、迁移、教材、AI 配置；--llm 会真实调用一次 AI
  python -m app.cli check-curricula                 只校验教材数据（学段、教材包、方向、跨教材关联、学校模板），不需要数据库
  python -m app.cli create-admin 邮箱 密码 [称呼]     创建管理员（或把已有家长账号设为管理员并重设密码）
  python -m app.cli reset-password 邮箱 新密码        重设任何账号的密码，并让它在所有设备上退出
  python -m app.cli unlock 邮箱                       解除密码输错导致的锁定
  python -m app.cli invite [次数] [天数] [备注]        生成注册邀请码
  python -m app.cli mark-weak 孩子邮箱 知识点ID ...    导入已知薄弱点（如以前的试卷分析）
  python -m app.cli bank-maintain                    手动跑一轮题库流水线（改编入库、校对答案、校准难度；平时后台自动跑）
  python -m app.cli backup [目标目录]                 备份数据库（PostgreSQL 用 pg_dump）
  python -m app.cli tts-test [文字]                  朗读：不经过缓存真实调用一次，打印配置和厂商返回的原始报错
  python -m app.cli library fetch [--all] [书的id ...] 下载书库的公版原文（默认只下还没有的、坏了的；--all 全部重新下载）
  python -m app.cli library rebuild                    按新的整理规则重新整理已下载的书（不重新下载）
  python -m app.cli news [--sources]                 每日新闻：马上抓取、精选、改写今天的新闻（平时后台每天早上自动跑）；--sources 只测试各新闻源能不能访问
"""
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import auth, config, db, engine, llm, migrate
from .catalog import catalog


def _ok(flag: bool, text: str) -> bool:
    print(("  [ OK ] " if flag else "  [FAIL] ") + text)
    return flag


def check(with_llm: bool) -> int:
    good = True
    print("beejoy 自检")
    try:
        db.one("SELECT 1 AS ok")
        _ok(True, f"数据库连接正常（{db.DIALECT}）")
    except Exception as e:  # noqa: BLE001
        _ok(False, f"数据库连不上：{e}")
        return 1
    pend = migrate.pending()
    good &= _ok(not pend, "数据库结构是最新的" if not pend else f"有 {len(pend)} 个迁移未执行：{', '.join(pend)}（运行 migrate）")
    catalog.load()
    good &= _ok(len(catalog.kps) > 0, f"教材包 {len(catalog.packs)} 个，知识点 {len(catalog.kps)} 个")
    good &= _ok(not catalog.errors, "教材数据校验通过" if not catalog.errors else f"教材数据有 {len(catalog.errors)} 个问题（运行 check-curricula 查看）")
    if config.SECRET_KEY in ("", "dev-secret-change-me") or "请改" in config.SECRET_KEY:
        good &= _ok(False, "SECRET_KEY 还是默认值，请在 .env 里改成随机字符串")
    else:
        _ok(True, "SECRET_KEY 已设置")
    if not pend:
        n_admin = db.one("SELECT COUNT(*) AS n FROM users WHERE is_admin=1")["n"]
        print("  [INFO] " + (f"管理员账号 {n_admin} 个" if n_admin else "还没有账号：打开网站，第一个注册的账号会成为管理员"))
    s = llm.settings()
    ok, why = llm.check()
    if s.default == "none" and not s.error:
        print("  [INFO] AI 未开启（config/llm.toml 里 default = \"none\"），查词/讲解/AI 出题不可用，其余功能正常")
    else:
        if ok:
            _ok(True, f"AI 配置：{why}")
        else:
            print(f"  [WARN] AI 还不能用：{why}（其余功能正常；填好 config/llm.toml 后重启服务）")
        if ok and with_llm:
            try:
                out = llm.ping()
                _ok(True, f"AI 实际调用成功：{out}")
            except llm.LLMError as e:
                good &= _ok(False, f"AI 实际调用失败：{e}")
    from . import tts_web
    ok, why = tts_web.check()
    print(f"  [{' OK ' if ok else 'INFO'}] 朗读：{why}" + ("" if ok else "（用浏览器自带的声音）"))
    print("全部正常" if good else "有问题需要处理（见上面 [FAIL]）")
    return 0 if good else 1


def tts_test(text: str) -> int:
    from . import tts_web
    svc = tts_web.service()
    s = svc.s
    key = s.api_key
    print(f"配置文件：{s.source or tts_web.TTS_CONFIG_FILE}")
    print(f"提供方：{s.provider}  模型：{s.model}  API 版本：{s.api_version or '(默认)'}")
    print(f"api_key：{(key[:8] + '…' + key[-4:]) if len(key) > 12 else ('(空)' if not key else '(太短)')}")
    print(f"声音：{s.voices or '(没配)'}  语速：{s.speeds}")
    if not svc.ready:
        print(f"[FAIL] 还不能用：{svc.problem or '没开启'}")
        return 1
    lang = "zh" if any("\u4e00" <= ch <= "\u9fff" for ch in text) else "en"
    voice = s.voice(lang)
    if not voice:
        print(f"[FAIL] 没给 {lang} 配声音（[voices] 里 {lang} = \"声音 id\"）")
        return 1
    try:
        n = sum(len(c) for c in svc.provider.stream(text, lang=lang, voice=voice, speed=s.speed("sentence")))
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] 调用失败：{e}")
        return 1
    print(f"[ OK ] 生成成功：{n} 字节音频（{lang}，声音 {voice}）")
    try:
        tts_web.TTS_DIR.mkdir(parents=True, exist_ok=True)
        probe = tts_web.TTS_DIR / ".write-test"
        probe.write_text("ok"); probe.unlink()
        print(f"[ OK ] 缓存目录可写：{tts_web.TTS_DIR}")
    except OSError as e:
        print(f"[FAIL] 缓存目录写不进去：{tts_web.TTS_DIR}（{e}）")
        return 1
    return 0


def backup(target: Path) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    if db.IS_PG:
        dst = target / f"aistudy-{stamp}.sql.gz"
        u = urlparse(config.DATABASE_URL)
        env = dict(os.environ, PGPASSWORD=unquote(u.password or ""))
        if not shutil.which("pg_dump"):
            raise SystemExit("找不到 pg_dump，请安装 postgresql-client")
        cmd = ["pg_dump", "--no-owner", "-h", u.hostname or "127.0.0.1", "-p", str(u.port or 5432),
               "-U", unquote(u.username or ""), (u.path or "/").lstrip("/")]
        with open(dst, "wb") as out:
            p1 = subprocess.Popen(cmd, stdout=subprocess.PIPE, env=env)
            p2 = subprocess.run(["gzip", "-c"], stdin=p1.stdout, stdout=out)
            p1.stdout.close()
            if p1.wait() != 0 or p2.returncode != 0:
                dst.unlink(missing_ok=True)
                raise SystemExit("pg_dump 失败")
    else:
        import sqlite3
        dst = target / f"aistudy-{stamp}.db"
        with sqlite3.connect(dst) as out, sqlite3.connect(db._TARGET) as src:
            src.backup(out)
    return dst


def check_curricula() -> int:
    """教材数据校验：知识点 id 唯一、学段已定义、前置和关联都能找到、方向合法、学校模板引用的教材存在。"""
    catalog.load()
    print(f"学段 {len(catalog.stages)} 个，教材包 {len(catalog.packs)} 个，知识点 {len(catalog.kps)} 个，"
          f"概念 {len(catalog.concepts)} 个，关联 {len(catalog.links)} 条，学校模板 {len(catalog.presets)} 个")
    for p in catalog.packs.values():
        tr = f"，方向：{' / '.join(t['id'] for t in p.tracks)}" if p.tracks else ""
        print(f"  {p.id:16} {p.subject_name} · {p.edition}（{len(p.kp_ids)} 个点{tr}）")
    for e in catalog.errors:
        print("  [FAIL]", e)
    print("校验通过" if not catalog.errors else f"有 {len(catalog.errors)} 个问题")
    return 1 if catalog.errors else 0


def main(argv) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0 if argv else 1
    cmd, args = argv[0], argv[1:]
    if cmd == "migrate":
        done = migrate.upgrade(verbose=True)
        print("已执行 %d 个迁移" % len(done) if done else "数据库已是最新，无需迁移")
        return 0
    if cmd == "migrate-status":
        for s in migrate.status():
            print(f"  {'已执行 ' + s['applied_at'] if s['applied_at'] else '待执行'}  {s['name']}")
        return 0
    if cmd == "check":
        return check("--llm" in args)
    if cmd == "check-curricula":
        return check_curricula()
    if cmd == "tts-test":
        return tts_test(" ".join(args) or "Hello, this is a test.")
    if cmd == "library":
        from .library import library
        if args and args[0] == "rebuild":
            ok, bad = library.rebuild_all()
            print(f"书库：重新整理 {ok} 本" + (f"，失败 {bad} 本" if bad else ""))
            return 0
        if not args or args[0] != "fetch":
            print("用法：python -m app.cli library fetch [--all] [书的id ...] | library rebuild")
            return 2
        ids = [a for a in args[1:] if not a.startswith("--")] or None
        ok, bad = library.fetch_all(only_missing="--all" not in args and not ids, ids=ids)
        print(f"书库：下载成功 {ok} 本，失败 {bad} 本" + ("（失败的可以在管理后台「书库」上传 txt）" if bad else ""))
        return 0
    if cmd == "backup":
        print("已备份到", backup(Path(args[0]) if args else config.DATA_DIR / "backups"))
        return 0

    migrate.upgrade()
    if cmd == "create-admin":
        email, pw, name = args[0], args[1], (args[2] if len(args) > 2 else "管理员")
        u = auth.by_email(email)
        if u:
            if u["role"] != "parent":
                print("这个邮箱是孩子账号，不能设为管理员"); return 1
            auth.set_password(u["id"], pw)
            db.run("UPDATE users SET is_admin=1, status='active' WHERE id=?", u["id"])
            print("已把", email, "设为管理员并重设密码")
        else:
            auth.create_user(email, pw, name, "parent", is_admin=True)
            print("已创建管理员", email)
    elif cmd == "reset-password":
        u = auth.by_email(args[0])
        if not u:
            print("没有这个邮箱"); return 1
        auth.set_password(u["id"], args[1])
        print("已重设，并已让该账号在所有设备上退出")
    elif cmd == "unlock":
        n = db.run("UPDATE users SET failed_logins=0, locked_until=NULL WHERE email=?", args[0].lower())
        print("已解锁" if n else "没有这个邮箱")
    elif cmd == "invite":
        admin = db.one("SELECT id FROM users WHERE is_admin=1 ORDER BY id")
        code = auth.create_invite(admin["id"] if admin else None, args[2] if len(args) > 2 else "命令行生成",
                                  int(args[0]) if args else 1, int(args[1]) if len(args) > 1 else 14)
        print("邀请码：", code)
    elif cmd == "bank-maintain":
        from . import bankflow
        catalog.load()
        print(bankflow.run_once(force=True))
    elif cmd == "news":
        from . import news
        if "--sources" in args:
            cands, status = news.collect()
            for sid, st in status.items():
                print(("  [ OK ] " if st["ok"] else "  [FAIL] ") + f"{sid:18} {st['name']}：" +
                      (f"{st['n']} 条，最新 {st['newest']}" if st["ok"] else st["error"]))
            print(f"最近的候选新闻 {len(cands)} 条")
            return 0
        catalog.load()
        res = news.run_daily(force=True)
        res.pop("sources", None)
        print(res)
        for p in db.q("SELECT day, band, source_name, title, why FROM news_picks WHERE day=?", db.today().isoformat()):
            print(f"  {news.BANDS[p['band']]}：{p['title']}（{p['source_name']}）— {p['why']}")
    elif cmd == "mark-weak":
        catalog.load()
        u = auth.by_email(args[0])
        if not u:
            print("没有这个邮箱"); return 1
        for kp in args[1:]:
            if not catalog.kp(kp):
                print("跳过未知知识点", kp); continue
            engine.set_mastery(u["id"], kp, 0.15, "weak", "import")
            print("已标为薄弱：", kp, catalog.kp(kp)["name"])
        engine.today_plan(u["id"], rebuild=True)
    else:
        print(__doc__)
        return 1
    return 0


def run(argv) -> int:
    try:
        return main(argv)
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
