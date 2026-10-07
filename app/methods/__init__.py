"""学习方法（教学理念）的入口：模型注册表 + 学习方式（profile）。

一种学习方式 = 掌握模型（怎么判断懂没懂）+ 记忆模型（怎么排复习）+ 每日计划策略（每天做什么、按什么顺序），
以及它们的参数，写在 app/methods/profiles.toml（可用 config/methods.toml 覆盖 / 新增）。
- 调参数、加一种方式：只改配置文件。
- 换一种算法：写一个实现同样接口的类（见 memory.py / mastery.py 的说明），在下面的注册表里登记。
- 每种方式有一个 key（模型 + 版本 + 参数的指纹）。存下来的掌握状态带着 key，key 变了（换了方式、改了参数、
  升级了算法），就从学习记录（events 表）重放，见 app/evidence.py。
"""
import hashlib
import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .. import config
from .mastery import BKT
from .memory import FSRS

MEMORY = {FSRS.id: FSRS}
MASTERY = {BKT.id: BKT}

BUILTIN = Path(__file__).with_name("profiles.toml")


@dataclass
class Profile:
    id: str
    name: str
    desc: str
    basis: str
    memory: object
    mastery: object
    plan: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        """掌握状态的指纹：模型、版本、参数有任何变化，旧状态就要按记录重算。每日计划的策略不影响它。"""
        m, r = self.mastery, self.memory
        params = json.dumps([m.params, r.params], sort_keys=True)
        return f"{m.id}{m.version}+{r.id}{r.version}:{hashlib.sha1(params.encode()).hexdigest()[:8]}"


def _build(pid: str, d: dict) -> Profile:
    mem_cfg, mas_cfg = dict(d.get("memory") or {"model": "fsrs"}), dict(d.get("mastery") or {"model": "bkt"})
    mem_cls, mas_cls = MEMORY.get(mem_cfg.pop("model", "fsrs")), MASTERY.get(mas_cfg.pop("model", "bkt"))
    if not mem_cls or not mas_cls:
        raise ValueError(f"学习方式 {pid}：模型名不认识（记忆模型可选 {list(MEMORY)}，掌握模型可选 {list(MASTERY)}）")
    memory = mem_cls(**mem_cfg)
    return Profile(id=pid, name=d.get("name", pid), desc=d.get("desc", ""), basis=d.get("basis", ""),
                   memory=memory, mastery=mas_cls(memory, **mas_cfg), plan=dict(d.get("plan") or {}))


def _merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


class Registry:
    def __init__(self):
        self.profiles: dict[str, Profile] = {}
        self.default = "balanced"

    def load(self, extra: Path | None = None):
        d = tomllib.loads(BUILTIN.read_text(encoding="utf-8"))
        extra = extra or config.METHODS_FILE
        if extra and Path(extra).exists():
            d = _merge(d, tomllib.loads(Path(extra).read_text(encoding="utf-8")))
        self.profiles = {pid: _build(pid, p) for pid, p in d.get("profiles", {}).items()}
        self.default = d.get("default", "balanced") if d.get("default") in self.profiles else next(iter(self.profiles))
        return self

    def get(self, pid: str | None) -> Profile:
        if not self.profiles:
            self.load()
        return self.profiles.get(pid or "") or self.profiles[self.default]

    def site_default(self) -> str:
        from .. import sitecfg
        pid = sitecfg.get("method_profile")
        return pid if pid in self.profiles else self.default

    def for_user(self, user_id: int | None) -> Profile:
        """孩子自己选的学习方式；没选就用全站默认。"""
        from .. import db
        pid = None
        if user_id:
            u = db.one("SELECT settings FROM users WHERE id=?", user_id)
            pid = db.jload(u["settings"], {}).get("method") if u else None
        if not self.profiles:
            self.load()
        return self.get(pid if pid in self.profiles else self.site_default())

    def choices(self) -> list[Profile]:
        if not self.profiles:
            self.load()
        return list(self.profiles.values())


methods = Registry()
