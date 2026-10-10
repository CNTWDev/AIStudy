#!/usr/bin/env bash
# =============================================================================
# AIStudy 一键安装 / 升级脚本
#
# 第一次安装（在一台全新的 Linux 云服务器上，用 root 或 sudo）：
#   curl -fsSL https://raw.githubusercontent.com/CNTWDev/AIStudy/main/install.sh | sudo bash -s -- install
#   # 有域名、想自动配 HTTPS：
#   curl -fsSL https://raw.githubusercontent.com/CNTWDev/AIStudy/main/install.sh | sudo bash -s -- install --domain study.example.com
#   # 仓库是私有的：先 git clone 到 /opt/aistudy，再运行 sudo /opt/aistudy/install.sh install
#
# 以后升级（拉取 GitHub 最新代码 → 备份数据库 → 安装依赖 → 迁移数据库 → 重启 → 健康检查）：
#   sudo /opt/aistudy/install.sh upgrade
#
# 其他命令：
#   domain     换域名：sudo /opt/aistudy/install.sh domain --domain 新域名（多个用逗号隔开）
#   check      检测环境、数据库、AI 配置（加 --llm 会真实调用一次 AI）
#   backup     立即备份数据库到 /opt/aistudy/data/backups
#   status     查看服务状态        logs    查看最近日志        restart   重启服务
#
# 选项：
#   --dir DIR         安装目录（默认 /opt/aistudy）
#   --repo URL        代码仓库（默认 https://github.com/CNTWDev/AIStudy.git）
#   --branch NAME     分支（默认 main）
#   --domain NAME     绑定域名并用 Caddy 自动申请 HTTPS 证书（需先把域名解析到这台服务器）
#                     多个域名用逗号隔开，如 beejoy.ai,www.beejoy.ai；第一个是主域名（用于生成链接）
#   --port N          应用监听端口（默认 8000）
#   --db-url URL      使用已有的 PostgreSQL（不在本机安装数据库），如 postgresql://user:pass@host:5432/aistudy
#   --mirror cn       使用国内 pip 镜像（服务器在中国大陆时建议加上）
#   --workers N       应用进程数（默认 2）
#   --llm             配合 check：真实调用一次 AI
#   -y, --yes         不询问，直接继续
#
# 支持系统：Ubuntu 22.04+/Debian 12+（推荐）、Rocky/Alma/CentOS Stream 9、阿里云 Alibaba Cloud Linux 3、
#           OpenCloudOS / TencentOS（dnf 系）。需要 systemd。
# =============================================================================
set -Eeuo pipefail

APP_NAME="aistudy"
APP_DIR="/opt/aistudy"
REPO_URL="https://github.com/CNTWDev/AIStudy.git"
BRANCH="main"
DOMAIN=""
PORT="8000"
DB_URL=""
MIRROR=""
WORKERS="2"
WITH_LLM=""
ASSUME_YES=""
SERVICE_USER="aistudy"
MIN_PY_MINOR=10
CMD="${1:-help}"
[[ $# -gt 0 ]] && shift

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir) APP_DIR="$2"; shift 2 ;;
    --repo) REPO_URL="$2"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    --domain) DOMAIN="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --db-url) DB_URL="$2"; shift 2 ;;
    --mirror) MIRROR="$2"; shift 2 ;;
    --workers) WORKERS="$2"; shift 2 ;;
    --llm) WITH_LLM="--llm"; shift ;;
    -y|--yes) ASSUME_YES=1; shift ;;
    *) echo "未知选项：$1"; exit 2 ;;
  esac
done

# ---------------------------------------------------------------- 输出
if [[ -t 1 ]]; then C_G=$'\e[32m'; C_Y=$'\e[33m'; C_R=$'\e[31m'; C_B=$'\e[1m'; C_0=$'\e[0m'; else C_G=; C_Y=; C_R=; C_B=; C_0=; fi
step() { echo; echo "${C_B}==> $*${C_0}"; }
ok()   { echo "  ${C_G}[ OK ]${C_0} $*"; }
warn() { echo "  ${C_Y}[WARN]${C_0} $*"; }
fail() { echo "  ${C_R}[FAIL]${C_0} $*"; }
die()  { echo; echo "${C_R}错误：$*${C_0}" >&2; exit 1; }
trap 'echo; echo "${C_R}安装/升级在第 $LINENO 行出错，已停止。把上面的输出发给维护者即可排查。${C_0}" >&2' ERR

confirm() {
  [[ -n "$ASSUME_YES" ]] && return 0
  [[ -t 0 ]] || return 0
  read -r -p "$1 [Y/n] " a
  [[ -z "$a" || "$a" =~ ^[Yy] ]]
}

need_root() { [[ $EUID -eq 0 ]] || die "请用 root 运行，或在命令前加 sudo"; }

ENV_FILE() { echo "$APP_DIR/.env"; }
VENV="$APP_DIR/.venv"
PY_APP() { "$VENV/bin/python" "$@"; }

# 升级时脚本自己会被 git 更新；先把自己复制到临时文件再执行，避免执行到一半文件被替换
if [[ -z "${AISTUDY_REEXEC:-}" && -f "${BASH_SOURCE[0]:-}" && "$CMD" == "upgrade" ]]; then
  tmp="$(mktemp /tmp/aistudy-install.XXXXXX.sh)"
  cp "${BASH_SOURCE[0]}" "$tmp"
  AISTUDY_REEXEC=1 exec bash "$tmp" "$CMD" --dir "$APP_DIR" --repo "$REPO_URL" --branch "$BRANCH" --port "$PORT" \
    --workers "$WORKERS" ${DOMAIN:+--domain "$DOMAIN"} ${MIRROR:+--mirror "$MIRROR"} ${ASSUME_YES:+--yes}
fi

# ---------------------------------------------------------------- 系统识别
OS_ID=""; OS_VER=""; PKG=""
detect_os() {
  if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    OS_ID="${ID:-}"; OS_VER="${VERSION_ID:-}"
  fi
  if command -v apt-get >/dev/null 2>&1; then PKG="apt"
  elif command -v dnf >/dev/null 2>&1; then PKG="dnf"
  elif command -v yum >/dev/null 2>&1; then PKG="yum"
  else PKG=""; fi
}

pkg_install() {
  case "$PKG" in
    apt) DEBIAN_FRONTEND=noninteractive apt-get install -y -q "$@" ;;
    dnf) dnf install -y -q "$@" ;;
    yum) yum install -y -q "$@" ;;
    *) die "不认识这台机器的包管理器，请手动安装：$*" ;;
  esac
}

APT_UPDATED=""
pkg_refresh() {
  if [[ "$PKG" == "apt" && -z "$APT_UPDATED" ]]; then apt-get update -q; APT_UPDATED=1; fi
}

# 找一个 >= 3.10 的 Python
PYTHON=""
find_python() {
  PYTHON=""
  for c in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1; then
      local v; v="$("$c" -c 'import sys;print(sys.version_info[1] if sys.version_info[0]==3 else 0)' 2>/dev/null || echo 0)"
      if [[ "$v" -ge $MIN_PY_MINOR ]]; then PYTHON="$(command -v "$c")"; return 0; fi
    fi
  done
  return 1
}

has_systemd() { command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; }

# ---------------------------------------------------------------- 环境检测
check_env() {
  step "检测服务器环境"
  detect_os
  local good=1
  ok "系统：${PRETTY_NAME:-$OS_ID $OS_VER}（包管理器：${PKG:-无}）"
  [[ -n "$PKG" ]] || { fail "没有 apt/dnf/yum，本脚本无法自动安装依赖"; good=0; }
  if has_systemd; then ok "systemd 可用"; else warn "没有 systemd：无法注册开机自启服务（容器环境请改用 docker compose，见 docs/DEPLOY.md）"; fi
  local mem; mem=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0)
  if [[ "$mem" -ge 900 ]]; then ok "内存 ${mem}MB"; else warn "内存 ${mem}MB，建议至少 1GB"; fi
  local disk; disk=$(df -Pm "$(dirname "$APP_DIR")" 2>/dev/null | awk 'NR==2{print $4}')
  if [[ "${disk:-0}" -ge 2048 ]]; then ok "可用磁盘 ${disk}MB"; else warn "可用磁盘 ${disk:-?}MB，建议至少 2GB"; fi
  if find_python; then ok "Python：$($PYTHON --version 2>&1)（$PYTHON）"; else warn "没有 Python 3.$MIN_PY_MINOR+，将尝试安装"; fi
  if command -v git >/dev/null; then ok "git：$(git --version | awk '{print $3}')"; else warn "没有 git，将安装"; fi
  if [[ -n "$DB_URL" ]]; then ok "数据库：使用外部 PostgreSQL"
  elif command -v psql >/dev/null; then ok "PostgreSQL 客户端：$(psql --version | awk '{print $3}')"
  else warn "没有 PostgreSQL，将安装"; fi
  if command -v ss >/dev/null && ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$PORT\$"; then
    if systemctl is-active --quiet "$APP_NAME" 2>/dev/null; then ok "端口 $PORT 由 $APP_NAME 使用中"
    else warn "端口 $PORT 已被其他程序占用，可用 --port 换一个"; fi
  fi
  local d
  for d in ${DOMAIN//,/ }; do
    local ip; ip=$(getent hosts "$d" | awk '{print $1}' | head -1 || true)
    if [[ -n "$ip" ]]; then ok "域名 $d 解析到 $ip（请确认这是本机公网 IP，且安全组放行 80/443）"
    else warn "域名 $d 还没有解析，HTTPS 证书会申请失败"; fi
  done
  [[ $good -eq 1 ]]
}

# ---------------------------------------------------------------- 安装依赖
install_packages() {
  step "安装系统依赖"
  detect_os
  pkg_refresh
  case "$PKG" in
    apt)
      pkg_install ca-certificates curl git gzip
      if ! find_python; then pkg_install python3 python3-venv python3-pip || true; fi
      find_python || die "系统自带 Python 低于 3.$MIN_PY_MINOR（例如 Ubuntu 20.04），请换 Ubuntu 22.04/24.04 或 Debian 12"
      # venv 模块在 Debian/Ubuntu 是单独的包
      local pyv; pyv="$($PYTHON -c 'import sys;print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
      "$PYTHON" -m venv --help >/dev/null 2>&1 && "$PYTHON" -c 'import ensurepip' 2>/dev/null || pkg_install "python${pyv}-venv" || pkg_install python3-venv
      [[ -n "$DB_URL" ]] || pkg_install postgresql postgresql-contrib
      [[ -z "$DB_URL" ]] || pkg_install postgresql-client
      ;;
    dnf|yum)
      pkg_install ca-certificates curl git gzip tar
      if ! find_python; then pkg_install python3.11 python3.11-pip 2>/dev/null || pkg_install python3.12 python3.12-pip 2>/dev/null || pkg_install python3 python3-pip; fi
      find_python || die "装不上 Python 3.$MIN_PY_MINOR+，请手动安装后重试"
      if [[ -z "$DB_URL" ]]; then
        pkg_install postgresql-server postgresql-contrib || pkg_install postgresql-server
        if [[ ! -f /var/lib/pgsql/data/PG_VERSION ]]; then postgresql-setup --initdb; fi
        # 允许本机用密码登录（默认是 ident，应用连不上）
        local hba=/var/lib/pgsql/data/pg_hba.conf
        if [[ -f "$hba" ]] && grep -qE '^host\s+all\s+all\s+127\.0\.0\.1/32\s+ident' "$hba"; then
          sed -ri 's/^(host\s+all\s+all\s+(127\.0\.0\.1\/32|::1\/128)\s+)ident/\1scram-sha-256/' "$hba"
        fi
      else
        pkg_install postgresql
      fi
      ;;
  esac
  ok "Python：$($PYTHON --version 2>&1)"
  ok "git：$(git --version | awk '{print $3}')"
}

# ---------------------------------------------------------------- PostgreSQL
start_postgres() {
  if has_systemd; then
    systemctl enable --now postgresql >/dev/null 2>&1 || {
      # Debian/Ubuntu 上服务名可能带版本号
      local unit; unit=$(systemctl list-unit-files 'postgresql*' --no-legend 2>/dev/null | awk '{print $1}' | head -1)
      [[ -n "$unit" ]] && systemctl enable --now "$unit"
    }
  else
    service postgresql start >/dev/null 2>&1 || true
  fi
  for _ in $(seq 1 20); do
    su - postgres -c "psql -qtAc 'select 1'" >/dev/null 2>&1 && return 0
    sleep 1
  done
  die "PostgreSQL 启动失败，可运行 journalctl -u postgresql 查看原因"
}

setup_database() {
  step "准备数据库"
  if grep -qE '^DATABASE_URL=postgres' "$(ENV_FILE)" 2>/dev/null; then
    ok "沿用 .env 里已有的 DATABASE_URL"
    if grep -qE '^DATABASE_URL=postgres(ql)?://[^@]*@(127\.0\.0\.1|localhost)' "$(ENV_FILE)"; then start_postgres; fi
    return
  fi
  if [[ -n "$DB_URL" ]]; then
    set_env DATABASE_URL "$DB_URL"
    ok "使用外部数据库"
    return
  fi
  start_postgres
  local pw; pw="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' | head -c 32)"
  if su - postgres -c "psql -qtAc \"select 1 from pg_roles where rolname='$APP_NAME'\"" | grep -q 1; then
    su - postgres -c "psql -qc \"alter role $APP_NAME with login password '$pw'\"" >/dev/null
    ok "数据库用户 $APP_NAME 已存在，已重设它的密码"
  else
    su - postgres -c "psql -qc \"create role $APP_NAME with login password '$pw'\"" >/dev/null
    ok "已创建数据库用户 $APP_NAME"
  fi
  if su - postgres -c "psql -qtAc \"select 1 from pg_database where datname='$APP_NAME'\"" | grep -q 1; then
    ok "数据库 $APP_NAME 已存在"
  else
    su - postgres -c "createdb -O $APP_NAME -E UTF8 -T template0 $APP_NAME"
    ok "已创建数据库 $APP_NAME"
  fi
  set_env DATABASE_URL "postgresql://$APP_NAME:$pw@127.0.0.1:5432/$APP_NAME"
}

# ---------------------------------------------------------------- 代码
fetch_code() {
  git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true
  if [[ -d "$APP_DIR/.git" ]]; then
    step "获取代码（$(git -C "$APP_DIR" remote get-url origin | sed -E 's#//[^/@]*@#//#')，分支 $BRANCH）"
    OLD_REV="$(git -C "$APP_DIR" rev-parse --short HEAD 2>/dev/null || echo none)"
    git -C "$APP_DIR" fetch --depth 50 origin "$BRANCH"
    if [[ -n "$(git -C "$APP_DIR" status --porcelain --untracked-files=no)" ]]; then
      warn "安装目录里有被手动改过的代码文件，将被覆盖："
      git -C "$APP_DIR" status --short --untracked-files=no | sed 's/^/        /'
      confirm "继续？" || die "已取消"
    fi
    git -C "$APP_DIR" checkout -q -B "$BRANCH" "origin/$BRANCH"
    git -C "$APP_DIR" reset -q --hard "origin/$BRANCH"
  else
    step "获取代码（$REPO_URL，分支 $BRANCH）"
    if [[ -e "$APP_DIR" && -n "$(ls -A "$APP_DIR" 2>/dev/null)" ]]; then die "$APP_DIR 已存在且不是 git 仓库，请换 --dir 或先移走它"; fi
    OLD_REV="none"
    git clone --depth 50 --branch "$BRANCH" "$REPO_URL" "$APP_DIR" || die "克隆失败。仓库若是私有的，请先手动 git clone 到 $APP_DIR 再运行本脚本"
  fi
  NEW_REV="$(git -C "$APP_DIR" rev-parse --short HEAD)"
  if [[ "$OLD_REV" == "$NEW_REV" ]]; then ok "代码已是最新（$NEW_REV）"
  elif [[ "$OLD_REV" == "none" ]]; then ok "已获取代码（$NEW_REV）"
  else
    ok "代码 $OLD_REV → $NEW_REV"
    git -C "$APP_DIR" log --oneline "$OLD_REV..$NEW_REV" 2>/dev/null | head -20 | sed 's/^/        /' || true
  fi
}

# ---------------------------------------------------------------- 配置文件
set_env() {
  local key="$1" val="$2" f; f="$(ENV_FILE)"
  touch "$f"
  if grep -qE "^$key=" "$f"; then
    local esc; esc=$(printf '%s' "$val" | sed -e 's/[\/&|]/\\&/g')
    sed -i "s|^$key=.*|$key=$esc|" "$f"
  else
    echo "$key=$val" >> "$f"
  fi
}

get_env() { grep -E "^$1=" "$(ENV_FILE)" 2>/dev/null | tail -1 | cut -d= -f2- ; }

setup_config() {
  step "生成配置文件"
  local f; f="$(ENV_FILE)"
  if [[ ! -f "$f" ]]; then
    cp "$APP_DIR/.env.example" "$f"
    ok "已从 .env.example 生成 .env"
  fi
  local sk; sk="$(get_env SECRET_KEY)"
  if [[ -z "$sk" || "$sk" == *请改* || "$sk" == "dev-secret-change-me" ]]; then
    set_env SECRET_KEY "$(head -c 48 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    ok "已生成随机 SECRET_KEY"
  fi
  if [[ -n "$DOMAIN" ]]; then
    set_env HTTPS_ONLY 1
    set_env PUBLIC_URL "https://${DOMAIN%%,*}"
  fi
  mkdir -p "$APP_DIR/config" "$APP_DIR/data/backups"
  if [[ ! -f "$APP_DIR/config/llm.toml" ]]; then
    cp "$APP_DIR/config/llm.example.toml" "$APP_DIR/config/llm.toml"
    ok "已生成 config/llm.toml（请填写 AI 的 api_key）"
  else
    ok "保留已有的 config/llm.toml"
  fi
  if [[ ! -f "$APP_DIR/config/tts.toml" ]]; then
    cp "$APP_DIR/config/tts.example.toml" "$APP_DIR/config/tts.toml"
    ok "已生成 config/tts.toml（朗读：要用时填写 Cartesia 的 api_key 和声音）"
  fi
}

fix_permissions() {
  id "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$SERVICE_USER" 2>/dev/null \
    || useradd -r -d "$APP_DIR" -s /sbin/nologin "$SERVICE_USER"
  chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR/data"
  chown "root:$SERVICE_USER" "$(ENV_FILE)" "$APP_DIR/config/llm.toml" "$APP_DIR/config/tts.toml"
  chmod 640 "$(ENV_FILE)" "$APP_DIR/config/llm.toml" "$APP_DIR/config/tts.toml"
}

# ---------------------------------------------------------------- Python 依赖
setup_venv() {
  step "安装 Python 依赖"
  find_python || die "没有 Python 3.$MIN_PY_MINOR+"
  local want; want="$($PYTHON -c 'import sys;print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
  if [[ -x "$VENV/bin/python" ]]; then
    local have; have="$("$VENV/bin/python" -c 'import sys;print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || echo none)"
    [[ "$have" == "$want" ]] || { warn "虚拟环境 Python $have 与系统 $want 不同，重建"; rm -rf "$VENV"; }
  fi
  [[ -x "$VENV/bin/python" ]] || "$PYTHON" -m venv "$VENV"
  local pipargs=(-q --disable-pip-version-check)
  if [[ "$MIRROR" == "cn" ]]; then pipargs+=(-i https://pypi.tuna.tsinghua.edu.cn/simple); fi
  "$VENV/bin/pip" install "${pipargs[@]}" --upgrade pip wheel
  "$VENV/bin/pip" install "${pipargs[@]}" -r "$APP_DIR/requirements.txt"
  ok "依赖已安装（$("$VENV/bin/python" --version)）"
}

# ---------------------------------------------------------------- 数据库迁移
run_cli() { (cd "$APP_DIR" && runuser -u "$SERVICE_USER" -- "$VENV/bin/python" -m app.cli "$@"); }

backup_db() {
  step "备份数据库"
  if run_cli migrate-status >/dev/null 2>&1 && run_cli migrate-status | grep -q 已执行; then
    run_cli backup "$APP_DIR/data/backups" | sed 's/^/  /'
    # 只保留最近 20 份
    ls -1t "$APP_DIR"/data/backups/aistudy-* 2>/dev/null | tail -n +21 | xargs -r rm -f
  else
    ok "数据库还是空的，跳过备份"
  fi
}

migrate_db() {
  step "迁移数据库"
  run_cli migrate | sed 's/^/  /'
  step "书库：下载还没有的公版名著原文（失败不影响安装，之后可在管理后台重试）"
  (cd "$APP_DIR" && timeout 900 runuser -u "$SERVICE_USER" -- "$VENV/bin/python" -m app.cli library fetch) 2>&1 | sed 's/^/  /' \
    || warn "书库下载没完成，稍后在「管理 → 书库」里点「下载所有还没有的」"
}

# ---------------------------------------------------------------- 服务
write_service() {
  step "注册系统服务"
  if ! has_systemd; then
    warn "没有 systemd，跳过。可手动运行：cd $APP_DIR && sudo -u $SERVICE_USER $VENV/bin/uvicorn app.main:app --host 0.0.0.0 --port $PORT"
    return
  fi
  local host="127.0.0.1"
  [[ -n "$DOMAIN" ]] || host="0.0.0.0"
  if [[ -f "/etc/systemd/system/$APP_NAME.service" ]] && ! grep -q -- "--port $PORT" "/etc/systemd/system/$APP_NAME.service" && [[ "$CMD" == "upgrade" || "$CMD" == "domain" ]]; then
    PORT="$(grep -oE -- '--port [0-9]+' "/etc/systemd/system/$APP_NAME.service" | awk '{print $2}')"
    grep -q -- "--host 127.0.0.1" "/etc/systemd/system/$APP_NAME.service" && host="127.0.0.1"
  fi
  cat > "/etc/systemd/system/$APP_NAME.service" <<EOF
[Unit]
Description=AIStudy 学习系统
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$APP_DIR
ExecStart=$VENV/bin/uvicorn app.main:app --host $host --port $PORT --workers $WORKERS --proxy-headers --forwarded-allow-ips 127.0.0.1
Restart=always
RestartSec=3
NoNewPrivileges=true
ProtectSystem=full
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable "$APP_NAME" >/dev/null 2>&1
  systemctl restart "$APP_NAME"
  ok "服务 $APP_NAME 已启动（监听 $host:$PORT）"
}

setup_caddy() {
  [[ -n "$DOMAIN" ]] || return 0
  step "配置 HTTPS（Caddy，自动申请和续期证书）"
  # 80/443 已被 nginx / Apache 等占用时，Caddy 起不来；不要假装成功，提示改那边的配置
  local other
  other="$(ss -ltnp 2>/dev/null | grep -E '[:.](80|443)[[:space:]]' | grep -oE '\(\("[^"]+"' | tr -d '("' | grep -vx caddy | sort -u | tr '\n' ' ' || true)"
  if [[ -n "$other" ]]; then
    warn "80/443 端口已被 ${other}占用，跳过 Caddy"
    warn "请在 ${other}里把 ${DOMAIN//,/ } 反向代理到 127.0.0.1:$PORT，并为它申请证书。nginx 示例："
    echo "      server_name ${DOMAIN//,/ };"
    echo "      location / { proxy_pass http://127.0.0.1:$PORT; proxy_set_header Host \$host;"
    echo "                   proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for; proxy_set_header X-Forwarded-Proto \$scheme; }"
    echo "      证书：certbot --nginx -d ${DOMAIN//,/ -d }"
    return 0
  fi
  if ! command -v caddy >/dev/null; then
    case "$PKG" in
      apt)
        pkg_install debian-keyring debian-archive-keyring apt-transport-https gnupg
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
        apt-get update -q && pkg_install caddy ;;
      dnf|yum)
        pkg_install 'dnf-command(copr)' || true
        dnf copr enable -y @caddy/caddy && pkg_install caddy ;;
    esac
  fi
  command -v caddy >/dev/null || die "Caddy 安装失败。也可以自己用 Nginx 反向代理到 127.0.0.1:$PORT"
  cat > /etc/caddy/Caddyfile <<EOF
# 由 AIStudy install.sh 生成
${DOMAIN//,/, } {
	encode gzip
	reverse_proxy 127.0.0.1:$PORT
}
EOF
  systemctl enable caddy >/dev/null 2>&1
  systemctl reload caddy 2>/dev/null || systemctl restart caddy
  ok "https://${DOMAIN//,/ https://} → 127.0.0.1:$PORT"
}

# 在本机直接访问 https://域名（不经过外网），确认 Caddy 证书和应用都正常。
# 本机通、外面打不开（例如 403），说明请求在到达服务器之前就被拦了：云厂商未备案拦截、CDN / WAF、DNS 指错等。
local_https_check() {
  local d out good
  for d in ${DOMAIN//,/ }; do
    good=""
    for _ in $(seq 1 30); do
      if out="$(curl -fsS -m 10 --resolve "$d:443:127.0.0.1" "https://$d/healthz" 2>&1)"; then good=1; break; fi
      sleep 2
    done
    if [[ -n "$good" ]]; then ok "本机访问 https://$d 正常"
    else
      warn "本机访问 https://$d 失败：$out"
      warn "多半是证书没申请下来：看 journalctl -u caddy -n 50；确认域名 A 记录指向本机、安全组放行 80/443"
    fi
  done
}

health_check() {
  step "健康检查"
  has_systemd || { warn "没有 systemd，跳过"; return 0; }
  local port; port="$(grep -oE -- '--port [0-9]+' "/etc/systemd/system/$APP_NAME.service" 2>/dev/null | awk '{print $2}')"
  for _ in $(seq 1 30); do
    if out="$(curl -fsS "http://127.0.0.1:${port:-$PORT}/healthz" 2>/dev/null)"; then ok "应用正常：$out"; return 0; fi
    sleep 1
  done
  fail "应用没有正常响应。最近日志："
  journalctl -u "$APP_NAME" -n 40 --no-pager || true
  return 1
}

summary() {
  local ip; ip="$(curl -fsS -m 3 https://ifconfig.me 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')"
  echo
  echo "${C_G}${C_B}AIStudy 已就绪${C_0}"
  if [[ -n "$DOMAIN" ]]; then echo "  访问地址：https://$DOMAIN"; else echo "  访问地址：http://${ip:-服务器IP}:$PORT  （记得在云服务器安全组放行 $PORT 端口）"; fi
  echo "  第一次打开时注册的账号会成为管理员。"
  echo
  echo "  AI 配置：  sudo nano $APP_DIR/config/llm.toml   填 api_key 后执行  sudo systemctl restart $APP_NAME"
  echo "  朗读配置：sudo nano $APP_DIR/config/tts.toml   （可选，不填就用浏览器自带的声音）"
  echo "  检查配置：sudo $APP_DIR/install.sh check --llm"
  echo "  以后升级：sudo $APP_DIR/install.sh upgrade"
  echo "  数据备份：$APP_DIR/data/backups（每次升级前自动备份）"
}

# ---------------------------------------------------------------- 命令
cmd_install() {
  need_root
  echo "${C_B}AIStudy 安装${C_0}  目录 $APP_DIR · 仓库 $REPO_URL ($BRANCH)${DOMAIN:+ · 域名 $DOMAIN}"
  check_env || die "环境检测未通过"
  confirm "开始安装？" || die "已取消"
  install_packages
  fetch_code
  setup_config
  setup_database
  setup_venv
  fix_permissions
  backup_db
  migrate_db
  run_cli check | sed 's/^/  /' || true
  write_service
  setup_caddy
  health_check
  summary
}

cmd_upgrade() {
  need_root
  [[ -d "$APP_DIR/.git" ]] || die "$APP_DIR 里没有安装 AIStudy，请先运行 install"
  detect_os
  find_python || die "没有 Python 3.$MIN_PY_MINOR+"
  fetch_code
  setup_config
  setup_database
  setup_venv
  fix_permissions
  backup_db
  migrate_db
  write_service
  setup_caddy
  if ! health_check; then
    echo
    warn "升级后服务没起来。回退代码：sudo git -C $APP_DIR reset --hard $OLD_REV && sudo systemctl restart $APP_NAME"
    warn "如需恢复数据库，备份在 $APP_DIR/data/backups（最新一份是升级前的）"
    exit 1
  fi
  echo; echo "${C_G}${C_B}升级完成：$OLD_REV → $NEW_REV${C_0}"
}

# 换域名：只改 Caddy 和 .env 里的 PUBLIC_URL，不动代码和数据
cmd_domain() {
  need_root
  [[ -n "$DOMAIN" ]] || die "用法：install.sh domain --domain 新域名（多个用逗号隔开，如 beejoy.ai,www.beejoy.ai）"
  [[ -f "$(ENV_FILE)" ]] || die "$APP_DIR 里没有安装 AIStudy，请先运行 install"
  detect_os
  check_env || true
  local old; old="$(get_env PUBLIC_URL)"
  step "更新 .env"
  set_env HTTPS_ONLY 1
  set_env PUBLIC_URL "https://${DOMAIN%%,*}"
  ok "PUBLIC_URL：${old:-（空）} → https://${DOMAIN%%,*}"
  # 以前没绑域名时应用监听 0.0.0.0，现在改成只给 Caddy 访问
  write_service
  setup_caddy
  health_check
  step "从本机检查 HTTPS"
  local_https_check
  echo
  echo "  如果本机检查正常，但浏览器打开新域名报 403 / 打不开，问题在服务器外面："
  echo "  · 服务器在中国大陆：新域名必须在这家云厂商做 ICP 备案（并非所有后缀都能备案），否则 80/443 会被拦截"
  echo "  · 域名开了 CDN / 代理（如 Cloudflare 橙色云朵、阿里云 ESA）：检查它的 WAF 规则，或先关掉代理直连"
  echo "  · 用 dig +short ${DOMAIN%%,*} 确认解析到的是这台服务器的公网 IP"
}

cmd_check() {
  [[ -d "$APP_DIR/app" ]] || die "$APP_DIR 里没有安装 AIStudy"
  check_env || true
  step "检测应用"
  if [[ $EUID -eq 0 ]]; then run_cli check $WITH_LLM | sed 's/^/  /'
  else (cd "$APP_DIR" && "$VENV/bin/python" -m app.cli check $WITH_LLM) | sed 's/^/  /'; fi
  if has_systemd; then
    if systemctl is-active --quiet "$APP_NAME"; then ok "服务 $APP_NAME 运行中"; else warn "服务 $APP_NAME 没有运行"; fi
  fi
}

case "$CMD" in
  install) cmd_install ;;
  upgrade|update) cmd_upgrade ;;
  check) cmd_check ;;
  domain) cmd_domain ;;
  backup) need_root; run_cli backup "$APP_DIR/data/backups" ;;
  status) systemctl status "$APP_NAME" --no-pager ;;
  logs) journalctl -u "$APP_NAME" -n 200 --no-pager ;;
  restart) need_root; systemctl restart "$APP_NAME"; health_check ;;
  help|-h|--help|*) sed -n '2,40p' "${BASH_SOURCE[0]:-/dev/null}" 2>/dev/null | sed 's/^# \{0,1\}//' || echo "用法：install.sh install|upgrade|domain|check|backup|status|logs|restart" ;;
esac
