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

## 安装（一台新的云服务器，一条命令）

准备：一台 Linux 云服务器（推荐 Ubuntu 24.04 / 22.04，1 核 1–2 GB 内存即可），可选一个已解析到服务器 IP 的域名。
用 root 或能 sudo 的用户登录服务器，运行：

```bash
# 有域名：自动配置 HTTPS（安全组放行 80、443）
curl -fsSL https://raw.githubusercontent.com/CNTWDev/AIStudy/main/install.sh | sudo bash -s -- install --domain study.example.com

# 没有域名：先用 http://服务器IP:8000 访问（安全组放行 8000）
curl -fsSL https://raw.githubusercontent.com/CNTWDev/AIStudy/main/install.sh | sudo bash -s -- install

# 服务器在中国大陆：再加 --mirror cn（用国内 pip 镜像）
```

脚本自动完成：

1. 检测环境（系统、内存、磁盘、Python、PostgreSQL、端口、域名解析）
2. 安装依赖（git、Python 3.10+、PostgreSQL；有域名时装 Caddy）
3. 拉取代码到 `/opt/aistudy`
4. 生成配置 `.env`（随机密钥、数据库地址）和 `config/llm.toml`（AI 配置模板）
5. 创建 PostgreSQL 数据库和用户
6. 安装 Python 依赖、执行数据库迁移
7. 注册开机自启服务 `aistudy`，配置 HTTPS，健康检查，打印访问地址

## 第一次使用

1. 打开网址，**第一个注册的账号就是管理员**（不需要邀请码）。
2. 在「家长页 → 添加孩子」给每个孩子创建账号：名字、登录邮箱（只当登录名，不需要能收信）、密码、年级、要学的教材。
3. 孩子在自己的手机 / 平板 / 电脑上用自己的邮箱登录；手机浏览器里可以「添加到主屏幕」。
4. 其他家庭想用：在「管理 → 邀请码」或「家长页 → 邀请亲友」生成邀请链接发给对方，对方注册后直接开通；
   没有邀请码的人也能提交申请，管理员在「管理 → 概览」里审批。

## 配置 AI（Claude 等）

```bash
sudo nano /opt/aistudy/config/llm.toml
```

在 `[providers.claude]` 的 `api_key` 填上 [Anthropic Console](https://console.anthropic.com) 申请的 Key（`sk-ant-...`）。
服务器在中国大陆或香港时，Claude API 不可用，把 `default` 改成 `"deepseek"` 并填 DeepSeek / 通义千问的 Key。改完：

```bash
sudo systemctl restart aistudy
sudo /opt/aistudy/install.sh check --llm      # 真实调用一次 AI，确认能用
```

不配 AI 也能用：诊断、现成题库练习、复习、记录照常；查词、讲解、AI 出题、AI 写短文需要 AI。

## 升级

```bash
sudo /opt/aistudy/install.sh upgrade
```

自动：拉取 GitHub 最新代码 → 备份数据库到 `/opt/aistudy/data/backups` → 更新依赖 → 执行新的数据库迁移 → 重启 → 健康检查。
`.env`、`config/llm.toml` 和所有数据都会保留。升级失败时脚本会打印回退命令。

## 日常运维

| 要做什么 | 命令 |
|---|---|
| 检查是否一切正常 | `sudo /opt/aistudy/install.sh check`（加 `--llm` 测 AI） |
| 状态 / 日志 / 重启 | `sudo /opt/aistudy/install.sh status` / `logs` / `restart` |
| 立即备份数据库 | `sudo /opt/aistudy/install.sh backup` |
| 每天凌晨自动备份 | `echo '30 3 * * * root /opt/aistudy/install.sh backup' \| sudo tee /etc/cron.d/aistudy-backup` |
| 家长忘记密码 | 管理员在「管理 → 家庭与孩子」点「重设密码链接」，把链接发给对方 |
| 孩子忘记密码 | 家长在「编辑孩子」里直接改 |
| 管理员自己忘记密码 | `cd /opt/aistudy && sudo -u aistudy .venv/bin/python -m app.cli reset-password 邮箱 新密码` |
| 账号被锁（连续输错 5 次） | 等 15 分钟，或 `sudo -u aistudy .venv/bin/python -m app.cli unlock 邮箱` |

更多（私有仓库安装、Docker、恢复备份）见 [docs/DEPLOY.md](docs/DEPLOY.md)。

## 两个配置文件

| 文件 | 管什么 |
|---|---|
| `.env` | 服务器、数据库地址、注册方式（`REGISTRATION`）、登录安全策略（安装脚本自动生成） |
| `config/llm.toml` | AI：用哪家（Claude / DeepSeek / 通义…）、模型、API Key、每日上限、按任务指定模型 |

注册方式 `REGISTRATION`：`approval`（默认，有邀请码直接开通，没有的需管理员审批）/ `invite`（必须邀请码）/ `open` / `closed`。

## 账号与管理后台

- 邮箱 + 密码登录；改密码后其他设备自动下线；可查看和退出登录中的设备；连续输错 5 次锁定 15 分钟。
- **管理后台**（管理员可见，页面顶部「管理」）：
  - 概览：家庭数、孩子数、近 7 天在学人数、**待审批申请**（通过 / 拒绝）
  - 家庭与孩子：每个家庭的家长、邀请人，以及每个孩子的年级、教材、今天完成情况、连续坚持天数、本周学习时长、待复习和生词数、薄弱点，可打开学习报告；停用 / 启用、重设密码链接、设为管理员
  - 邀请码：生成（次数、有效期、备注）、作废、看每个码被谁用了
  - 邀请关系：谁邀请了谁的树状图
  - 安全日志、系统状态（版本、数据库、AI、迁移）

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
