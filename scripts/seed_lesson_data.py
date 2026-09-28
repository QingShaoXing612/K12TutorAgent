# scripts/seed_lesson_data.py
# 备课教学最小示例数据：六年级数学知识点树 + 教案模板 + 配套习题
# 运行：PYTHONPATH=. PYTHONUTF8=1 .venv/Scripts/python.exe scripts/seed_lesson_data.py
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncpg
from backend.config import get_settings

SUBJECT = "数学"
GRADE = "六年级"

# ── 六年级数学知识点树（人教版：3 大类 + 11 叶子）────────────
# 字段：(name, parent, difficulty, requirement_level 课标层级, curriculum_code)
KNOWLEDGE_POINTS = [
    {"name": "六年级数学", "parent": None,         "difficulty": None,     "requirement": None,   "code": "RJ-6"},
    {"name": "数与代数",   "parent": "六年级数学", "difficulty": None,     "requirement": None,   "code": "RJ-6-A"},
    {"name": "图形与几何", "parent": "六年级数学", "difficulty": None,     "requirement": None,   "code": "RJ-6-B"},
    {"name": "统计与概率", "parent": "六年级数学", "difficulty": None,     "requirement": None,   "code": "RJ-6-C"},
    {"name": "分数乘法",   "parent": "数与代数",   "difficulty": "medium", "requirement": "掌握", "code": "RJ-6-A-1"},
    {"name": "分数除法",   "parent": "数与代数",   "difficulty": "medium", "requirement": "掌握", "code": "RJ-6-A-2"},
    {"name": "比",         "parent": "数与代数",   "difficulty": "easy",   "requirement": "理解", "code": "RJ-6-A-3"},
    {"name": "百分数",     "parent": "数与代数",   "difficulty": "medium", "requirement": "掌握", "code": "RJ-6-A-4"},
    {"name": "负数",       "parent": "数与代数",   "difficulty": "easy",   "requirement": "了解", "code": "RJ-6-A-5"},
    {"name": "比例",       "parent": "数与代数",   "difficulty": "hard",   "requirement": "理解", "code": "RJ-6-A-6"},
    {"name": "圆的认识",   "parent": "图形与几何", "difficulty": "easy",   "requirement": "了解", "code": "RJ-6-B-1"},
    {"name": "圆的周长与面积", "parent": "图形与几何", "difficulty": "medium", "requirement": "掌握", "code": "RJ-6-B-2"},
    {"name": "圆柱",       "parent": "图形与几何", "difficulty": "hard",   "requirement": "掌握", "code": "RJ-6-B-3"},
    {"name": "圆锥",       "parent": "图形与几何", "difficulty": "hard",   "requirement": "理解", "code": "RJ-6-B-4"},
    {"name": "扇形统计图", "parent": "统计与概率", "difficulty": "easy",   "requirement": "了解", "code": "RJ-6-C-1"},
]

# ── 教案模板（3 种课型）──────────────────────────────────────

def _template_structure(lesson_type_hint: str, process_steps: list) -> dict:
    """标准教案模板结构（对应教案整合节点的 sections）"""
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

# ── 配套习题（34 道，覆盖 11 个知识点 + 多题型 + 难度分层）──────
EXERCISES = [
    {"kp": "分数乘法", "type": "fill_blank", "difficulty": "medium", "score": 5,
     "content": "计算：3/4 × 2/5 = ____",
     "options": [], "answer": "3/10",
     "analysis": "分数乘法：分子乘分子、分母乘分母，3×2=6，4×5=20，约分得 3/10。"},
    {"kp": "分数除法", "type": "short_answer", "difficulty": "medium", "score": 10,
     "content": "一个数的 2/3 是 12，这个数是多少？",
     "options": [], "answer": "18",
     "analysis": "已知一个数的几分之几是多少，求这个数用除法：12 ÷ 2/3 = 12 × 3/2 = 18。"},
    {"kp": "比", "type": "fill_blank", "difficulty": "easy", "score": 5,
     "content": "化简比：12 : 18 = ____ : ____",
     "options": [], "answer": "2:3",
     "analysis": "前后项同时除以最大公因数 6，得 2:3。"},
    {"kp": "百分数", "type": "short_answer", "difficulty": "medium", "score": 10,
     "content": "某商品原价 200 元，打八五折出售，售价是多少元？",
     "options": [], "answer": "170",
     "analysis": "八五折即 85%，200 × 85% = 170 元。"},
    {"kp": "负数", "type": "single_choice", "difficulty": "easy", "score": 5,
     "content": "下列各数中，最小的是（）",
     "options": [{"key": "A", "text": "-5"}, {"key": "B", "text": "-2"}, {"key": "C", "text": "0"}, {"key": "D", "text": "3"}],
     "answer": "A",
     "analysis": "负数小于 0 和正数；两个负数比较，绝对值大的反而小，-5 最小。"},
    {"kp": "比例", "type": "short_answer", "difficulty": "hard", "score": 10,
     "content": "解比例：3 : 4 = x : 12，则 x = ____",
     "options": [], "answer": "9",
     "analysis": "比例内项积等于外项积：4x = 3×12，x = 36÷4 = 9。"},
    {"kp": "圆的认识", "type": "single_choice", "difficulty": "easy", "score": 5,
     "content": "圆是轴对称图形，它有（）条对称轴。",
     "options": [{"key": "A", "text": "1"}, {"key": "B", "text": "2"}, {"key": "C", "text": "4"}, {"key": "D", "text": "无数"}],
     "answer": "D",
     "analysis": "圆的任意一条直径所在直线都是对称轴，所以有无数条。"},
    {"kp": "圆的周长与面积", "type": "short_answer", "difficulty": "medium", "score": 10,
     "content": "一个圆的半径是 5 厘米，求它的周长（π 取 3.14）。",
     "options": [], "answer": "31.4",
     "analysis": "C = 2πr = 2 × 3.14 × 5 = 31.4 厘米。"},
    {"kp": "圆柱", "type": "short_answer", "difficulty": "hard", "score": 10,
     "content": "一个圆柱的底面半径是 3 厘米，高是 10 厘米，求体积（π 取 3.14）。",
     "options": [], "answer": "282.6",
     "analysis": "V = πr²h = 3.14 × 9 × 10 = 282.6 立方厘米。"},
    {"kp": "圆锥", "type": "short_answer", "difficulty": "hard", "score": 10,
     "content": "一个圆锥的底面半径是 3 厘米，高是 6 厘米，求体积（π 取 3.14）。",
     "options": [], "answer": "56.52",
     "analysis": "V = 1/3 πr²h = 1/3 × 3.14 × 9 × 6 = 56.52 立方厘米。"},
    {"kp": "扇形统计图", "type": "single_choice", "difficulty": "easy", "score": 5,
     "content": "扇形统计图能清楚地表示（）",
     "options": [{"key": "A", "text": "各部分数量与总数的关系"}, {"key": "B", "text": "数量的增减变化情况"}, {"key": "C", "text": "数量的多少"}, {"key": "D", "text": "以上都不对"}],
     "answer": "A",
     "analysis": "扇形统计图用扇形大小表示各部分占整体的百分比，反映部分与整体的关系。"},
    {"kp": "分数乘法", "type": "multi_choice", "difficulty": "medium", "score": 5,
     "content": "关于分数乘法的计算，下列说法正确的是（）",
     "options": [{"key": "A", "text": "分数乘整数，用分子与整数相乘的积作分子，分母不变"},
                 {"key": "B", "text": "分数乘分数，用分子相乘的积作分子，分母相乘的积作分母"},
                 {"key": "C", "text": "计算时能约分的可以先约分再计算"},
                 {"key": "D", "text": "任何分数乘 0 都得 0"}],
     "answer": "ABCD",
     "analysis": "四项都是分数乘法的基本运算法则，均正确。"},
    # ── 以下为扩充习题（每个知识点补 1-2 道，题型/难度分层）────
    {"kp": "分数乘法", "type": "single_choice", "difficulty": "medium", "score": 5,
     "content": "计算 2/3 × 3/4 的结果是（）",
     "options": [{"key": "A", "text": "1/2"}, {"key": "B", "text": "2/7"}, {"key": "C", "text": "5/7"}, {"key": "D", "text": "1"}],
     "answer": "A",
     "analysis": "2/3 × 3/4 = 6/12，约分得 1/2。"},
    {"kp": "分数乘法", "type": "short_answer", "difficulty": "medium", "score": 10,
     "content": "一台拖拉机每小时耕地 3/4 公顷，2/3 小时能耕地多少公顷？",
     "options": [], "answer": "1/2",
     "analysis": "工作总量 = 工作效率 × 时间 = 3/4 × 2/3 = 6/12 = 1/2 公顷。"},
    {"kp": "分数除法", "type": "fill_blank", "difficulty": "easy", "score": 5,
     "content": "计算：5/6 ÷ 5 = ____",
     "options": [], "answer": "1/6",
     "analysis": "分数除以整数，等于乘这个整数的倒数：5/6 × 1/5 = 1/6。"},
    {"kp": "分数除法", "type": "single_choice", "difficulty": "medium", "score": 5,
     "content": "小明 1/3 小时走了 2 千米，他平均每小时走（）千米。",
     "options": [{"key": "A", "text": "1/6"}, {"key": "B", "text": "2/3"}, {"key": "C", "text": "6"}, {"key": "D", "text": "8"}],
     "answer": "C",
     "analysis": "速度 = 路程 ÷ 时间 = 2 ÷ 1/3 = 2 × 3 = 6 千米/小时。"},
    {"kp": "比", "type": "short_answer", "difficulty": "medium", "score": 10,
     "content": "把 30 克盐放入 120 克水中，盐与盐水的质量比是多少？",
     "options": [], "answer": "1:5",
     "analysis": "盐水 = 30 + 120 = 150 克，盐:盐水 = 30:150 = 1:5。"},
    {"kp": "比", "type": "fill_blank", "difficulty": "easy", "score": 5,
     "content": "求比值：8 : 5 = ____",
     "options": [], "answer": "8/5",
     "analysis": "比值 = 前项 ÷ 后项 = 8 ÷ 5 = 8/5（也可写成 1.6）。"},
    {"kp": "百分数", "type": "fill_blank", "difficulty": "easy", "score": 5,
     "content": "0.45 用百分数表示是 ____",
     "options": [], "answer": "45%",
     "analysis": "小数化百分数：小数点向右移动两位，添上百分号，0.45 = 45%。"},
    {"kp": "百分数", "type": "single_choice", "difficulty": "medium", "score": 5,
     "content": "六年级有 200 名学生，今天到校 196 名，出勤率是（）",
     "options": [{"key": "A", "text": "96%"}, {"key": "B", "text": "98%"}, {"key": "C", "text": "99%"}, {"key": "D", "text": "100%"}],
     "answer": "B",
     "analysis": "出勤率 = 出勤人数 ÷ 总人数 × 100% = 196 ÷ 200 = 98%。"},
    {"kp": "负数", "type": "fill_blank", "difficulty": "easy", "score": 5,
     "content": "如果向东走 5 米记作 +5 米，那么向西走 3 米记作 ____ 米。",
     "options": [], "answer": "-3",
     "analysis": "正负数表示相反意义的量，向西与向东相反，记作负数 -3。"},
    {"kp": "负数", "type": "short_answer", "difficulty": "easy", "score": 10,
     "content": "某地某天气温从 -3℃ 上升到 5℃，气温上升了多少摄氏度？",
     "options": [], "answer": "8",
     "analysis": "上升温度 = 5 - (-3) = 5 + 3 = 8℃。"},
    {"kp": "比例", "type": "single_choice", "difficulty": "medium", "score": 5,
     "content": "下面各组比中，能组成比例的是（）",
     "options": [{"key": "A", "text": "3:4 和 4:3"}, {"key": "B", "text": "2:3 和 4:6"}, {"key": "C", "text": "1:2 和 2:1"}, {"key": "D", "text": "5:6 和 6:5"}],
     "answer": "B",
     "analysis": "2:3 与 4:6 化简后比值相等（都为 2/3），能组成比例。"},
    {"kp": "比例", "type": "fill_blank", "difficulty": "medium", "score": 5,
     "content": "在比例 2 : 5 = 6 : 15 中，两个内项分别是 ____ 和 ____。",
     "options": [], "answer": "5:6",
     "analysis": "比例中靠近等号的两个项为内项，即 5 和 6。"},
    {"kp": "圆的认识", "type": "fill_blank", "difficulty": "easy", "score": 5,
     "content": "连接圆心和圆上任意一点的线段叫做 ____。",
     "options": [], "answer": "半径",
     "analysis": "半径是圆心到圆上任意一点的线段，用 r 表示。"},
    {"kp": "圆的认识", "type": "single_choice", "difficulty": "easy", "score": 5,
     "content": "在同圆或等圆中，直径是半径的（）",
     "options": [{"key": "A", "text": "1/2 倍"}, {"key": "B", "text": "1 倍"}, {"key": "C", "text": "2 倍"}, {"key": "D", "text": "4 倍"}],
     "answer": "C",
     "analysis": "直径通过圆心且两端在圆上，d = 2r，直径是半径的 2 倍。"},
    {"kp": "圆的周长与面积", "type": "fill_blank", "difficulty": "easy", "score": 5,
     "content": "圆的周长计算公式 C = ____（用直径 d 和 π 表示）。",
     "options": [], "answer": "πd",
     "analysis": "圆周长 C = πd = 2πr。"},
    {"kp": "圆的周长与面积", "type": "short_answer", "difficulty": "medium", "score": 10,
     "content": "一个圆的直径是 10 厘米，它的面积是多少平方厘米？（π 取 3.14）",
     "options": [], "answer": "78.5",
     "analysis": "半径 r = 10 ÷ 2 = 5 厘米，S = πr² = 3.14 × 25 = 78.5 平方厘米。"},
    {"kp": "圆柱", "type": "single_choice", "difficulty": "easy", "score": 5,
     "content": "圆柱的侧面沿高展开后是一个（）",
     "options": [{"key": "A", "text": "圆"}, {"key": "B", "text": "长方形"}, {"key": "C", "text": "梯形"}, {"key": "D", "text": "三角形"}],
     "answer": "B",
     "analysis": "圆柱侧面沿高展开是一个长方形（或正方形），长等于底面周长，宽等于高。"},
    {"kp": "圆柱", "type": "fill_blank", "difficulty": "medium", "score": 5,
     "content": "一个圆柱的底面积是 20 平方厘米，高是 5 厘米，体积是 ____ 立方厘米。",
     "options": [], "answer": "100",
     "analysis": "V = Sh = 20 × 5 = 100 立方厘米。"},
    {"kp": "圆锥", "type": "fill_blank", "difficulty": "easy", "score": 5,
     "content": "等底等高的圆锥体积是圆柱体积的 ____。",
     "options": [], "answer": "1/3",
     "analysis": "等底等高的圆锥体积是圆柱体积的三分之一，V 锥 = 1/3 Sh。"},
    {"kp": "圆锥", "type": "single_choice", "difficulty": "easy", "score": 5,
     "content": "一个圆锥有（）条高。",
     "options": [{"key": "A", "text": "1"}, {"key": "B", "text": "2"}, {"key": "C", "text": "3"}, {"key": "D", "text": "无数"}],
     "answer": "A",
     "analysis": "圆锥只有一条高，即从顶点到底面圆心的距离。"},
    {"kp": "扇形统计图", "type": "short_answer", "difficulty": "easy", "score": 10,
     "content": "在扇形统计图中，某部分占总体的 25%，这部分对应的圆心角是多少度？",
     "options": [], "answer": "90",
     "analysis": "圆心角 = 360° × 25% = 90°。"},
    {"kp": "扇形统计图", "type": "single_choice", "difficulty": "easy", "score": 5,
     "content": "在扇形统计图中，某部分占总体的一半，对应的圆心角是（）",
     "options": [{"key": "A", "text": "90°"}, {"key": "B", "text": "180°"}, {"key": "C", "text": "270°"}, {"key": "D", "text": "360°"}],
     "answer": "B",
     "analysis": "一半即 50%，圆心角 = 360° × 50% = 180°。"},
]


async def seed_knowledge_points(conn) -> dict:
    """插入知识点树，返回 name → kp_id 映射（两步：先插全节点，再回填 parent_kp_id）"""
    name_to_id = {}
    for kp in KNOWLEDGE_POINTS:
        kp_id = await conn.fetchval(
            "INSERT INTO knowledge_points (subject, grade, kp_name, parent_kp_id, difficulty_level, requirement_level, curriculum_code)"
            " VALUES ($1, $2, $3, NULL, $4, $5, $6) RETURNING kp_id",
            SUBJECT, GRADE, kp["name"], kp["difficulty"], kp["requirement"], kp["code"],
        )
        name_to_id[kp["name"]] = kp_id
    for kp in KNOWLEDGE_POINTS:
        if kp["parent"]:
            await conn.execute(
                "UPDATE knowledge_points SET parent_kp_id = $1 WHERE kp_id = $2",
                name_to_id[kp["parent"]], name_to_id[kp["name"]],
            )
    return name_to_id


async def seed_lesson_templates(conn):
    for t in LESSON_TEMPLATES:
        await conn.execute(
            "INSERT INTO lesson_templates (subject, grade, lesson_type, template_structure)"
            " VALUES ($1, $2, $3, $4)",
            SUBJECT, GRADE, t["lesson_type"], json.dumps(t["structure"], ensure_ascii=False),
        )


async def seed_exercise_bank(conn, name_to_id: dict):
    for e in EXERCISES:
        await conn.execute(
            "INSERT INTO exercise_bank (subject, grade, topic, kp_id, question_type, content,"
            " options, answer, analysis, difficulty, score, knowledge_tag, quality_status, created_by)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, 'checked', NULL)",
            SUBJECT, GRADE, e["kp"], name_to_id[e["kp"]], e["type"], e["content"],
            json.dumps(e["options"], ensure_ascii=False), e["answer"], e["analysis"],
            e["difficulty"], e["score"], e["kp"],
        )


async def main():
    s = get_settings()
    print(f"连接 PG: {s.db_host}:{s.db_port}/{s.db_name}")
    conn = await asyncpg.connect(
        host=s.db_host, port=s.db_port, user=s.db_user,
        password=s.db_password, database=s.db_name, timeout=20,
    )
    try:
        async with conn.transaction():
            # 清旧（按外键依赖逆序；exercise_bank/knowledge_points 已被学情/作业模块引用，先清依赖表）
            await conn.execute("DELETE FROM intervention_strategies")   # 学情 → knowledge_points
            await conn.execute("DELETE FROM student_practice_records")  # 学情 → exercise_bank
            await conn.execute("DELETE FROM assignment_exercises")      # 作业 → exercise_bank
            await conn.execute("DELETE FROM lesson_exercise_rel")
            await conn.execute("DELETE FROM lesson_knowledge_rel")
            await conn.execute("DELETE FROM exercise_bank")
            await conn.execute("DELETE FROM lesson_templates")
            await conn.execute("DELETE FROM knowledge_points")
            name_to_id = await seed_knowledge_points(conn)
            await seed_lesson_templates(conn)
            await seed_exercise_bank(conn, name_to_id)

        kp_count = await conn.fetchval("SELECT count(*) FROM knowledge_points")
        tp_count = await conn.fetchval("SELECT count(*) FROM lesson_templates")
        ex_count = await conn.fetchval("SELECT count(*) FROM exercise_bank")
        print(f"✅ 灌入完成：知识点 {kp_count} / 模板 {tp_count} / 习题 {ex_count}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
