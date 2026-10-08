"""测试环境：临时数据库、mock 大模型。所有测试文件共用（必须在导入 app 之前设置）。"""
import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{os.environ['DATA_DIR']}/test.db"
os.environ["LLM_CONFIG_FILE"] = os.path.join(os.environ["DATA_DIR"], "no-llm.toml")
os.environ["LLM_PROVIDER"] = "mock"
os.environ["SECRET_KEY"] = "test"
os.environ["AISTUDY_JOBS"] = "0"  # 不开后台题库流水线（测试里直接调用 bankflow.run_once）
os.environ["REGISTRATION"] = "invite"
os.environ["METHODS_FILE"] = os.path.join(os.environ["DATA_DIR"], "no-methods.toml")  # 不受本机 config/methods.toml 影响

if os.environ.get("TEST_DATABASE_URL"):  # 测试库每次清空重建
    import psycopg
    with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as _c:
        _c.execute("DROP SCHEMA public CASCADE")
        _c.execute("CREATE SCHEMA public")

