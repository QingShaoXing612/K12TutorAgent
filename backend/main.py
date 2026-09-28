# backend/main.py

import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Windows 下 psycopg3 的 async 模式不能用默认的 ProactorEventLoop，需切到 SelectorEventLoop
# （须在任何事件循环创建前设置，否则持久化 checkpointer 连接失败）
if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backend.config import get_settings
from backend.core.logger import configure_logging, get_logger
from backend.api.router import api_router

settings = get_settings()

# MCP Sub-Apps 在 lifespan 定义前创建，这样 lifespan 与下面的 mount 共享同一实例
from backend.mcp_servers.knowledge_base_server import mcp as kb_mcp  # 知识库 MCP
from backend.mcp_servers.web_search_server import mcp as ws_mcp      # 联网搜索 MCP


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()                                  # 初始化结构化日志
    logger = get_logger(__name__)
    logger.info("app.starting | env=%s port=%s", settings.app_env, settings.app_port)

    # ① DB Schema 自动迁移（幂等，每次启动执行）
    try:
        from backend.db.migrations import run_migrations
        await run_migrations()
    except Exception as e:
        logger.warning("app.migrations_failed | error=%s", e)   # 迁移失败只告警，不拦启动

    # ①b checkpointer：禁用 AsyncPostgresSaver，改用进程内 MemorySaver。
    # 原因：AsyncPostgresSaver 在 interrupt/resume 重复使用下会卡住（云上实测第二次生成卡死在
    # reflection_optimize 的 interrupt），而本地 e2e 走 MemorySaver 全绿。演示场景服务不重启即可。
    # 若需跨重启持久化，需排查 langgraph-checkpoint-postgres 的 interrupt 状态复位问题。
    try:
        from backend.core.memory import init_pg_checkpointer  # noqa: F401 保留 import 备用
        # await init_pg_checkpointer()
        logger.info("app.checkpointer_using_memory_saver")
    except Exception as e:
        logger.warning("app.pg_checkpointer_init_failed | error=%s", e)   # 失败兜底 MemorySaver

    # ② 预热三个本地模型（首次加载慢，提前热好；best-effort，失败不拦启动）
    # 注意：必须同步顺序加载，不能用 run_in_executor 丢后台线程——
    # transformers pipeline 的 low_cpu_mem_usage 用 meta device 加载权重再 dispatch，
    # 在后台线程里 dispatch 会失败，模型停在 meta 设备，前向传播报
    # "Tensor.item() cannot be called on meta tensors"（实测 QueryClassifier 踩过）。
    try:
        from backend.core.reranker import BGEReranker            # 重排序模型
        from backend.core.query_classifier import QueryClassifier # 意图分类器
        from backend.core.knowledge_base import BGEMEmbedder      # BGE-M3 嵌入

        BGEReranker.get_instance()
        QueryClassifier.get_instance()
        BGEMEmbedder.get_instance()
        logger.info("app.local_models_warmed_up")
    except Exception as e:
        logger.warning("app.local_models_warmup_failed | error=%s", e)

    # ③ 驱动 MCP Server 的 lifespan（mount 不会自动调用子应用的 lifespan，需手动嵌套进入）
    async with kb_mcp._session_manager.run():
        async with ws_mcp._session_manager.run():
            logger.info("app.started")
            yield                                               # ← 应用运行期间停在这里

            # ── 关闭时执行 ──
            logger.info("app.shutting_down")
            from backend.core.llm_factory import LLMFactory
            LLMFactory.clear_cache()                            # 清缓存
            logger.info("app.shutdown_complete")


app = FastAPI(lifespan=lifespan)


# 前端静态资源禁用缓存：开发/演示阶段改 JS/CSS 后，浏览器刷新即生效（避免旧模块缓存）
@app.middleware("http")
async def _no_cache_static(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith(("/js/", "/css/")):
        response.headers["Cache-Control"] = "no-cache"
    return response

# 统一挂载总路由（内部已按前缀聚合各子模块）
app.include_router(api_router, prefix="/api/v1")

# MCP 端点挂载（与 config.py 里 kb_mcp_server_url / web_search_mcp_url 对齐）
app.mount("/mcp/kb", kb_mcp.streamable_http_app())
app.mount("/mcp/web-search", ws_mcp.streamable_http_app())


@app.get("/health")
async def health():
    return {"status": "ok"}


# ── 前端静态文件（纯静态 SPA，同源部署，无需 CORS）────────────
# 必须挂载在所有 API 路由之后：API 路由（/api/v1、/health、/mcp）先精确匹配，
# Mount("/") 只兜底 serve index.html 与 /css、/js 静态资源。
_frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
if _frontend_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
