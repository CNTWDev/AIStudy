"""站点设置：管理员在后台「系统 → 站点设置」里改，存在 site_settings 表；没设置的用 .env / 默认值。"""
import threading

from . import config, db

DEFAULTS = {
    "site_name": lambda: "AIStudy",
    "assistant_name": lambda: config.ASSISTANT_NAME,
    "assistant_icon": lambda: config.ASSISTANT_ICON,
    "registration": lambda: config.REGISTRATION,
    "parent_invite_limit": lambda: str(config.PARENT_INVITE_LIMIT),
    "method_profile": lambda: "",  # 全站默认学习方式（空 = 用 app/methods/profiles.toml 的 default）
}
REG_MODES = {"approval": "审批制：有邀请码直接开通，没有的提交申请等管理员审批",
             "invite": "邀请制：必须有邀请码", "open": "开放注册", "closed": "关闭注册：只能由管理员创建"}

_lock = threading.Lock()
_cache: dict | None = None


def _load() -> dict:
    global _cache
    with _lock:
        if _cache is None:
            try:
                _cache = {r["key"]: r["value"] for r in db.q("SELECT key, value FROM site_settings")}
            except Exception:  # 迁移之前
                return {}
        return _cache


def get(key: str) -> str:
    v = _load().get(key)
    return v if v not in (None, "") else DEFAULTS[key]()


def set_many(values: dict) -> None:
    global _cache
    now = db.now()
    with db.tx() as t:
        for k, v in values.items():
            if k not in DEFAULTS:
                continue
            t.run("INSERT INTO site_settings(key,value,updated_at) VALUES(?,?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at", k, str(v).strip(), now)
    with _lock:
        _cache = None


def reset_cache() -> None:
    global _cache
    with _lock:
        _cache = None


def registration() -> str:
    m = get("registration")
    return m if m in REG_MODES else "approval"


def parent_invite_limit() -> int:
    try:
        return max(0, int(get("parent_invite_limit")))
    except ValueError:
        return config.PARENT_INVITE_LIMIT
