"""追根找到的根、共同的薄弱前置：昨天已经学过、练得也还行，今天不再排「先打基础」从头学，换一道小题确认。"""
import uuid

from fastapi.testclient import TestClient

from app import auth, evidence, insights, trace
from app.main import app

ROOT, KP = "MATH-LS-8N-02", "MATH-LS-8N-04"


def _found(kid):
    return [f for f in insights.detect(kid) if f["kind"] == "trace"]


def test_learned_root_is_confirmed_not_relearned(monkeypatch):
    with TestClient(app):
        kid = auth.create_user(f"{uuid.uuid4().hex}@test.example", "secret123", "测试孩子", role="kid")
        monkeypatch.setattr(trace, "recent", lambda *a, **k: [{
            "kp": KP, "name": "幂的运算", "result": "root", "root": {"kp": ROOT, "name": "质因数分解与 HCF、LCM"},
            "day": "2026-10-18", "chain": [{"kind": "kp", "kp": ROOT, "name": "质因数分解与 HCF、LCM", "correct": False}]}])

        # 追根时答错了根：先打基础
        evidence.record(kid, ROOT, False, mode="probe", fmt="num")
        f = _found(kid)
        assert f and f[0]["action"] == "backfill"
        assert insights.plan_tasks(f)["backfill"][0]["title"].startswith("先打基础：")

        # 按「先打基础」学过、练习题都做对了：还差隔天、换题再对才算学会，但不用再从头学一遍
        for i in range(3):
            evidence.record(kid, ROOT, True, item={"id": f"x{i}", "type": "num"}, mode="practice")
        f = _found(kid)
        assert f and f[0]["action"] == "check"
        tasks = insights.plan_tasks(f)
        assert not tasks["backfill"] and tasks["check"][0]["kp"] == ROOT
        assert tasks["check"][0]["title"].startswith("还记得吗：")
