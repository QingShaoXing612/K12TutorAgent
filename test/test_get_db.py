# test_get_db.py（项目根目录）
import asyncio
from sqlalchemy import text
from backend.dependencies import get_db

async def main():
    # 模拟 FastAPI 调用依赖：迭代一次拿到 session，跑一条查询
    async for db in get_db():
        r = await db.execute(text(
            "SELECT current_database(), "
            "count(*) FROM information_schema.tables WHERE table_schema = :s"
        ), {"s": "public"})
        row = r.fetchone()
        print("get_db 连到库:", row[0])
        print("public 下表数量:", row[1])

asyncio.run(main())
print("get_db 测试通过")
