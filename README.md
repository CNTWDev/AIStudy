# AIStudy 家庭学习系统

给孩子用的网页版学习系统：每个孩子用自己的邮箱登录，家长有总览页。
同一套系统，换一份教材数据就能覆盖小学、初中、高中和国际课程。

**能做什么**

- **摸底诊断**：从孩子当前学段出题，做错了自动往回追前置知识（最多 3 层），找到真正的「断点」。
- **每日计划**：按「补弱 + 回溯补漏 + 间隔复习 + 少量预习 + 阅读」自动排，按每天可用时间截断。
- **一步步学一个知识点**：看懂 → 记方法 → 做 3 题（从易到难，有提示）→ 一句话总结；连错两题会提示「退一步」去补前置。
- **间隔复习**：生词、错题、术语、知识点总结都变成卡片，按 1/2/4/7/15/30 天出现。
- **阅读**：AI 按孩子水平写短文（自动用上要复习的生词），或者粘贴自己的书；点词即查，收藏进生词本，用它造句让 AI 点评，看不懂的句子让 AI 拆解。
- **学习记录**：打卡日历、每日反思、做题记录、各科掌握度、薄弱点；家长可看每个孩子的报告，也可以「以孩子视角」陪着用。

**已内置的教材（6 套，741 个知识点，带前置关系）**

| 学科 | 教材 | 学段 |
|---|---|---|
| 语文 | 统编版（部编版） | 一年级–高三 |
| 英语 | 上海牛津版 + 上海高中新教材 | 一年级–高三 |
| 数学 | 沪教版 | 一年级–高三 |
| 物理 | 沪教版（初中）+ 沪科教版（高中） | 初二–高三（含六、七年级科学基础） |
| 英语 | Cambridge Lower Secondary → IGCSE | 三年级–IGCSE |
| 物理 | Cambridge Lower Secondary Science → IGCSE 0625 | 六年级–IGCSE（1.1 测量章附 35 道已核对的题和 47 张术语卡） |

## 上线（一键安装）

在一台 Linux 云服务器（推荐 Ubuntu 24.04）上：

```bash
curl -fsSL https://raw.githubusercontent.com/CNTWDev/AIStudy/main/install.sh | sudo bash -s -- install --domain 你的域名
```

自动完成：环境检测 → 安装 Python / PostgreSQL / Caddy → 拉代码 → 生成配置 → 建库 → 迁移 → 注册开机服务 → HTTPS → 健康检查。
以后升级：`sudo /opt/aistudy/install.sh upgrade`（自动备份数据库、迁移、重启）。详见 [docs/DEPLOY.md](docs/DEPLOY.md)。

**两个配置文件**

| 文件 | 管什么 |
|---|---|
| `.env` | 服务器、数据库地址、注册方式、登录安全策略（安装脚本自动生成） |
| `config/llm.toml` | AI：用哪家（Claude / DeepSeek / 通义…）、模型、API Key、每日上限、按任务指定模型 |

**账号**：邮箱 + 密码登录。第一个注册的是管理员；其他家庭凭邀请码注册；孩子账号由家长创建。
支持改密码、多设备登录管理、输错锁定、管理员停用账号 / 生成重设密码链接、安全日志。

## 第一次使用

打开网址 → 注册管理员（家长）账号 → 在家长页「添加孩子」，填邮箱、密码、年级，勾选教材 → 孩子用自己的邮箱登录。

已经知道的薄弱点（例如以前的试卷分析）可以一条命令导入：

```bash
cd /opt/aistudy && sudo -u aistudy .venv/bin/python -m app.cli mark-weak 孩子邮箱 PHY-IG-1.1-02 PHY-IG-1.1-04
```

## 本地开发

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # 默认 SQLite，无需安装数据库
uvicorn app.main:app --reload   # 打开 http://localhost:8000
python -m pytest -q
```

## 设计与扩展

见 [docs/DESIGN.md](docs/DESIGN.md)：架构、账号、数据模型、诊断与计划算法、AI 抽象层、怎样加新教材/新学科/新题库/新模型厂商。

## 目录

```
app/            应用代码（app/llm/ 是 AI 抽象层）
migrations/     数据库迁移（按编号执行）
curricula/      教材知识图谱（每套教材一个 JSON）
seed/           人工核对过的题库、术语卡
config/         llm.example.toml（AI 配置模板）
install.sh      一键安装 / 升级 / 检测 / 备份
docs/           设计文档、部署文档
```
