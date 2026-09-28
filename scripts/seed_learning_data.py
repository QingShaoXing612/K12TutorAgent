# scripts/seed_learning_data.py
# 执行：PYTHONUTF8=1 .venv/Scripts/python.exe scripts/seed_learning_data.py
# 用途：灌入学情分析 Agent 的最小闭环数据（模拟真实学情）：
#   ① 给 2 个学生加 class_id（形成班级）
#   ② 灌 student_practice_records（日常练习作答，覆盖多 kp/题型/对错）
#   ③ 灌 intervention_strategies（干预策略库）
# 幂等：先清该班级的 practice + 学生 class_id + 学情干预策略，再重灌。

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

SUBJECT = "数学"
GRADE = "六年级"
CLASS_ID = "c1a55e00-0000-4000-8000-000000000001"
TENANT_ID = "tenant_default"

# 干预策略（覆盖 strat_level × error_type，含 1 条具体 kp 策略）
INTERVENTIONS = [
    ("e0000000-0000-4000-8000-000000000001", "weak", "calculation", None,
     "计算专项：每日口算训练，强化分数四则运算法则，重点是通分、约分、颠倒相乘。"),
    ("e0000000-0000-4000-8000-000000000002", "weak", "concept", None,
     "概念梳理：用思维导图对比易混概念（比 vs 比例、分数乘法 vs 分数除法），建立知识框架。"),
    ("e0000000-0000-4000-8000-000000000003", "weak", "method", None,
     "方法归纳：整理各类题型的解题步骤，做变式训练，提升方法迁移能力。"),
    ("e0000000-0000-4000-8000-000000000004", "weak", "calculation", "分数除法",
     "分数除法计算易错专项：强化『除以一个数等于乘它的倒数』，配套颠倒相乘 + 约分步骤训练。"),
    ("e0000000-0000-4000-8000-000000000005", "good", "method", None,
     "达标生方法提升：综合题变式训练，提升解题方法的迁移与灵活运用。"),
    ("e0000000-0000-4000-8000-000000000006", "all", "all", None,
     "分层作业：薄弱生做基础题、达标生做综合题、优生做拓展题，分层布置分别批改。"),
]

# 学生作答：每道题 (kp, 题型, 学生序号, 得分)。学生 0=student01（较好）, 1=student02（薄弱）
# 分数除法这条策略挂具体 kp，seed 时动态查 kp_id
PRACTICE = [
    # (kp, 题型, [(学生序号, 得分, 作答)])。错答刻意体现不同错因，供 error_type LLM 分析：
    ("分数乘法",       "fill_blank",    [(0, 5, "3/10"), (1, 5, "3/10")]),        # 都对
    ("分数除法",       "short_answer",  [(0, 10, "18"), (1, 0, "8")]),            # s2 错：12×2/3=8 方法错
    ("比",             "fill_blank",    [(0, 5, "2:3"), (1, 5, "2:3")]),          # 都对
    ("百分数",         "short_answer",  [(0, 10, "170"), (1, 0, "30")]),          # s2 错：八五折当15% 审题错
    ("负数",           "single_choice", [(0, 5, "A"), (1, 5, "A")]),              # 都对
    ("比例",           "short_answer",  [(0, 0, "8"), (1, 0, "8")]),              # 都错：36÷4 算错 计算错
    ("圆的认识",       "single_choice", [(0, 5, "D"), (1, 0, "B")]),              # s2 错：以为2条 概念错
    ("圆的周长与面积", "short_answer",  [(0, 10, "31.4"), (1, 0, "78.5")]),       # s2 错：用面积公式 方法错
]


async def main():
    conn = await asyncpg.connect(DB_DSN)
    print(f"连接 PG 成功，准备灌学情数据（{SUBJECT} {GRADE}）")

    # ── 幂等清理 ──────────────────────────────────────────────
    rows = await conn.fetch(
        "SELECT id FROM users WHERE role='student' ORDER BY username")
    student_ids = [r["id"] for r in rows]
    await conn.execute(
        "DELETE FROM student_practice_records WHERE student_id = ANY($1)",
        student_ids,
    )
    await conn.execute(
        "UPDATE users SET class_id = NULL WHERE class_id = $1", CLASS_ID)
    await conn.execute(
        "DELETE FROM intervention_strategies WHERE id::text LIKE 'e0000000-%'")
    await conn.execute(
        "DELETE FROM learning_reports WHERE class_id = $1", CLASS_ID)
    print(f"清理完成（practice/class_id/策略/报告）")

    # ── ① 学生加 class_id ─────────────────────────────────────
    if len(student_ids) < 2:
        print("警告：学生不足 2 个，请先 seed_data.py 建用户")
        return
    await conn.execute(
        "UPDATE users SET class_id = $1 WHERE id = ANY($2)",
        CLASS_ID, student_ids[:2],
    )
    s1, s2 = student_ids[0], student_ids[1]
    print(f"班级 {CLASS_ID[:8]}…：{len(student_ids[:2])} 个学生已入班")

    # ── ② 灌 practice 记录 ────────────────────────────────────
    count = 0
    for i, (kp, qtype, answers) in enumerate(PRACTICE):
        ex = await conn.fetchrow(
            "SELECT exercise_id, score FROM exercise_bank "
            "WHERE knowledge_tag = $1 AND question_type = $2 AND subject = $3 LIMIT 1",
            kp, qtype, SUBJECT,
        )
        if not ex:
            print(f"跳过 {kp}/{qtype}：exercise_bank 无此题")
            continue
        for sidx, score, answer in answers:
            sid = [s1, s2][sidx]
            await conn.execute(
                "INSERT INTO student_practice_records "
                "(id, tenant_id, student_id, exercise_id, kp_id, knowledge_tag,"
                " subject, grade, source, answer, is_correct, score, recorded_at) "
                "VALUES ($1,$2,$3,$4,NULL,$5,$6,$7,'homework',$8,$9,$10,NOW())",
                f"e0000001-0000-4000-8000-{count:012d}",
                TENANT_ID, sid, ex["exercise_id"], kp,
                SUBJECT, GRADE, answer, score == ex["score"], score,
            )
            count += 1
    print(f"灌入 {count} 条 practice 记录")

    # ── ③ 灌干预策略 ──────────────────────────────────────────
    fd_kp = await conn.fetchval(
        "SELECT kp_id FROM knowledge_points WHERE kp_name = '分数除法' AND subject = $1",
        SUBJECT,
    )
    for sid, strat_level, error_type, kp_name, content in INTERVENTIONS:
        kp_id = fd_kp if kp_name == "分数除法" else None
        await conn.execute(
            "INSERT INTO intervention_strategies "
            "(id, tenant_id, subject, grade, strat_level, error_type, kp_id, strategy_content) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
            sid, TENANT_ID, SUBJECT, GRADE, strat_level, error_type, kp_id, content,
        )
    print(f"灌入 {len(INTERVENTIONS)} 条干预策略")

    await conn.close()
    _load_interventions_to_milvus()   # 干预策略灌 Milvus（语义检索双路）
    print("学情数据 seed 完成 ✅")


def _load_interventions_to_milvus():
    """把干预策略灌进 Milvus（knowledge_domain 集合，resource_type='intervention'），
    供 intervention_retrieval 的语义检索双路使用。幂等：先删旧 intervention chunk。"""
    import sys
    sys.path.insert(0, ".")
    from backend.core.knowledge_base import BGEMEmbedder, KnowledgeBaseClient, DocumentChunk

    embedder = BGEMEmbedder.get_instance()
    kb = KnowledgeBaseClient()

    try:
        kb._client.delete(collection_name="knowledge_domain", filter='resource_type == "intervention"')
    except Exception as e:
        print(f"删除旧 intervention chunk 失败（可能尚无）：{e}")

    chunks = []
    for sid, strat_level, error_type, _kp_name, content in INTERVENTIONS:
        dense, sparse = embedder.encode_query(content)
        chunks.append(DocumentChunk(
            id=f"interv_{sid}",
            content=content,
            embedding=dense,
            sparse_embedding=sparse,
            course_id="",
            document_id=sid,
            source_name=f"干预策略·{strat_level}层·{error_type}",
            chunk_type="text",
            chunk_index=0,
            version="1",
            tenant_id=TENANT_ID,
            resource_type="intervention",
            subject=SUBJECT,
            grade=GRADE,
            source="intervention_strategies",
            content_id=sid,
        ))
    kb.upsert_chunks(chunks)
    print(f"灌入 {len(chunks)} 条干预策略到 Milvus")


if __name__ == "__main__":
    asyncio.run(main())
