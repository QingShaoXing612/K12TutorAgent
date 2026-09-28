# scripts/seed_classes.py
# 灌入班级实体数据（幂等），供布置作业/学情报告下拉使用（名称 + 年级 + 按年级过滤）。
# 运行：PYTHONUTF8=1 .venv/Scripts/python.exe scripts/seed_classes.py
import asyncio
import os

from dotenv import load_dotenv
import asyncpg

load_dotenv(".env.local")

DB_DSN = (
    f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
    f"@{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', 5433)}"
    f"/{os.getenv('DB_NAME', 'eduagent')}"
)

# (class_id, 名称, 年级)。六年级4班 与 users.class_id / assignments.class_id 对齐
CLASSES = [
    ("c1a55e00-0000-4000-8000-000000000001", "六年级4班", "六年级"),
    ("c1a55e00-0000-4000-8000-000000000002", "六年级1班", "六年级"),
    ("c1a55e00-0000-4000-8000-000000000003", "五年级1班", "五年级"),
]


async def main():
    conn = await asyncpg.connect(DB_DSN)
    try:
        for cid, name, grade in CLASSES:
            await conn.execute(
                "INSERT INTO classes (id, tenant_id, name, grade) "
                "VALUES ($1, 'tenant_default', $2, $3) "
                "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, grade = EXCLUDED.grade",
                cid, name, grade,
            )
        rows = await conn.fetch("SELECT name, grade FROM classes ORDER BY grade, name")
        print("✅ 班级数据就绪：")
        for r in rows:
            print(f"   {r['grade']} · {r['name']}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
