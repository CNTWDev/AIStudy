"""运行配置。

- 服务器/数据库/安全相关：环境变量，或项目根目录的 .env 文件（install.sh 会自动生成）。
- AI（Claude 等大模型）相关：config/llm.toml，见 config/llm.example.toml。
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env = Path(os.environ.get("AISTUDY_ENV_FILE", BASE_DIR / ".env"))
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
# 生产：postgresql://用户:密码@127.0.0.1:5432/aistudy    本地试用：sqlite:///data/aistudy.db
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DATA_DIR / 'aistudy.db'}")
DB_POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "10"))
CURRICULA_DIR = Path(os.environ.get("CURRICULA_DIR", BASE_DIR / "curricula"))
SEED_DIR = Path(os.environ.get("SEED_DIR", BASE_DIR / "seed"))
METHODS_FILE = Path(os.environ.get("METHODS_FILE", BASE_DIR / "config" / "methods.toml"))  # 可选：覆盖 / 新增学习方式
LLM_CONFIG_FILE = Path(os.environ.get("LLM_CONFIG_FILE", BASE_DIR / "config" / "llm.toml"))
TIMEZONE = os.environ.get("TZ_NAME", "Asia/Shanghai")
HTTPS_ONLY = os.environ.get("HTTPS_ONLY", "0") == "1"
# 对外访问地址，用于生成重设密码链接，例如 https://study.example.com
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")

# 家长账号注册方式：
#   approval（默认）= 有邀请码直接开通；没有邀请码也能申请，管理员在后台审批通过后才能登录
#   invite  = 必须有邀请码
#   open    = 任何人可注册，直接开通
#   closed  = 不能自助注册，只能由管理员创建
# 无论哪种，系统里第一个注册的账号都会成为管理员。孩子账号始终由家长创建。
REGISTRATION = os.environ.get("REGISTRATION", "approval").lower()
if os.environ.get("ALLOW_REGISTER") == "1":  # 兼容旧配置
    REGISTRATION = "open"
# 每个家长最多同时持有几个未用完的邀请码（管理员不限）
PARENT_INVITE_LIMIT = int(os.environ.get("PARENT_INVITE_LIMIT", "3"))
# 「问一问」小助手的名字和图标（可以按自家孩子改，例如取孩子名字里的字）
ASSISTANT_NAME = os.environ.get("ASSISTANT_NAME", "小艾").strip() or "小艾"
ASSISTANT_ICON = os.environ.get("ASSISTANT_ICON", "🙋").strip() or "🙋"
PASSWORD_MIN_LEN = int(os.environ.get("PASSWORD_MIN_LEN", "6"))
SESSION_DAYS = int(os.environ.get("SESSION_DAYS", "60"))
# 连续输错密码多少次后锁定，锁定多少分钟
LOGIN_MAX_FAILS = int(os.environ.get("LOGIN_MAX_FAILS", "5"))
LOGIN_LOCK_MINUTES = int(os.environ.get("LOGIN_LOCK_MINUTES", "15"))

DATA_DIR.mkdir(parents=True, exist_ok=True)
