#!/bin/bash
# ============================================================
# K12TutorAgent 一键部署脚本（阿里云 Alibaba Cloud Linux 3 / OpenAnolis，root 执行）
# 前置：teachmate_code.tar.gz / teachmate_models.tar.gz / teachmate_images.tar 已在 /root/
# 用法：bash /root/deploy_alinux.sh
# 与 deploy.sh(Ubuntu) 的差异：
#   apt → dnf；Python 用系统自带 3.12(/opt/aiext/bin/python3)；不换源(Anolis 默认阿里云源)
# ============================================================
set -e

log() { echo -e "\n\033[1;32m===== $1 =====\033[0m"; }

# ---------- 0. 校验三件套 ----------
log "0. 校验三件套"
cd /root
for f in teachmate_code.tar.gz teachmate_models.tar.gz teachmate_images.tar; do
  [ -f "$f" ] || { echo "[错误] 缺少 $f，请先上传到 /root/"; exit 1; }
done
ls -lh teachmate_code.tar.gz teachmate_models.tar.gz teachmate_images.tar

# ---------- 1. 确认 Python 3.12 ----------
log "1. 确认 Python 3.12（系统自带 aiext）"
PY=/opt/aiext/bin/python3
[ -x "$PY" ] || { echo "[错误] 未找到 $PY（aiext Python 3.12）"; exit 1; }
$PY --version

# ---------- 2. 装基础工具（dnf） ----------
log "2. 装基础工具（gcc / gcc-c++ / curl / libpq-devel）"
dnf install -y gcc gcc-c++ curl libpq-devel

# ---------- 3. 装 Docker + Compose 插件 ----------
log "3. 装 Docker + Compose 插件（阿里云 dnf 源）"
if ! command -v docker >/dev/null 2>&1; then
  dnf install -y dnf-plugins-core
  dnf config-manager --add-repo https://mirrors.aliyun.com/docker-ce/linux/centos/docker-ce.repo
  # Alibaba Cloud Linux 3 的 $releasever 解析非 8，强制钉成 8（CentOS 8 兼容仓库）
  sed -i 's/\$releasever/8/g' /etc/yum.repos.d/docker-ce.repo
  dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi
systemctl enable --now docker
docker version --format 'Docker {{.Server.Version}}'

# ---------- 4. 解压 + 导入镜像 ----------
log "4. 解压 + 导入镜像"
mkdir -p /opt/teachmate
tar xzf /root/teachmate_code.tar.gz    -C /opt/teachmate
tar xzf /root/teachmate_models.tar.gz  -C /opt/teachmate
docker load -i /root/teachmate_images.tar

# ---------- 5. pip 换源 + 建 venv + 装依赖 ----------
log "5. pip 换源 + 装依赖（约 5~10 分钟）"
mkdir -p ~/.pip
cat > ~/.pip/pip.conf <<'PYEOF'
[global]
index-url = https://mirrors.aliyun.com/pypi/simple/
trusted-host = mirrors.aliyun.com
PYEOF
cd /opt/teachmate
$PY -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# ---------- 6. 生成 .env.local ----------
log "6. 配置 .env.local"
cat > /opt/teachmate/.env.local <<'ENVEOF'
DB_HOST=127.0.0.1
DB_PORT=5433
DB_NAME=eduagent
DB_USER=eduagent_user
DB_PASSWORD=<请填写数据库密码>
MILVUS_HOST=127.0.0.1
MILVUS_PORT=19531
DEEPSEEK_API_KEY=<请填写DeepSeek的APIKey>
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL_CHAT=deepseek-chat
DEEPSEEK_MODEL_CODER=deepseek-coder
RERANKER_MODEL_PATH=./models/reranker/bge-reranker-large
CLASSIFIER_MODEL_PATH=./models/classifier/all-MiniLM-L6-v2
BGE_M3_MODEL_PATH=./models/embedding/bge-m3
JWT_SECRET_KEY=<请填写64位随机hex>
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=10080
TAVILY_API_KEY=<留空则用DuckDuckGo>
APP_ENV=production
APP_DEBUG=false
APP_HOST=0.0.0.0
APP_PORT=8000
LOG_LEVEL=INFO
DEFAULT_TENANT_ID=tenant_default
ENVEOF
echo "[OK] .env.local 已生成（含 DeepSeek/JWT 密钥，部署后可考虑轮换）"

# ---------- 7. 起基础设施 ----------
log "7. 起 PostgreSQL + Milvus（docker compose）"
cd /opt/teachmate
docker compose --env-file .env.local up -d
sleep 20
docker compose ps

# ---------- 8. 建库灌数据 ----------
# 顺序有依赖：seed_exam_math(演示卷) → seed_lesson_data(习题34) → seed_classes(班级) → seed_learning_data(干预策略+Milvus) → seed_learning_data_plus(30学生+exam批改)
log "8. 建库 + 灌数据"
# run_init_db.py 内部 open("../scripts/init_db.sql") 依赖 CWD=test/，单独在 test 子目录跑
(cd test && PYTHONUTF8=1 PYTHONPATH=/opt/teachmate /opt/teachmate/.venv/bin/python run_init_db.py)
# 应用 migrations（补 init_db.sql 未同步的列，如 exams.subject；幂等，IF NOT EXISTS）
PYTHONUTF8=1 PYTHONPATH=. .venv/bin/python -c "import asyncio; from backend.db.migrations import run_migrations; asyncio.run(run_migrations())"
for f in scripts/init_milvus.py scripts/seed_data.py scripts/build_knowledge_base.py scripts/seed_exam_math.py scripts/seed_lesson_data.py scripts/seed_classes.py scripts/seed_learning_data.py scripts/seed_learning_data_plus.py scripts/seed_high_data.py; do
  echo "--- 运行 $f ---"
  PYTHONUTF8=1 PYTHONPATH=. .venv/bin/python "$f"
done

# ---------- 9. systemd 起服务 ----------
log "9. systemd 常驻服务"
cat > /etc/systemd/system/teachmate.service <<'SVCEOF'
[Unit]
Description=K12TutorAgent
After=network.target docker.service

[Service]
User=root
WorkingDirectory=/opt/teachmate
Environment=PYTHONUTF8=1
Environment=PYTHONPATH=/opt/teachmate
ExecStart=/opt/teachmate/.venv/bin/python -m backend.main
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SVCEOF
systemctl daemon-reload
systemctl enable --now teachmate

# ---------- 10. 等预热 + 验证 ----------
log "10. 等待服务预热（首次加载 3 个本地模型，1~2 分钟）"
for i in $(seq 1 30); do
  if curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; then
    echo "[OK] health 已通：$(curl -s http://127.0.0.1:8000/health)"
    break
  fi
  echo "等待服务就绪... $i/30"
  sleep 20
done

echo -e "\n\033[1;33m===== 部署完成 =====\033[0m"
echo "浏览器访问：http://<你的公网IP>:8000/   登录：teacher01 / Teacher@123456"
echo "前提：安全组入方向放行 TCP 8000（源 0.0.0.0/0）"
echo "排查日志：journalctl -u teachmate -n 50"
