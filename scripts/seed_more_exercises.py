# scripts/seed_more_exercises.py
# 增量灌入六年级数学习题（幂等）：只插入 exercise_bank 中尚不存在的题，不动现有数据。
# 用途：扩充 seed_lesson_data.py 的 EXERCISES 后，无需重跑会清库的 seed_lesson_data.py，
#       用本脚本把新增题目安全追加进当前库（现有作业/学情/高中习题不受影响）。
# 运行：PYTHONPATH=. PYTHONUTF8=1 .venv/Scripts/python.exe scripts/seed_more_exercises.py
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncpg
from backend.config import get_settings


def _load_lesson_data():
    """按路径加载 seed_lesson_data.py，复用其 EXERCISES / SUBJECT / GRADE（单一数据源）。"""
    path = Path(__file__).parent / "seed_lesson_data.py"
    spec = importlib.util.spec_from_file_location("seed_lesson_data", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def main():
    mod = _load_lesson_data()
    EXERCISES = mod.EXERCISES
    SUBJECT = mod.SUBJECT
    GRADE = mod.GRADE

    s = get_settings()
    conn = await asyncpg.connect(
        host=s.db_host, port=s.db_port, user=s.db_user,
        password=s.db_password, database=s.db_name, timeout=20,
    )
    try:
        existing = {r["content"] for r in await conn.fetch(
            "SELECT content FROM exercise_bank WHERE subject = $1 AND grade = $2",
            SUBJECT, GRADE,
        )}
        kp_map = {r["kp_name"]: r["kp_id"] for r in await conn.fetch(
            "SELECT kp_name, kp_id FROM knowledge_points WHERE subject = $1 AND grade = $2",
            SUBJECT, GRADE,
        )}

        inserted = skipped = 0
        for e in EXERCISES:
            if e["content"] in existing:
                skipped += 1
                continue
            kp_id = kp_map.get(e["kp"])
            if kp_id is None:
                print(f"⚠️ 知识点不存在，跳过：{e['kp']}（{e['content'][:20]}…）")
                continue
            await conn.execute(
                "INSERT INTO exercise_bank (subject, grade, topic, kp_id, question_type, content,"
                " options, answer, analysis, difficulty, score, knowledge_tag, quality_status, created_by)"
                " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, 'checked', NULL)",
                SUBJECT, GRADE, e["kp"], kp_id, e["type"], e["content"],
                json.dumps(e["options"], ensure_ascii=False), e["answer"], e["analysis"],
                e["difficulty"], e["score"], e["kp"],
            )
            inserted += 1

        total = await conn.fetchval(
            "SELECT count(*) FROM exercise_bank WHERE subject = $1 AND grade = $2",
            SUBJECT, GRADE,
        )
        print(f"✅ 增量完成：新增 {inserted} 道，跳过已存在 {skipped} 道；当前 {SUBJECT} {GRADE} 习题共 {total} 道")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
