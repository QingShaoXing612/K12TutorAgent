# scripts/seed_learning_data_plus.py
# 扩充学情数据（幂等）：学生 30 个、日常练习多学生分层、考试批改数据（exam_submissions + exam_reviews）。
# 解决两个演示缺口：
#   ① 日常练习源学生太少 → 扩到 30 个（标准教学班），形成掌握度分层（优8/良好8/中等7/薄弱7）
#   ② 考试批改源「暂无六年级4班数学考试数据」→ 灌 30 学生的演示卷提交 + 批改结果
# 运行：PYTHONUTF8=1 .venv/Scripts/python.exe scripts/seed_learning_data_plus.py
import asyncio
import os
import uuid

from dotenv import load_dotenv
import asyncpg
from passlib.context import CryptContext

import bcrypt as _b, types as _t
if not hasattr(_b, "__about__"):
    _b.__about__ = _t.SimpleNamespace(__version__=getattr(_b, "__version__", "4.x"))

load_dotenv(".env.local")

DB_DSN = (
    f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
    f"@{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', 5433)}"
    f"/{os.getenv('DB_NAME', 'eduagent')}"
)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SUBJECT = "数学"
GRADE = "六年级"
CLASS_ID = "c1a55e00-0000-4000-8000-000000000001"
TENANT_ID = "tenant_default"
EXAM_ID = "b3a2c1d0-0000-4000-8000-000000000001"  # 演示卷（seed_exam_math.py）

# 30 个学生账号（student01~student30）
STUDENTS = [f"student{i:02d}" for i in range(1, 31)]


def _gen_matrix(tiers):
    """按分层模板生成对错矩阵（题数 × 学生数，1 对 0 错）。
    tiers = [(人数, [模板1, 模板2, ...])]，同一层内模板轮转，避免完全一致。"""
    cols = []
    for count, templates in tiers:
        for i in range(count):
            cols.append(templates[i % len(templates)])
    nq = len(cols[0])
    return [[cols[s][q] for s in range(len(cols))] for q in range(nq)]


# ── 日常练习：8 题（按难度易→难排序）× 30 学生 ──────────────
# (kp, 题型, 满分, 正确答案, 常见错误答案)
PRACTICE = [
    ("负数",           "single_choice", 5,  "A",    "B"),
    ("圆的认识",       "single_choice", 5,  "D",    "B"),
    ("比",             "fill_blank",    5,  "2:3",  "3:2"),
    ("分数乘法",       "fill_blank",    5,  "3/10", "6/20"),
    ("百分数",         "short_answer",  10, "170",  "30"),
    ("圆的周长与面积", "short_answer",  10, "31.4", "78.5"),
    ("分数除法",       "short_answer",  10, "18",   "8"),
    ("比例",           "short_answer",  10, "9",    "8"),
]
# 分层模板（8 题）：优全对/错1、良好错后2、中等对前4、薄弱对前1~2
PRACTICE_TIERS = [
    (8, [[1, 1, 1, 1, 1, 1, 1, 1], [1, 1, 1, 1, 1, 1, 1, 0]]),  # 优
    (8, [[1, 1, 1, 1, 1, 1, 0, 0]]),                            # 良好
    (7, [[1, 1, 1, 1, 0, 0, 0, 0]]),                            # 中等
    (7, [[1, 1, 0, 0, 0, 0, 0, 0], [1, 0, 0, 0, 0, 0, 0, 0]]),  # 薄弱
]

# ── 考试批改：演示卷 6 题（question_id 固定，见 seed_exam_math.py）──
EXAM_QUESTIONS = [
    ("c3a2c1d0-0000-4000-8000-000000000001", "single_choice", "圆的周长",     5),
    ("c3a2c1d0-0000-4000-8000-000000000002", "single_choice", "分数除法",     5),
    ("c3a2c1d0-0000-4000-8000-000000000003", "judge",         "圆的面积",     5),
    ("c3a2c1d0-0000-4000-8000-000000000004", "judge",         "分数乘法",     5),
    ("c3a2c1d0-0000-4000-8000-000000000005", "short_answer",  "圆的周长与面积", 15),
    ("c3a2c1d0-0000-4000-8000-000000000006", "short_answer",  "分数应用题",    15),
]
# 分层模板（6 题）：优全对、良好错1、中等错3、薄弱只对1~2
EXAM_TIERS = [
    (8, [[1, 1, 1, 1, 1, 1]]),                                    # 优
    (8, [[1, 0, 1, 1, 1, 1]]),                                    # 良好
    (7, [[1, 0, 1, 1, 0, 0]]),                                    # 中等
    (7, [[1, 0, 1, 0, 0, 0], [1, 1, 0, 0, 0, 0]]),                # 薄弱
]
# 每道考试题的正确答案（客观题用于生成 student_answer / ai_feedback）
EXAM_ANSWERS = {
    "c3a2c1d0-0000-4000-8000-000000000001": "B",
    "c3a2c1d0-0000-4000-8000-000000000002": "B",
    "c3a2c1d0-0000-4000-8000-000000000003": "正确",
    "c3a2c1d0-0000-4000-8000-000000000004": "错误",
    "c3a2c1d0-0000-4000-8000-000000000005": "周长=31.4厘米，面积=78.5平方厘米",
    "c3a2c1d0-0000-4000-8000-000000000006": "第二段长2/5米，一共1米",
}
# 错误作答（用于错题）
EXAM_WRONG = {
    "c3a2c1d0-0000-4000-8000-000000000001": "C",
    "c3a2c1d0-0000-4000-8000-000000000002": "C",
    "c3a2c1d0-0000-4000-8000-000000000003": "错误",
    "c3a2c1d0-0000-4000-8000-000000000004": "正确",
    "c3a2c1d0-0000-4000-8000-000000000005": "只算了周长，漏了面积",
    "c3a2c1d0-0000-4000-8000-000000000006": "一共4/5米",
}


async def main():
    conn = await asyncpg.connect(DB_DSN)
    try:
        # ── ① 建学生账号（幂等）───────────────────────────────
        for uname in STUDENTS:
            await conn.execute(
                "INSERT INTO users (id, tenant_id, username, email, password_hash, role, is_active) "
                "VALUES ($1,$2,$3,$4,$5,'student',TRUE) "
                "ON CONFLICT (tenant_id, email) DO NOTHING",
                str(uuid.uuid4()), TENANT_ID, uname, f"{uname}@eduagent.local",
                pwd_context.hash("Student@123456"),
            )

        rows = await conn.fetch(
            "SELECT id, username FROM users WHERE username = ANY($1::text[]) ORDER BY username",
            STUDENTS,
        )
        student_ids = [r["id"] for r in rows]
        print(f"① 学生账号就绪：{len(student_ids)} 个")

        # ── ② 全部入班（先清旧班级）───────────────────────────
        await conn.execute("UPDATE users SET class_id = NULL WHERE class_id = $1", CLASS_ID)
        await conn.execute(
            "UPDATE users SET class_id = $1 WHERE id = ANY($2::uuid[])",
            CLASS_ID, student_ids,
        )
        print(f"② {len(student_ids)} 个学生已入班 {CLASS_ID[:8]}…")

        # ── ③ 幂等清旧数据 ────────────────────────────────────
        await conn.execute(
            "DELETE FROM student_practice_records WHERE student_id = ANY($1::uuid[])",
            student_ids,
        )
        await conn.execute(
            "DELETE FROM exam_submissions WHERE student_id = ANY($1::uuid[])",
            student_ids,
        )  # exam_reviews 由 ON DELETE CASCADE 连带删除
        print("③ 旧 practice / exam 数据已清理")

        # ── ④ 灌日常练习 ──────────────────────────────────────
        practice_matrix = _gen_matrix(PRACTICE_TIERS)
        practice_count = 0
        for qi, (kp, qtype, score, correct, wrong) in enumerate(PRACTICE):
            ex = await conn.fetchrow(
                "SELECT exercise_id, score FROM exercise_bank "
                "WHERE knowledge_tag = $1 AND question_type = $2 AND subject = $3 LIMIT 1",
                kp, qtype, SUBJECT,
            )
            if not ex:
                print(f"  跳过 {kp}/{qtype}：exercise_bank 无此题")
                continue
            for si, sid in enumerate(student_ids):
                ok = practice_matrix[qi][si]
                await conn.execute(
                    "INSERT INTO student_practice_records "
                    "(id, tenant_id, student_id, exercise_id, kp_id, knowledge_tag,"
                    " subject, grade, source, answer, is_correct, score, recorded_at) "
                    "VALUES ($1,$2,$3,$4,NULL,$5,$6,$7,'homework',$8,$9,$10,NOW())",
                    str(uuid.uuid4()), TENANT_ID, sid, ex["exercise_id"],
                    kp, SUBJECT, GRADE, correct if ok else wrong, bool(ok), score if ok else 0,
                )
                practice_count += 1
        print(f"④ 灌入 {practice_count} 条 practice 记录")

        # ── ⑤ 灌考试批改数据 ──────────────────────────────────
        exam_matrix = _gen_matrix(EXAM_TIERS)
        exam_count = 0
        for si, sid in enumerate(student_ids):
            submission_id = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO exam_submissions "
                "(id, tenant_id, exam_id, student_id, source, status, submitted_at, published_at) "
                "VALUES ($1,$2,$3,$4,'word','published',NOW(),NOW())",
                submission_id, TENANT_ID, EXAM_ID, sid,
            )
            for qi, (qid, qtype, ktag, qscore) in enumerate(EXAM_QUESTIONS):
                ok = exam_matrix[qi][si]
                await conn.execute(
                    "INSERT INTO exam_reviews "
                    "(id, submission_id, question_id, question_type, knowledge_tag,"
                    " student_answer, ai_score, ai_feedback, ai_raw_result, final_score, needs_review) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'{}'::jsonb,$9,FALSE)",
                    str(uuid.uuid4()), submission_id, qid, qtype, ktag,
                    EXAM_ANSWERS[qid] if ok else EXAM_WRONG[qid],
                    qscore if ok else 0,
                    "正确" if ok else f"正确答案：{EXAM_ANSWERS[qid]}",
                    qscore if ok else 0,
                )
                exam_count += 1
        print(f"⑤ 灌入 {exam_count} 条 exam_reviews（{len(student_ids)} 学生 × 6 题）")

        # ── ⑥ 校验 ────────────────────────────────────────────
        pr = await conn.fetchval(
            "SELECT count(*) FROM student_practice_records r JOIN users u ON r.student_id=u.id "
            "WHERE u.class_id=$1 AND r.subject=$2", CLASS_ID, SUBJECT)
        er = await conn.fetchval(
            "SELECT count(*) FROM exam_reviews r JOIN exam_submissions s ON r.submission_id=s.id "
            "JOIN exams e ON s.exam_id=e.id "
            "WHERE s.student_id IN (SELECT id FROM users WHERE class_id=$1 AND role='student') "
            "AND r.final_score IS NOT NULL AND s.status='published' AND e.subject=$2",
            CLASS_ID, SUBJECT)
        # 分层分布：统计各层学生数（按 practice 对题数）
        dist = await conn.fetch(
            "SELECT u.username, count(*) FILTER (WHERE r.is_correct) AS correct "
            "FROM student_practice_records r JOIN users u ON r.student_id=u.id "
            "WHERE u.class_id=$1 GROUP BY u.username ORDER BY u.username",
            CLASS_ID)
        print(f"✅ 校验：班级 practice {pr} 条 / 考试批改 {er} 条")
        print(f"   分层示例（前 8 人对题数）：",
              "，".join(f"{d['username']}={d['correct']}" for d in dist[:8]))

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
