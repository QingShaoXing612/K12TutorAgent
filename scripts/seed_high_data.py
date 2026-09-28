# scripts/seed_high_data.py
# 用途：灌高中（高一/高二/高三数学 + 高一物理/化学）结构化数据，补齐 K12 高中段：
#   ① knowledge_points 知识点树（每学段一套）
#   ② lesson_templates 教案模板（3 课型通用，按 subject+grade 灌）
#   ③ exercise_bank 习题（各学段核心知识点）
#   ④ student_practice_records 日常练习作答（高一数学，供学情分析）
#   ⑤ intervention_strategies 干预策略（高中通用）
# 幂等：只清高中数据（grade IN 高中年级），不清小学六年级。
# 运行：PYTHONPATH=. PYTHONUTF8=1 .venv/Scripts/python.exe scripts/seed_high_data.py

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncpg
from backend.config import get_settings

TENANT_ID = "tenant_default"

# ── 教案模板（3 课型，通用结构，按 subject+grade 复用）─────────
def _template_structure(lesson_type_hint: str, process_steps: list) -> dict:
    return {
        "lesson_type_hint": lesson_type_hint,
        "sections": [
            {"key": "objectives",       "title": "教学目标", "type": "list",  "required": True},
            {"key": "key_points",       "title": "教学重点", "type": "text",  "required": True},
            {"key": "difficult_points", "title": "教学难点", "type": "text",  "required": True},
            {"key": "process",          "title": "教学过程", "type": "steps", "steps": process_steps, "required": True},
            {"key": "board",            "title": "板书设计", "type": "text",  "required": False},
            {"key": "exercises",        "title": "课堂练习", "type": "list",  "required": False},
            {"key": "reflection",       "title": "教学反思", "type": "text",  "required": False},
        ],
    }

LESSON_TEMPLATES = [
    {"lesson_type": "new",      "structure": _template_structure("新授课", ["导入", "新授", "巩固练习", "课堂小结"])},
    {"lesson_type": "exercise", "structure": _template_structure("习题课", ["知识回顾", "例题精讲", "变式训练", "当堂检测"])},
    {"lesson_type": "review",   "structure": _template_structure("复习课", ["知识梳理", "综合应用", "易错点辨析", "拓展提升"])},
]

# ── 学段数据：每学段 (subject, grade, 知识点, 习题) ─────────────
# 知识点：(name, parent, difficulty, requirement, code)
# 习题：(kp, type, difficulty, score, content, options, answer, analysis)
STAGES = [
    {
        "subject": "数学", "grade": "高一",
        "kps": [
            {"name": "高一数学", "parent": None, "difficulty": None, "requirement": None, "code": "G1"},
            {"name": "函数主线", "parent": "高一数学", "difficulty": None, "requirement": None, "code": "G1-A"},
            {"name": "几何与代数主线", "parent": "高一数学", "difficulty": None, "requirement": None, "code": "G1-B"},
            {"name": "函数的概念与性质", "parent": "函数主线", "difficulty": "medium", "requirement": "掌握", "code": "G1-A-1"},
            {"name": "指数函数与对数函数", "parent": "函数主线", "difficulty": "medium", "requirement": "掌握", "code": "G1-A-2"},
            {"name": "三角函数", "parent": "函数主线", "difficulty": "hard", "requirement": "理解", "code": "G1-A-3"},
            {"name": "平面向量", "parent": "几何与代数主线", "difficulty": "medium", "requirement": "理解", "code": "G1-B-1"},
            {"name": "立体几何初步", "parent": "几何与代数主线", "difficulty": "hard", "requirement": "掌握", "code": "G1-B-2"},
            {"name": "复数", "parent": "几何与代数主线", "difficulty": "easy", "requirement": "了解", "code": "G1-B-3"},
        ],
        "exercises": [
            {"kp": "函数的概念与性质", "type": "fill_blank", "difficulty": "easy", "score": 5,
             "content": "函数 f(x)=√(x-1) 的定义域为 ____",
             "options": [], "answer": "[1,+∞)",
             "analysis": "被开方数非负，即 x-1≥0，得 x≥1，定义域为 [1,+∞)。"},
            {"kp": "函数的概念与性质", "type": "single_choice", "difficulty": "medium", "score": 5,
             "content": "下列函数中，是偶函数的是（）",
             "options": [{"key": "A", "text": "f(x)=x"}, {"key": "B", "text": "f(x)=x²"}, {"key": "C", "text": "f(x)=x³"}, {"key": "D", "text": "f(x)=1/x"}],
             "answer": "B",
             "analysis": "偶函数满足 f(-x)=f(x)，f(x)=x² 有 (-x)²=x²，为偶函数；其余为奇函数。"},
            {"kp": "指数函数与对数函数", "type": "fill_blank", "difficulty": "medium", "score": 5,
             "content": "计算：log₂8 = ____",
             "options": [], "answer": "3",
             "analysis": "8=2³，所以 log₂8=log₂2³=3。"},
            {"kp": "三角函数", "type": "fill_blank", "difficulty": "easy", "score": 5,
             "content": "sin30° = ____",
             "options": [], "answer": "1/2",
             "analysis": "30° 的正弦值为 1/2，是特殊角的三角函数值。"},
        ],
    },
    {
        "subject": "数学", "grade": "高二",
        "kps": [
            {"name": "高二数学", "parent": None, "difficulty": None, "requirement": None, "code": "G2"},
            {"name": "函数主线", "parent": "高二数学", "difficulty": None, "requirement": None, "code": "G2-A"},
            {"name": "几何与代数主线", "parent": "高二数学", "difficulty": None, "requirement": None, "code": "G2-B"},
            {"name": "数列", "parent": "函数主线", "difficulty": "medium", "requirement": "掌握", "code": "G2-A-1"},
            {"name": "导数及其应用", "parent": "函数主线", "difficulty": "hard", "requirement": "掌握", "code": "G2-A-2"},
            {"name": "空间向量与立体几何", "parent": "几何与代数主线", "difficulty": "hard", "requirement": "掌握", "code": "G2-B-1"},
            {"name": "直线与圆的方程", "parent": "几何与代数主线", "difficulty": "medium", "requirement": "掌握", "code": "G2-B-2"},
            {"name": "圆锥曲线", "parent": "几何与代数主线", "difficulty": "hard", "requirement": "理解", "code": "G2-B-3"},
        ],
        "exercises": [
            {"kp": "圆锥曲线", "type": "short_answer", "difficulty": "hard", "score": 10,
             "content": "椭圆 x²/9 + y²/4 = 1 的离心率为 ____",
             "options": [], "answer": "√5/3",
             "analysis": "a²=9 得 a=3，b²=4 得 b=2，c²=a²-b²=5，c=√5，离心率 e=c/a=√5/3。"},
            {"kp": "导数及其应用", "type": "fill_blank", "difficulty": "medium", "score": 5,
             "content": "函数 f(x)=x² 的导数 f'(x)=____",
             "options": [], "answer": "2x",
             "analysis": "幂函数求导公式 (xⁿ)'=nxⁿ⁻¹，所以 (x²)'=2x。"},
            {"kp": "数列", "type": "short_answer", "difficulty": "medium", "score": 10,
             "content": "等差数列中 a₁=1，公差 d=2，则 a₅=____",
             "options": [], "answer": "9",
             "analysis": "等差数列通项 aₙ=a₁+(n-1)d，a₅=1+4×2=9。"},
            {"kp": "直线与圆的方程", "type": "single_choice", "difficulty": "easy", "score": 5,
             "content": "圆 x²+y²=4 的半径为（）",
             "options": [{"key": "A", "text": "2"}, {"key": "B", "text": "4"}, {"key": "C", "text": "16"}, {"key": "D", "text": "√2"}],
             "answer": "A",
             "analysis": "圆的标准方程 x²+y²=r²，这里 r²=4，所以半径 r=2。"},
        ],
    },
    {
        "subject": "数学", "grade": "高三",
        "kps": [
            {"name": "高三数学", "parent": None, "difficulty": None, "requirement": None, "code": "G3"},
            {"name": "概率与统计主线", "parent": "高三数学", "difficulty": None, "requirement": None, "code": "G3-A"},
            {"name": "计数原理", "parent": "概率与统计主线", "difficulty": "medium", "requirement": "理解", "code": "G3-A-1"},
            {"name": "随机变量及其分布", "parent": "概率与统计主线", "difficulty": "hard", "requirement": "掌握", "code": "G3-A-2"},
            {"name": "统计与成对数据", "parent": "概率与统计主线", "difficulty": "medium", "requirement": "了解", "code": "G3-A-3"},
        ],
        "exercises": [
            {"kp": "随机变量及其分布", "type": "short_answer", "difficulty": "hard", "score": 10,
             "content": "若 X~B(3, 1/2)，则 P(X=2)=____",
             "options": [], "answer": "3/8",
             "analysis": "二项分布 P(X=k)=C(n,k)pᵏ(1-p)ⁿ⁻ᵏ，P(X=2)=C(3,2)(1/2)²(1/2)¹=3/8。"},
            {"kp": "计数原理", "type": "fill_blank", "difficulty": "easy", "score": 5,
             "content": "从 5 人中选 2 人，共有 ____ 种选法",
             "options": [], "answer": "10",
             "analysis": "组合数 C(5,2)=5×4/2=10。"},
            {"kp": "统计与成对数据", "type": "single_choice", "difficulty": "medium", "score": 5,
             "content": "描述两个变量线性相关程度强弱的统计量是（）",
             "options": [{"key": "A", "text": "平均数"}, {"key": "B", "text": "方差"}, {"key": "C", "text": "相关系数"}, {"key": "D", "text": "众数"}],
             "answer": "C",
             "analysis": "相关系数 r 衡量两个变量的线性相关程度，|r| 越接近 1 相关越强。"},
        ],
    },
    {
        "subject": "物理", "grade": "高一",
        "kps": [
            {"name": "高一物理", "parent": None, "difficulty": None, "requirement": None, "code": "P1"},
            {"name": "运动与相互作用", "parent": "高一物理", "difficulty": None, "requirement": None, "code": "P1-A"},
            {"name": "匀变速直线运动", "parent": "运动与相互作用", "difficulty": "medium", "requirement": "掌握", "code": "P1-A-1"},
            {"name": "牛顿运动定律", "parent": "运动与相互作用", "difficulty": "hard", "requirement": "掌握", "code": "P1-A-2"},
        ],
        "exercises": [
            {"kp": "牛顿运动定律", "type": "short_answer", "difficulty": "medium", "score": 10,
             "content": "质量 2 kg 的物体受 6 N 合外力，加速度为 ____ m/s²",
             "options": [], "answer": "3",
             "analysis": "牛顿第二定律 F=ma，a=F/m=6/2=3 m/s²。"},
        ],
    },
    {
        "subject": "化学", "grade": "高一",
        "kps": [
            {"name": "高一化学", "parent": None, "difficulty": None, "requirement": None, "code": "C1"},
            {"name": "物质及其变化", "parent": "高一化学", "difficulty": None, "requirement": None, "code": "C1-A"},
            {"name": "氧化还原反应", "parent": "物质及其变化", "difficulty": "hard", "requirement": "掌握", "code": "C1-A-1"},
            {"name": "离子反应", "parent": "物质及其变化", "difficulty": "medium", "requirement": "理解", "code": "C1-A-2"},
        ],
        "exercises": [
            {"kp": "氧化还原反应", "type": "single_choice", "difficulty": "medium", "score": 5,
             "content": "反应 2Na + Cl₂ = 2NaCl 中，氧化剂是（）",
             "options": [{"key": "A", "text": "Na"}, {"key": "B", "text": "Cl₂"}, {"key": "C", "text": "NaCl"}, {"key": "D", "text": "都不是"}],
             "answer": "B",
             "analysis": "Cl₂ 中氯元素化合价从 0 降到 -1，得电子被还原，作氧化剂。"},
        ],
    },
]

# ── 学情练习作答（高一数学，2 学生；错答体现不同错因）─────────
# (kp, 题型, [(学生序号, 得分, 作答)])。学生 0=student01（较好）, 1=student02（薄弱）
HIGH_PRACTICE = [
    ("函数的概念与性质", "fill_blank",      [(0, 5, "[1,+∞)"), (1, 5, "[1,+∞)")]),   # 都对
    ("指数函数与对数函数", "fill_blank",    [(0, 5, "3"), (1, 0, "2")]),              # s2 错：把 log₂8 当 2³ 算成 2
    ("三角函数", "fill_blank",              [(0, 5, "1/2"), (1, 5, "1/2")]),          # 都对
    ("函数的概念与性质", "single_choice",   [(0, 5, "B"), (1, 0, "A")]),              # s2 错：把 x 当偶函数 概念错
]

# ── 高中干预策略（strat_level × error_type）────────────────────
HIGH_INTERVENTIONS = [
    ("e0000a00-0000-4000-8000-000000000001", "weak", "concept", None,
     "函数概念梳理：用图像对比单调性、奇偶性的定义，区分 f(-x)=f(x) 与 f(-x)=-f(x)，建立概念框架。"),
    ("e0000a00-0000-4000-8000-000000000002", "weak", "calculation", None,
     "对数运算专项：强化 logₐMⁿ=n·logₐM、logₐ(MN)=logₐM+logₐN 等运算法则，配套换底公式训练。"),
    ("e0000a00-0000-4000-8000-000000000003", "good", "method", None,
     "达标生方法提升：函数综合题变式训练，提升数形结合与分类讨论的迁移能力。"),
    ("e0000a00-0000-4000-8000-000000000004", "all", "all", None,
     "分层作业：薄弱生做基础概念题、达标生做综合题、优生做导数压轴题，分层布置分别批改。"),
]


async def _clear_high(conn):
    """只清高中数据（grade IN 高中年级），保留小学六年级。"""
    high_grades = ("高一", "高二", "高三")
    # 按外键依赖逆序清理
    await conn.execute("DELETE FROM intervention_strategies WHERE grade = ANY($1)", high_grades)
    await conn.execute("DELETE FROM student_practice_records WHERE grade = ANY($1)", high_grades)
    await conn.execute("DELETE FROM assignment_exercises WHERE exercise_id IN "
                       "(SELECT exercise_id FROM exercise_bank WHERE grade = ANY($1))", high_grades)
    await conn.execute("DELETE FROM lesson_exercise_rel WHERE exercise_id IN "
                       "(SELECT exercise_id FROM exercise_bank WHERE grade = ANY($1))", high_grades)
    await conn.execute("DELETE FROM lesson_knowledge_rel WHERE kp_id IN "
                       "(SELECT kp_id FROM knowledge_points WHERE grade = ANY($1))", high_grades)
    await conn.execute("DELETE FROM exercise_bank WHERE grade = ANY($1)", high_grades)
    await conn.execute("DELETE FROM lesson_templates WHERE grade = ANY($1)", high_grades)
    await conn.execute("DELETE FROM knowledge_points WHERE grade = ANY($1)", high_grades)


async def seed_stage_kps(conn, subject, grade, kps) -> dict:
    name_to_id = {}
    for kp in kps:
        kp_id = await conn.fetchval(
            "INSERT INTO knowledge_points (subject, grade, kp_name, parent_kp_id, difficulty_level, requirement_level, curriculum_code)"
            " VALUES ($1, $2, $3, NULL, $4, $5, $6) RETURNING kp_id",
            subject, grade, kp["name"], kp["difficulty"], kp["requirement"], kp["code"],
        )
        name_to_id[kp["name"]] = kp_id
    for kp in kps:
        if kp["parent"]:
            await conn.execute(
                "UPDATE knowledge_points SET parent_kp_id = $1 WHERE kp_id = $2",
                name_to_id[kp["parent"]], name_to_id[kp["name"]],
            )
    return name_to_id


async def main():
    s = get_settings()
    print(f"连接 PG: {s.db_host}:{s.db_port}/{s.db_name}")
    conn = await asyncpg.connect(
        host=s.db_host, port=s.db_port, user=s.db_user,
        password=s.db_password, database=s.db_name, timeout=20,
    )
    try:
        async with conn.transaction():
            await _clear_high(conn)
            print("清理高中旧数据完成（保留小学六年级）")

            for st in STAGES:
                subject, grade = st["subject"], st["grade"]
                name_to_id = await seed_stage_kps(conn, subject, grade, st["kps"])
                # 教案模板（3 课型 × 每学段）
                for t in LESSON_TEMPLATES:
                    await conn.execute(
                        "INSERT INTO lesson_templates (subject, grade, lesson_type, template_structure)"
                        " VALUES ($1, $2, $3, $4)",
                        subject, grade, t["lesson_type"], json.dumps(t["structure"], ensure_ascii=False),
                    )
                # 习题
                for e in st["exercises"]:
                    await conn.execute(
                        "INSERT INTO exercise_bank (subject, grade, topic, kp_id, question_type, content,"
                        " options, answer, analysis, difficulty, score, knowledge_tag, quality_status, created_by)"
                        " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, 'checked', NULL)",
                        subject, grade, e["kp"], name_to_id[e["kp"]], e["type"], e["content"],
                        json.dumps(e["options"], ensure_ascii=False), e["answer"], e["analysis"],
                        e["difficulty"], e["score"], e["kp"],
                    )
                print(f"  {subject} {grade}：知识点 {len(st['kps'])} / 习题 {len(st['exercises'])}")

            # ── 学情练习（高一数学）──
            students = await conn.fetch(
                "SELECT id FROM users WHERE role='student' ORDER BY username")
            if len(students) < 2:
                print("警告：学生不足 2 个，请先 seed_data.py 建用户")
                return
            s1, s2 = students[0]["id"], students[1]["id"]
            count = 0
            for kp, qtype, answers in HIGH_PRACTICE:
                ex = await conn.fetchrow(
                    "SELECT exercise_id, score FROM exercise_bank "
                    "WHERE knowledge_tag = $1 AND question_type = $2 AND subject = '数学' AND grade = '高一' LIMIT 1",
                    kp, qtype,
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
                        f"e0000a01-0000-4000-8000-{count:012d}",
                        TENANT_ID, sid, ex["exercise_id"], kp,
                        "数学", "高一", answer, score == ex["score"], score,
                    )
                    count += 1
            print(f"灌入 {count} 条高一数学 practice 记录")

            # ── 干预策略（高中通用，grade='高一' 挂高一数学）──
            for sid, strat_level, error_type, kp_name, content in HIGH_INTERVENTIONS:
                await conn.execute(
                    "INSERT INTO intervention_strategies "
                    "(id, tenant_id, subject, grade, strat_level, error_type, kp_id, strategy_content) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
                    sid, TENANT_ID, "数学", "高一", strat_level, error_type, None, content,
                )
            print(f"灌入 {len(HIGH_INTERVENTIONS)} 条高中干预策略")

        kp_count = await conn.fetchval("SELECT count(*) FROM knowledge_points WHERE grade = ANY($1)", ("高一", "高二", "高三"))
        ex_count = await conn.fetchval("SELECT count(*) FROM exercise_bank WHERE grade = ANY($1)", ("高一", "高二", "高三"))
        print(f"✅ 高中数据灌入完成：知识点 {kp_count} / 习题 {ex_count}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
