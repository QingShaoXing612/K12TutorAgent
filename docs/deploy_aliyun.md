# K12TutorAgent 阿里云部署清单

> 目标：把本项目从 Windows 本地搬到阿里云 ECS，用于面试演示，一次跑通。
> 规格建议：**成都地域 · 4C16G · 60G ESSD · 5M 带宽**（内存是瓶颈：3 个本地模型常驻 ~6G + Milvus）。
> 系统建议：**Ubuntu 22.04**（Python 3.11 需额外装，见第 4 节）。

---

## 0. 本地准备（Windows 上做，避开服务器端跨境下载）

### 0.1 打包代码（不含 .venv / 缓存）

```bash
# 在项目根目录 C:\Projects\TeachMateAgent 下，用 Git Bash 跑（cmd/PowerShell 的 \ 续行不生效）
# 在项目根目录 C:\Projects\TeachMateAgent 下，用 Git Bash 跑（cmd/PowerShell 的 \ 续行不生效）
# 输出先写 /tmp 再 mv 回来：避免 tar 把「正在写的输出文件」也读进 . 目录，触发 "file changed" 警告(exit≠0)
tar czf /tmp/teachmate_code.tar.gz \
  --exclude=.venv --exclude=__pycache__ --exclude=.pytest_cache \
  --exclude=.idea --exclude=.claude --exclude=models \
  --exclude=.env.local \
  --exclude='*.tar' --exclude='*.tar.gz' \
  . \
  && mv /tmp/teachmate_code.tar.gz ./teachmate_code.tar.gz
# 两个必须加的 exclude：
#  --exclude=.env.local    密钥(DeepSeek key/JWT secret/DB 密码)绝不进代码包，云上第 5 节手动新建
#  --exclude='*.tar*'      避免重复打包时把已存在的 teachmate_code.tar.gz 又打进去（体积翻倍）
```

### 0.2 打包模型（5.5G，二进制，单独传）

```bash
tar czf /tmp/teachmate_models.tar.gz models \
  && mv /tmp/teachmate_models.tar.gz ./teachmate_models.tar.gz
```

### 0.3 离线导出 Docker 镜像（从虚拟机 <你的虚拟机IP> 导出，不是本机）

> 为什么这步是重点：`quay.io/coreos/etcd` 来自 **quay.io**（不是 Docker Hub），国内镜像加速器**加速不到**，云服务器直接 `docker pull` 大概率失败/极慢。而你的 PG + Milvus 实际跑在**虚拟机 `<你的虚拟机IP>`** 上，镜像在那台机器里——所以从它导出，本机 Docker Desktop 里没有。

```bash
# ① 先 SSH 到虚拟机，确认镜像在、tag 和 docker-compose.yml 一致
ssh YOUR_USER@<你的虚拟机IP> "docker images"

# ② 在虚拟机上导出（写到家目录，避免 /tmp 空间不足；docker 需 sudo 则前面加 sudo）
ssh YOUR_USER@<你的虚拟机IP> "docker save \
  postgres:15-alpine \
  quay.io/coreos/etcd:v3.5.14 \
  minio/minio:latest \
  milvusdb/milvus:v2.4.0 \
  zilliz/attu:v2.4.12 \
  -o ~/teachmate_images.tar"

# ③ 拉回本机（Windows，Git Bash 里跑）
scp YOUR_USER@<你的虚拟机IP>:~/teachmate_images.tar .
```

> 把 `YOUR_USER` 换成你 SSH 虚拟机用的实际用户名（**不要带尖括号**——那只是占位符标记，不是命令的一部分）。导出包约 **1G**（等于各镜像压缩层 `CONTENT SIZE` 总和 ≈ 823MB，不是 `DISK USAGE`），内网 scp 很快。第①步的 `docker images` 一定要先看一眼：如果某个镜像的 TAG 和上面写的不一致（比如 minio 不是 `latest`），`docker save` 会报错，用实际 tag 替换即可。

### 0.4 上传（三件套：代码包 + 模型包 + 镜像包）

```bash
scp teachmate_code.tar.gz   root@<你的公网IP>:/opt/
scp teachmate_models.tar.gz root@<你的公网IP>:/opt/
scp teachmate_images.tar    root@<你的公网IP>:/opt/
```

---

## 1. 服务器初始化（换 apt 源）

```bash
# Ubuntu 22.04 换阿里云 apt 源
sudo sed -i 's@//.*archive.ubuntu.com@//mirrors.aliyun.com@g;s@//.*security.ubuntu.com@//mirrors.aliyun.com@g' /etc/apt/sources.list
sudo apt update
```

---

## 2. 装 Docker + Compose

```bash
# Docker
curl -fsSL https://get.docker.com | sh    # 若慢，改用 apt 装 docker.io 亦可
sudo systemctl enable --now docker

# 若使用国内 Docker 镜像加速（Docker Hub 镜像加速，etcd 不走这里）
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json <<'EOF'
{ "registry-mirrors": ["https://<你的阿里云加速地址>.mirror.aliyuncs.com"] }
EOF
sudo systemctl daemon-reload && sudo systemctl restart docker
```

> 加速地址在阿里云控制台 →「容器镜像服务 ACR」→「镜像工具 → 镜像加速器」里，**每人一个专属地址**，复制替换上面的占位符。

---

## 3. 解压 + 导入镜像

```bash
mkdir -p /opt/teachmate
tar xzf /opt/teachmate_code.tar.gz   -C /opt/teachmate
tar xzf /opt/teachmate_models.tar.gz -C /opt/teachmate

# 导入离线镜像（免跨境拉取）
docker load -i /opt/teachmate_images.tar
docker images   # 确认 5 个镜像都在
```

---

## 4. Python 3.11 + 依赖（换 pip 源）

```bash
# 装 Python 3.11（Ubuntu 22.04 默认 3.10，项目锁 3.11）
sudo apt install -y software-properties-common
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3.11-dev

# pip 换阿里云源
mkdir -p ~/.pip
cat > ~/.pip/pip.conf <<'EOF'
[global]
index-url = https://mirrors.aliyun.com/pypi/simple/
trusted-host = mirrors.aliyun.com
EOF

# 建 venv + 装依赖（torch 走 PyPI 默认 CPU 版，无需 GPU）
cd /opt/teachmate
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> 依赖安装（torch/transformers 等）约 3~5 分钟，走阿里云 pip 源基本全速。

---

## 5. 环境变量（脱敏模板）

在 `/opt/teachmate` 下新建 `.env.local`（**密钥全部换成新的**，别用本地明文）：

```ini
# ===== 数据库（PostgreSQL，云上 docker 本地，改 127.0.0.1）=====
DB_HOST=127.0.0.1
DB_PORT=5433
DB_NAME=eduagent
DB_USER=eduagent_user
DB_PASSWORD=<生成一个新密码>

# ===== Milvus 向量库（同样改 127.0.0.1）=====
MILVUS_HOST=127.0.0.1
MILVUS_PORT=19531

# ===== DeepSeek 大模型 =====
DEEPSEEK_API_KEY=<你的 DeepSeek key>
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL_CHAT=deepseek-chat
DEEPSEEK_MODEL_CODER=deepseek-coder

# ===== 本地模型路径（相对项目根目录，无需改）=====
RERANKER_MODEL_PATH=./models/reranker/bge-reranker-large
CLASSIFIER_MODEL_PATH=./models/classifier/all-MiniLM-L6-v2
BGE_M3_MODEL_PATH=./models/embedding/bge-m3

# ===== JWT 认证（换新）=====
JWT_SECRET_KEY=<生成一段新的随机 64 位 hex>
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=10080

# ===== Web 搜索（可留空走 DuckDuckGo）=====
TAVILY_API_KEY=

# ===== 应用配置 =====
APP_ENV=production
APP_DEBUG=false
APP_HOST=0.0.0.0
APP_PORT=8000
LOG_LEVEL=INFO
DEFAULT_TENANT_ID=tenant_default
```

生成随机密钥：`openssl rand -hex 32`

---

## 6. 起基础设施（PostgreSQL + Milvus + etcd + MinIO + Attu）

```bash
cd /opt/teachmate
docker compose --env-file .env.local up -d
docker compose ps          # 等 postgres/milvus 变 healthy
```

---

## 7. 建库 + 灌数据

```bash
cd /opt/teachmate
source .venv/bin/activate

PYTHONUTF8=1 PYTHONPATH=. .venv/bin/python test/run_init_db.py          # 建表
PYTHONUTF8=1 PYTHONPATH=. .venv/bin/python scripts/init_milvus.py       # Milvus 集合
PYTHONUTF8=1 PYTHONPATH=. .venv/bin/python scripts/seed_data.py         # 测试账号
PYTHONUTF8=1 PYTHONPATH=. .venv/bin/python scripts/build_knowledge_base.py   # 知识库
PYTHONUTF8=1 PYTHONPATH=. .venv/bin/python scripts/seed_exam_math.py    # 试卷
PYTHONUTF8=1 PYTHONPATH=. .venv/bin/python scripts/seed_lesson_data.py  # 备课（习题 34 道）
PYTHONUTF8=1 PYTHONPATH=. .venv/bin/python scripts/seed_classes.py      # 班级（作业/学情下拉）
PYTHONUTF8=1 PYTHONPATH=. .venv/bin/python scripts/seed_learning_data.py # 学情基础（干预策略+Milvus）
PYTHONUTF8=1 PYTHONPATH=. .venv/bin/python scripts/seed_learning_data_plus.py # 学情扩充（30 学生+practice+exam 批改）
# 高中数据（演示需覆盖高中则加）
PYTHONUTF8=1 PYTHONPATH=. .venv/bin/python scripts/seed_high_data.py
```

---

## 8. 起服务（systemd 常驻）

```bash
sudo tee /etc/systemd/system/teachmate.service <<'EOF'
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
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now teachmate
sudo systemctl status teachmate    # 看是否 active (running)
```

> 首次启动会预热 3 个本地模型，**30 秒 ~ 2 分钟**（日志里出现 `app.local_models_warmed_up` 才算就绪）。日志：`journalctl -u teachmate -f`。

---

## 9. nginx 反代（可选，让访问不带 :8000，并保障 SSE 流式）

```bash
sudo apt install -y nginx
sudo tee /etc/nginx/sites-available/teachmate <<'EOF'
server {
    listen 80;
    server_name _;

    client_max_body_size 50m;   # 试卷 docx / PDF 上传

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection '';
        proxy_buffering off;          # ← SSE 流式关键，不缓冲
        proxy_cache off;
        proxy_read_timeout 600s;      # ← 长连接，避免流式中途断
    }
}
EOF

sudo ln -s /etc/nginx/sites-available/teachmate /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

> 若不用 nginx，直接在安全组开 **8000** 端口，用 `http://<IP>:8000` 访问，少一层但访问带端口号。

---

## 10. 安全组（阿里云控制台）

只开这些入方向端口，其余全关：

| 端口 | 用途 | 允许来源 |
|---|---|---|
| 22 | SSH | 你的 IP（或临时 0.0.0.0/0） |
| 80（或 8000） | Web 演示 | 0.0.0.0/0 |
| ~~5433 / 19531 / 30000~~ | **绝不开** | PG / Milvus / Attu 无鉴权，裸奔必被扫 |

---

## 11. 验证清单（一条条过）

```bash
# 本机健康检查
curl http://127.0.0.1:8000/health          # → {"status":"ok"}

# 公网健康检查（浏览器 / 另一台设备）
curl http://<你的公网IP>/health

# 登录（测试账号）
curl -X POST http://<你的公网IP>/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"teacher01","password":"Teacher@123456"}'
```

浏览器打开 `http://<你的公网IP>/`，用 `teacher01 / Teacher@123456` 登录，走一遍四大 Agent 即可。

---

## 12. 常见坑速查

| 现象 | 根因 | 解法 |
|---|---|---|
| 模型预热极慢 / 被 kill | 内存 < 16G，3 模型 + Milvus OOM | 加 swap：`fallocate -l 8G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile` |
| `etcd` 镜像拉不下来 | quay.io 无国内加速 | 用第 0.3 节离线导出，别在服务器 pull |
| 前端不流式 | nginx 缓冲了 SSE | 确认 `proxy_buffering off` |
| 启动报 `libpq` / `psycopg` | Linux 缺系统库 | `sudo apt install -y libpq-dev gcc` |
| `APP_ENV=production` 但日志是 INFO | 正常，`LOG_LEVEL` 单独控制 | 演示可保持 INFO |
| 端口 5433/19531 连接被拒 | 忘了改 `DB_HOST`/`MILVUS_HOST` 为 `127.0.0.1` | 见第 5 节 |

---

## 13. 面试当天秒开（抢占式调试 → 打镜像 → 按量秒开 → 演完释放）

准备阶段用**抢占式实例**（便宜、可回收、环境可重建）；环境配好验证通过后冻成**自定义镜像**；面试当天用**按量付费 + 镜像**秒开；演示完**立刻释放**。当天成本 ≈ ¥3。

### 13.1 环境配好后，打自定义镜像

1. 控制台 → 云服务器 ECS → 实例列表 → 选中抢占式实例 → 更多 →「磁盘和镜像」→「创建自定义镜像」。
2. 镜像名填 `k12tutor-demo`，确定（阿里云会先停机再打镜像，几分钟完成）。
3. 镜像打完后，这台抢占式实例可释放（省几毛钱）；代码资产在本地 + 镜像里各有一份，不会丢。

### 13.2 面试当天，用镜像秒开按量付费实例

1. 控制台 → 创建实例 → 镜像选「**自定义镜像**」→ `k12tutor-demo`。
2. 付费方式选「**按量付费**」，规格 `ecs.e-c1m4.xlarge`（4C16G），选好安全组、带宽。
3. 开机 1~2 分钟，环境/代码/数据/模型**全都在**，直接 `http://<IP>/` 演示。

### 13.3 演示完立刻释放，别再计费

按量付费实例「停止」只停 CPU/内存计费，**系统盘仍计费**。要彻底不花钱：

- 实例列表 → 选中实例 →「释放」→ 勾选「同时释放云盘」。
- 自定义镜像本身是独立资源，不受释放影响，下次还能秒开。

> 口诀：抢占式搭台、镜像存档、按量付费唱戏、演完释放。环境不丢、现场不塌、账单几块钱。

---

## 附：一条命令复现（建库 → 起服务全量）

```bash
cd /opt/teachmate && source .venv/bin/activate && \
for f in test/run_init_db.py scripts/init_milvus.py scripts/seed_data.py \
         scripts/build_knowledge_base.py scripts/seed_exam_math.py \
         scripts/seed_lesson_data.py scripts/seed_classes.py \
         scripts/seed_learning_data.py scripts/seed_learning_data_plus.py; do \
  PYTHONUTF8=1 PYTHONPATH=. .venv/bin/python "$f"; done && \
sudo systemctl restart teachmate
```
