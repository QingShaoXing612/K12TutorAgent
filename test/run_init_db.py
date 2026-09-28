import asyncio
import asyncpg
from backend.config import get_settings

async def main():
    s = get_settings()
    sql = open("../scripts/init_db.sql", encoding="utf-8").read()
    conn = await asyncpg.connect(
        host=s.db_host, port=s.db_port, user=s.db_user,
        password=s.db_password, database=s.db_name,  # 即 eduagent
    )
    try:
        await conn.execute(sql)  # 无参数时 asyncpg 支持一次执行多语句
        print("已执行 init_db.sql 到库:", s.db_name)
    finally:
        await conn.close()

asyncio.run(main())
