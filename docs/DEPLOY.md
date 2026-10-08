# 部署上线

目标：孩子在自己的手机 / 平板 / 电脑浏览器里打开一个网址，用自己的邮箱登录。

## 1. 准备

- **一台 Linux 云服务器**：1 核 1–2 GB 内存、20 GB 磁盘就够。系统推荐 **Ubuntu 24.04 / 22.04** 或 Debian 12；
  Rocky / Alma 9、阿里云 Alibaba Cloud Linux 3、OpenCloudOS 也支持。
- **一个域名**（推荐，用于 HTTPS）。没有域名也能先用 `http://服务器IP:8000` 访问。
- **AI 密钥**（可选，但查词、讲解、AI 出题、写短文、造句点评都需要）。

### 服务器放哪里？AI 用哪家？

| 方案 | 服务器 | AI | 说明 |
|---|---|---|---|
| A | 海外（美国、新加坡、日本等） | Claude（`type = "anthropic"`） | Claude 质量最好；国内访问可能偏慢 |
| B | 香港 / 新加坡 / 日本 | DeepSeek 或通义千问（`type = "openai_compat"`） | 域名无需备案；国内访问速度一般可以 |
| C | 中国大陆（阿里云、腾讯云） | DeepSeek 或通义千问（`type = "openai_compat"`） | 访问最快；**域名必须先做 ICP 备案** |

注意：按 Anthropic 的支持地区政策，中国大陆和香港目前不在 Claude API 的服务范围内，服务器在这两地时请用 `openai_compat`。
请以 Anthropic 官网的最新支持地区列表为准。

## 2. 一键安装

登录服务器（root 或能 sudo 的用户），运行一条命令：

```bash
# 没有域名，先用 IP 访问
curl -fsSL https://raw.githubusercontent.com/CNTWDev/AIStudy/main/install.sh | sudo bash -s -- install

# 有域名（先把域名 A 记录解析到这台服务器），自动配置 HTTPS
curl -fsSL https://raw.githubusercontent.com/CNTWDev/AIStudy/main/install.sh | sudo bash -s -- install --domain study.example.com

# 服务器在中国大陆：加 --mirror cn 用清华 pip 镜像
curl -fsSL https://raw.githubusercontent.com/CNTWDev/AIStudy/main/install.sh | sudo bash -s -- install --mirror cn
```

如果仓库是**私有**的，raw 链接打不开，改成先克隆再安装：

```bash
sudo git clone https://<你的GitHub用户名>:<个人访问令牌>@github.com/CNTWDev/AIStudy.git /opt/aistudy
sudo /opt/aistudy/install.sh install            # 可加 --domain / --mirror cn
```

（个人访问令牌在 GitHub → Settings → Developer settings → Fine-grained tokens 生成，只给这个仓库 Contents 只读权限即可。
以后 `upgrade` 会沿用这个地址拉代码。）

脚本会依次做：

1. **检测环境**：系统版本、内存、磁盘、Python、git、PostgreSQL、端口占用、域名解析。
2. **安装依赖**：git、Python 3.10+、PostgreSQL（以及有域名时的 Caddy）。
3. **获取代码**到 `/opt/aistudy`。
4. **生成配置**：`.env`（随机 SECRET_KEY、数据库地址）和 `config/llm.toml`（AI 配置模板）。已有的配置不会被覆盖。
5. **建数据库**：本机 PostgreSQL 里创建 `aistudy` 用户和库（随机密码写进 `.env`）。
6. **安装 Python 依赖**到 `/opt/aistudy/.venv`。
7. **备份 + 迁移数据库**。
8. **注册 systemd 服务** `aistudy`（开机自启、崩溃自动重启），以普通用户 `aistudy` 运行。
9. **配置 HTTPS**（有 `--domain` 时，Caddy 自动申请和续期证书）。
10. **健康检查**，打印访问地址。

云服务器的**安全组 / 防火墙**要放行：有域名时 80 和 443；没域名时 8000。

## 3. 第一次使用

1. 打开网址，**第一个注册的账号是网站管理员**（只管网站，不带孩子）。在「管理 → 家庭与孩子」底部给自己创建一个**家长账号**，退出后用家长账号登录。
2. 在「家长页」→「添加孩子」：给每个孩子一个登录邮箱（不需要是真实能收信的邮箱，只当登录名用）和密码，选年级和要学的教材。
3. 孩子在自己的设备上用自己的邮箱登录。手机浏览器里可以「添加到主屏幕」，像 App 一样打开。

4. 家长页「阅读与单词」给每个孩子选书和词表，「学校进度」选好每门课学到哪了；电脑上在顶部「查词工具」安装划词查词插件。

其他家庭想用：管理员在「管理」页生成**邀请码**发给对方，对方在登录页「新家庭注册」时填写。
没有邀请码的人也能提交申请，管理员在「管理 → 概览」里审批。注册方式在「管理 → 站点设置」里可以改为邀请制（必须邀请码）、关闭（只能管理员创建）或开放注册。

## 4. 配置 AI（Claude 等）

```bash
sudo nano /opt/aistudy/config/llm.toml
```

用 Claude：在 [Anthropic Console](https://console.anthropic.com) 申请 API Key，填到 `[providers.claude]` 的 `api_key`：

```toml
default = "claude"

[providers.claude]
type = "anthropic"
api_key = "sk-ant-..."
model = "claude-opus-5-5"
```

用 DeepSeek / 通义千问：把 `default` 改成 `"deepseek"`，在 `[providers.deepseek]` 里填 `api_key`（通义千问照文件里的注释改 `base_url` 和 `model`）。

改完重启并检查：

```bash
sudo systemctl restart aistudy
sudo /opt/aistudy/install.sh check --llm     # --llm 会真实调用一次 AI，确认密钥和网络都通
```

还可以按任务单独指定模型（例如查词用便宜的、出题用强的），见 `config/llm.toml` 底部的 `[tasks.xxx]` 注释。
`default = "none"` 则关闭所有 AI 功能，其余功能照常。

**试卷拍照解析**要用能看图片的模型：Claude 可以直接用；用 DeepSeek 时给 `[tasks.paper]` 单独指定通义千问的视觉模型（`qwen-vl-max`，见 `config/llm.toml` 底部注释），或者导入时粘贴文字。
试卷照片存在 `/opt/aistudy/data/papers/`；`install.sh backup` 只备份数据库，照片如需保留请一并拷贝。

## 5. 升级

```bash
sudo /opt/aistudy/install.sh upgrade
```

会自动：拉取 GitHub 上的最新代码 → **备份数据库**到 `/opt/aistudy/data/backups` → 更新依赖 → 执行新的数据库迁移 →
重启服务 → 健康检查。如果升级后服务起不来，脚本会打印回退命令。`.env`、`config/llm.toml` 和数据不会被改动。

## 5.1 换域名

先把新域名的 A 记录解析到这台服务器，然后：

```bash
sudo /opt/aistudy/install.sh domain --domain beejoy.ai,www.beejoy.ai   # 多个域名用逗号隔开，第一个是主域名
```

会自动：改 `.env` 的 `PUBLIC_URL`（邀请链接、重设密码链接用它）→ 重写 Caddy 配置并申请新证书 → 重启 → 在服务器本机用新域名访问一次。
想让旧域名继续能用，把旧域名也写进列表即可。登录状态不受影响。

如果服务器上已经有 nginx（或 Apache）占着 80/443，脚本不会启用 Caddy，而是打印一段 nginx 配置：在 nginx 里给新域名加 `server_name`、`proxy_pass http://127.0.0.1:端口`，再用 `certbot --nginx -d 新域名` 申请证书即可。

**本机检查通过、浏览器却报 `403 Forbidden` 或打不开**：请求在到达服务器之前就被拦了，和本应用无关（应用和 Caddy 都不会返回这个 403）。逐个排查：

- **服务器在中国大陆**：新域名必须在这家云厂商完成 ICP 备案，否则 80/443 的访问会被云厂商拦截。不是所有域名后缀都能备案（以工信部公布的列表为准）；不能备案的域名，只能换香港 / 海外服务器。
- **域名开了 CDN / 代理**（Cloudflare 的橙色云朵、阿里云 ESA / CDN 等）：检查它的 WAF / 防火墙规则，或先关掉代理（DNS only）直连试试。
- **解析没指到这台服务器，或者不止一个 IP**：`dig +short beejoy.ai @8.8.8.8` 应该**只有**服务器公网 IP 一行。多出来的 IP 多半是注册商的停放页 / 转发服务器（例如 GoDaddy 的 `15.197.148.33`、`3.33.130.190`，它们返回的就是 `Request forbidden by administrative rules`），浏览器会随机连到它们。在注册商的 DNS 里删掉这些记录，并关掉域名转发。

## 6. 日常运维

| 要做什么 | 命令 |
|---|---|
| 检查一切是否正常 | `sudo /opt/aistudy/install.sh check`（加 `--llm` 测 AI） |
| 看服务状态 / 日志 | `sudo /opt/aistudy/install.sh status` / `logs` |
| 重启 | `sudo /opt/aistudy/install.sh restart` |
| 立即备份 | `sudo /opt/aistudy/install.sh backup` |
| 家长忘记密码 | 管理员在「管理」页点「重设密码链接」，把链接发给对方 |
| 管理员自己忘记密码 | `cd /opt/aistudy && sudo -u aistudy .venv/bin/python -m app.cli reset-password 邮箱 新密码` |
| 账号被锁 | `cd /opt/aistudy && sudo -u aistudy .venv/bin/python -m app.cli unlock 邮箱` |
| 命令行生成邀请码 | `cd /opt/aistudy && sudo -u aistudy .venv/bin/python -m app.cli invite 次数 天数 备注` |
| 导入已知薄弱点 | `cd /opt/aistudy && sudo -u aistudy .venv/bin/python -m app.cli mark-weak 孩子邮箱 知识点ID ...` |

**自动定时备份**（建议）：

```bash
echo '30 3 * * * root /opt/aistudy/install.sh backup >/dev/null 2>&1' | sudo tee /etc/cron.d/aistudy-backup
```

**恢复备份**：

```bash
sudo systemctl stop aistudy
gunzip -c /opt/aistudy/data/backups/aistudy-时间.sql.gz | sudo -u postgres psql -d aistudy   # 恢复到空库；或先 dropdb/createdb
sudo systemctl start aistudy
```

备份文件最好再定期下载一份到自己电脑（`scp`）或同步到对象存储。

## 7. 不用脚本：Docker Compose

适合已经熟悉 Docker 的情况（应用 + PostgreSQL + Caddy 三个容器）：

```bash
git clone https://github.com/CNTWDev/AIStudy.git && cd AIStudy
cp .env.example .env                       # 改 SECRET_KEY；DATABASE_URL 用 compose 里的默认值即可（见 docker-compose.yml）
cp config/llm.example.toml config/llm.toml # 填 AI 密钥
nano Caddyfile                             # 把域名换成你的
docker compose up -d --build
```

升级：`git pull && docker compose up -d --build`（应用启动时自动执行数据库迁移）。

## 8. 本地开发

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env                       # 默认 SQLite，无需装数据库
cp config/llm.example.toml config/llm.toml # 不填密钥也能用；想试 AI 界面可把 default 改成 "mock"
uvicorn app.main:app --reload
python -m pytest -q                        # 测试（SQLite）
TEST_DATABASE_URL=postgresql://user:pass@127.0.0.1/aistudy_test python -m pytest -q   # 在 PostgreSQL 上测试
```
