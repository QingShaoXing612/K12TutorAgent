import asyncio
import os
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from pymilvus import connections, utility

# 连接信息从环境变量读取，避免把真实密码/主机写进代码（勿提交真实值）
DB_URL = os.environ.get("DB_URL", "postgresql+asyncpg://USER:PASSWORD@HOST:5433/eduagent")
MILVUS_HOST = os.environ.get("MILVUS_HOST", "localhost")

async def check_postgres():
    engine = create_async_engine(DB_URL)
    async with engine.connect() as conn:
        r = await conn.execute(text("SELECT 1"))
        print("✅ PostgreSQL 连通：", r.scalar())
    await engine.dispose()

def check_milvus():
    connections.connect(alias="default", host=MILVUS_HOST, port=19531)
    print("✅ Milvus 连通，已有集合：", utility.list_collections())

async def main():
    await check_postgres()
    check_milvus()

asyncio.run(main())
