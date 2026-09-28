# backend/agents/lesson_prep/nodes.py

import asyncio
import json
import uuid as _uuid

from pydantic import BaseModel, Field
from sqlalchemy import text
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import interrupt

from backend.agents.lesson_prep.state import LessonPrepState
from backend.agents.lesson_prep.prompts import (
    SYSTEM_PROMPT,
    REQUIREMENT_PARSE_PROMPT,
    OBJECTIVE_GENERATE_PROMPT,
    KEY_POINT_PROMPT,
    PROCESS_GENERATE_PROMPT,
    EXERCISE_MATCH_PROMPT,
    QUALITY_CHECK_PROMPT,
    BOARD_DESIGN_PROMPT,
    REFLECTION_PROMPT,
)
from backend.core.llm_factory import get_structured_llm
from backend.core.logger import get_logger
from backend.dependencies import AsyncSessionLocal

logger = get_logger(__name__)


# ──────────────────────────────────────────────────────────────
# 结构化输出 Schema
# ──────────────────────────────────────────────────────────────

class RetrievalQueries(BaseModel):
    """需求解析的 LLM 输出：优化后的检索 query 列表。"""
    queries: list[str] = Field(..., description="3-5 个优化后的检索 query，供语义检索多路召回")


# ──────────────────────────────────────────────────────────────
# 节点：requirement_parse — 需求解析（匹配知识点树 + 生成检索 query）
# ──────────────────────────────────────────────────────────────

async def requirement_parse_node(state: LessonPrepState) -> dict:
    """
    需求解析：
      ① 查 PG knowledge_points，按 subject+grade 匹配 topic，构建知识点（含父路径）
      ② LLM 解析 teacher_requirement + topic，生成优化后的检索 query 列表
    """
    subject     = state["subject"]
    grade       = state["grade"]
    topic       = state["topic"]
    requirement = state.get("teacher_requirement", "")

    knowledge_points = await _match_knowledge_points(subject, grade, topic)
    retrieval_queries = await _generate_retrieval_queries(topic, requirement, knowledge_points)

    logger.info(
        "requirement_parse.done",
        kp=len(knowledge_points),
        queries=len(retrieval_queries),
    )
    return {
        "knowledge_points":  knowledge_points,
        "retrieval_queries": retrieval_queries,
    }


async def _match_knowledge_points(subject: str, grade: str, topic: str) -> list[dict]:
    """查 PG knowledge_points，匹配 topic，构建含父路径的知识点列表。"""
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            text("""
                SELECT kp_id, kp_name, parent_kp_id, difficulty_level, requirement_level, curriculum_code
                FROM knowledge_points WHERE subject = :s AND grade = :g
            """),
            {"s": subject, "g": grade},
        )
        kps = {}
        for r in rows:
            kps[str(r.kp_id)] = {
                "kp_id":             str(r.kp_id),
                "kp_name":           r.kp_name,
                "parent_kp_id":      str(r.parent_kp_id) if r.parent_kp_id else None,
                "difficulty_level":  r.difficulty_level,
                "requirement_level": r.requirement_level,
                "curriculum_code":   r.curriculum_code,
            }

    if not kps:
        logger.warning("requirement_parse.no_knowledge_points", subject=subject, grade=grade)
        return []

    # 匹配：精确优先，其次双向包含（topic 含知识点名 或 知识点名含 topic）
    matched = [kp for kp in kps.values() if kp["kp_name"] == topic]
    if not matched:
        matched = [kp for kp in kps.values() if topic in kp["kp_name"] or kp["kp_name"] in topic]

    result = []
    for kp in matched:
        # 构建父路径（从根到父，如「圆的周长与面积」→ ["图形与几何"]）
        path = []
        cur = kp
        while cur.get("parent_kp_id") and cur["parent_kp_id"] in kps:
            cur = kps[cur["parent_kp_id"]]
            path.append(cur["kp_name"])
        result.append({
            "kp_id":             kp["kp_id"],
            "name":              kp["kp_name"],
            "difficulty_level":  kp["difficulty_level"],
            "requirement_level": kp["requirement_level"],
            "curriculum_code":   kp["curriculum_code"],
            "parent_path":       list(reversed(path)),
        })
    return result


async def _generate_retrieval_queries(topic: str, requirement: str, kps: list[dict]) -> list[str]:
    """LLM 生成面向 RAG 多路召回的检索 query 列表。"""
    kp_names = "、".join(k["name"] for k in kps) or "（未匹配到知识点，按主题直接检索）"
    prompt = REQUIREMENT_PARSE_PROMPT.format(
        topic=topic,
        requirement=requirement or "（无）",
        knowledge_points=kp_names,
    )
    structured = get_structured_llm("lesson_prep", RetrievalQueries)
    result: RetrievalQueries = await structured.ainvoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ])
    return result.queries


# ──────────────────────────────────────────────────────────────
# 节点：semantic_retrieve — 语义检索（双库协同：Milvus 多路召回 + PG 精确查）
# ──────────────────────────────────────────────────────────────

MILVUS_TOP_K = 6   # 每个 query 的 Hybrid 召回数（多路合并去重）

async def semantic_retrieve_node(state: LessonPrepState) -> dict:
    """
    语义检索：
      ① Milvus 多路召回：retrieval_queries 分别 Hybrid 检索，合并去重，按 resource_type 分组
      ② PG 检索：教案模板 + 历史教案 + 配套习题候选（按 subject+grade+知识点）
    """
    queries   = state.get("retrieval_queries", [])
    subject   = state["subject"]
    grade     = state["grade"]
    tenant_id = state["tenant_id"]
    kps       = state.get("knowledge_points", [])
    loop      = asyncio.get_running_loop()

    # 降级：query 为空时用 topic 兜底
    if not queries:
        queries = [state["topic"]]

    # ① Milvus 多路召回（同步阻塞，放线程池）
    milvus_groups = await loop.run_in_executor(
        None, _milvus_multi_recall, queries, tenant_id, subject, grade,
    )

    # ② PG 检索（模板 / 历史教案 / 习题候选）
    pg_templates, history_plans, exercise_candidates = await _pg_retrieve(subject, grade, kps)

    retrieved_resources = {
        **milvus_groups,
        "lesson_templates":     pg_templates,
        "history_lesson_plans": history_plans,
    }

    logger.info(
        "semantic_retrieve.done",
        resources={k: len(v) for k, v in retrieved_resources.items()},
        exercise_candidates=len(exercise_candidates),
    )
    return {
        "retrieved_resources": retrieved_resources,
        "exercise_candidates": exercise_candidates,
    }


def _milvus_multi_recall(queries: list[str], tenant_id: str, subject: str, grade: str) -> dict:
    """Milvus 多路召回：多 query 分别 Hybrid 检索，合并去重，按 resource_type 分组。"""
    from backend.core.knowledge_base import BGEMEmbedder, KnowledgeBaseClient

    kb = KnowledgeBaseClient()
    embedder = BGEMEmbedder.get_instance()
    # 备课素材按 subject+grade 过滤（不按 course_id）
    filters = kb._build_filter(tenant_id, subject=subject, grade=grade)

    dedup: dict = {}
    for query in queries:
        dense, sparse = embedder.encode_query(query)
        candidates = kb._hybrid_search(dense, sparse, top_k=MILVUS_TOP_K, filters=filters)
        for c in candidates:
            key = (c["metadata"].get("document_id"), c["metadata"].get("chunk_index"))
            if key not in dedup:
                dedup[key] = c

    groups = {
        "reference_lesson_plans": [],
        "curriculums":           [],
        "papers":                [],
        "materials":             [],
    }
    type_map = {
        "lesson_plan": "reference_lesson_plans",
        "curriculum":  "curriculums",
        "paper":       "papers",
        "material":    "materials",
    }
    for c in dedup.values():
        target = type_map.get(c["metadata"].get("resource_type", ""))
        if target:
            groups[target].append(c)
    return groups


async def _pg_retrieve(subject: str, grade: str, kps: list[dict]):
    """PG 检索：教案模板 + 历史教案 + 配套习题候选（按 subject+grade+知识点）。"""
    async with AsyncSessionLocal() as session:
        # ① 教案模板
        tpl_rows = await session.execute(
            text("SELECT template_id, lesson_type, template_structure FROM lesson_templates WHERE subject=:s AND grade=:g"),
            {"s": subject, "g": grade},
        )
        templates = [dict(r._mapping) for r in tpl_rows]

        # ② 历史教案（已正式/已发布）
        his_rows = await session.execute(
            text("SELECT lesson_id, topic, lesson_content FROM lesson_plans WHERE subject=:s AND grade=:g AND status IN ('formal','published') ORDER BY updated_at DESC LIMIT 10"),
            {"s": subject, "g": grade},
        )
        history_plans = [dict(r._mapping) for r in his_rows]

        # ③ 习题候选：按 kp_id（已质检/已沉淀），空则降级到 subject+grade 全查
        kp_uuids = [_uuid.UUID(k["kp_id"]) for k in kps if k.get("kp_id")]
        ex_sql = ("SELECT exercise_id, kp_id, question_type, content, options, answer, analysis, difficulty, quality_status "
                  "FROM exercise_bank WHERE subject=:s AND grade=:g AND quality_status IN ('checked','settled')")
        if kp_uuids:
            ex_rows = await session.execute(
                text(ex_sql + " AND kp_id = ANY(:kp)"),
                {"s": subject, "g": grade, "kp": kp_uuids},
            )
        else:
            ex_rows = await session.execute(text(ex_sql), {"s": subject, "g": grade})
        candidates = [dict(r._mapping) for r in ex_rows]

        # 降级：按 kp_id 无结果 → 学科+年级全查
        if not candidates and kp_uuids:
            ex_rows = await session.execute(text(ex_sql), {"s": subject, "g": grade})
            candidates = [dict(r._mapping) for r in ex_rows]

    # UUID → str（state 序列化安全）
    for t in templates:
        t["template_id"] = str(t["template_id"])
    for h in history_plans:
        h["lesson_id"] = str(h["lesson_id"])
    for c in candidates:
        c["exercise_id"] = str(c["exercise_id"])
        c["kp_id"] = str(c["kp_id"]) if c["kp_id"] else None

    return templates, history_plans, candidates


# ──────────────────────────────────────────────────────────────
# 结构化输出 Schema（教学目标 / 重点难点）
# ──────────────────────────────────────────────────────────────

class TeachingObjectives(BaseModel):
    """教学目标：三维目标框架。"""
    knowledge_skills: str = Field(..., description="知识与技能目标")
    process_methods:  str = Field(..., description="过程与方法目标")
    emotion_values:   str = Field(..., description="情感态度与价值观目标")


class KeyDifficultPoints(BaseModel):
    """重点难点分析结果。"""
    key_points:       str = Field(..., description="教学重点（课标核心内容）")
    difficult_points: str = Field(..., description="教学难点（学生认知障碍点）")


# ──────────────────────────────────────────────────────────────
# 节点：objective_generate — 教学目标生成（三维目标框架）
# ──────────────────────────────────────────────────────────────

async def objective_generate_node(state: LessonPrepState) -> dict:
    """基于三维目标框架 + 课标 + 知识点，生成教学目标。"""
    subject     = state["subject"]
    grade       = state["grade"]
    requirement = state.get("teacher_requirement", "")
    kps         = state.get("knowledge_points", [])
    curriculums = state.get("retrieved_resources", {}).get("curriculums", [])

    kp_text = "、".join(k["name"] for k in kps) or "（未匹配到知识点）"
    curriculum_text = _build_context(curriculums)

    prompt = OBJECTIVE_GENERATE_PROMPT.format(
        subject=subject,
        grade=grade,
        knowledge_points=kp_text,
        curriculums=curriculum_text,
        requirement=requirement or "（无）",
    )
    structured = get_structured_llm("lesson_prep", TeachingObjectives)
    result: TeachingObjectives = await structured.ainvoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ])

    logger.info("objective_generate.done")
    return {"teaching_objectives": {
        "knowledge_skills": result.knowledge_skills,
        "process_methods":  result.process_methods,
        "emotion_values":   result.emotion_values,
    }}


# ──────────────────────────────────────────────────────────────
# 节点：key_point_analyze — 重点难点分析（四源交叉）
# ──────────────────────────────────────────────────────────────

async def key_point_analyze_node(state: LessonPrepState) -> dict:
    """四源交叉分析重点难点：难度基准 + 历史共识 + 错误率(降级) + 教研结论。"""
    subject   = state["subject"]
    grade     = state["grade"]
    kps       = state.get("knowledge_points", [])
    resources = state.get("retrieved_resources", {})

    # ① 知识点难度基准（knowledge_points 的 difficulty_level + requirement_level）
    kp_basis = "；".join(
        f"{k['name']}(难度={k.get('difficulty_level') or '未知'}, 课标={k.get('requirement_level') or '未知'})"
        for k in kps
    ) or "（未匹配到知识点）"

    # ② 历史教案重难点共识（节点内查 PG；最小集 lesson_plans 空则降级）
    history_consensus = await _query_history_consensus(subject, grade, kps)

    # ④ 教研/课标结论（curriculums + papers）
    research_text = _build_context(resources.get("curriculums", []) + resources.get("papers", []))

    prompt = KEY_POINT_PROMPT.format(
        subject=subject,
        grade=grade,
        kp_basis=kp_basis,
        history_consensus=history_consensus,
        research_consensus=research_text,
    )
    structured = get_structured_llm("lesson_prep", KeyDifficultPoints)
    result: KeyDifficultPoints = await structured.ainvoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ])

    logger.info("key_point_analyze.done")
    return {"key_difficult_points": {
        "key_points":       result.key_points,
        "difficult_points": result.difficult_points,
    }}


# ──────────────────────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────────────────────

def _build_context(chunks: list[dict], limit: int = 3) -> str:
    """把检索 chunk 拼成 prompt 参考文本（取前 limit 条）。"""
    if not chunks:
        return "（无参考素材）"
    parts = []
    for i, c in enumerate(chunks[:limit], 1):
        parts.append(f"【素材{i}】{c.get('content', '')[:500]}")
    return "\n\n".join(parts)


async def _query_history_consensus(subject: str, grade: str, kps: list[dict]) -> str:
    """查 PG 历史教案的同知识点重难点共识（最小集下 lesson_plans 空，降级返回提示）。"""
    kp_uuids = [_uuid.UUID(k["kp_id"]) for k in kps if k.get("kp_id")]
    if not kp_uuids:
        return "（无历史教案数据）"
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            text("""
                SELECT lp.lesson_content
                FROM lesson_plans lp
                JOIN lesson_knowledge_rel lkr ON lp.lesson_id = lkr.lesson_id
                WHERE lkr.kp_id = ANY(:kp) AND lp.status IN ('formal','published')
                LIMIT 5
            """),
            {"kp": kp_uuids},
        )
        contents = [r.lesson_content for r in rows]
    if not contents:
        return "（无历史教案数据）"
    parts = []
    for c in contents:
        if isinstance(c, dict):
            if c.get("key_points") or c.get("difficult_points"):
                parts.append(f"重点：{c.get('key_points','')}；难点：{c.get('difficult_points','')}")
    return "\n".join(parts) if parts else "（历史教案无重难点标注）"


# ──────────────────────────────────────────────────────────────
# 结构化输出 Schema（教学流程）
# ──────────────────────────────────────────────────────────────

class ProcessStep(BaseModel):
    """单个教学环节。"""
    name:             str   # 环节名，如「导入」「新授」
    duration_min:     int   # 时间分配（分钟）
    teacher_activity: str   # 教师活动
    student_activity: str   # 学生活动
    design_intent:    str   # 设计意图


class TeachingProcess(BaseModel):
    """教学流程：按课型模板的多个环节。"""
    steps: list[ProcessStep]


# ──────────────────────────────────────────────────────────────
# 节点：process_generate — 教学流程生成（按课型匹配模板 steps）
# ──────────────────────────────────────────────────────────────

LESSON_TYPE_CN = {"new": "新授课", "exercise": "习题课", "review": "复习课"}

async def process_generate_node(state: LessonPrepState) -> dict:
    """按 lesson_type 匹配模板 steps，生成每环节四要素的详细教学流程。"""
    subject       = state["subject"]
    grade         = state["grade"]
    lesson_type   = state.get("lesson_type", "new")
    duration      = state.get("duration", 40)
    objectives    = state.get("teaching_objectives", {})
    key_difficult = state.get("key_difficult_points", {})
    resources     = state.get("retrieved_resources", {})

    # 从教案模板选对应课型的流程 steps
    process_steps = _pick_process_steps(resources.get("lesson_templates", []), lesson_type)

    # 参考教案互动设计
    reference_text = _build_context(resources.get("reference_lesson_plans", []))

    objectives_text = (
        f"知识与技能：{objectives.get('knowledge_skills','')}\n"
        f"过程与方法：{objectives.get('process_methods','')}\n"
        f"情感态度价值观：{objectives.get('emotion_values','')}"
    )
    key_text = f"重点：{key_difficult.get('key_points','')}；难点：{key_difficult.get('difficult_points','')}"

    prompt = PROCESS_GENERATE_PROMPT.format(
        subject=subject,
        grade=grade,
        lesson_type=LESSON_TYPE_CN.get(lesson_type, lesson_type),
        duration=duration,
        objectives=objectives_text,
        key_difficult_points=key_text,
        process_steps=" → ".join(process_steps),
        reference_plans=reference_text,
    )
    structured = get_structured_llm("lesson_prep", TeachingProcess)
    result: TeachingProcess = await structured.ainvoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ])

    logger.info("process_generate.done", steps=len(result.steps))
    return {"teaching_process": {"steps": [s.model_dump() for s in result.steps]}}


def _pick_process_steps(templates: list[dict], lesson_type: str) -> list[str]:
    """从教案模板选对应课型的流程 steps。"""
    for t in templates:
        if t.get("lesson_type") != lesson_type:
            continue
        structure = t.get("template_structure")
        if isinstance(structure, str):
            structure = json.loads(structure)
        if isinstance(structure, dict):
            for section in structure.get("sections", []):
                if section.get("key") == "process" and section.get("steps"):
                    return section["steps"]
    # 默认新授课流程
    return ["导入", "新授", "巩固练习", "课堂小结", "作业布置"]


# ──────────────────────────────────────────────────────────────
# 结构化输出 Schema（习题匹配）
# ──────────────────────────────────────────────────────────────

class ExerciseSelection(BaseModel):
    """选中的一道习题。"""
    exercise_id: str = Field(..., description="习题 ID（引用 exercise_bank.exercise_id）")
    reason:      str = Field(..., description="适配理由")


class SelectedExercises(BaseModel):
    """两套习题：课堂练习 + 课后作业。"""
    class_exercises:    list[ExerciseSelection]
    homework_exercises: list[ExerciseSelection]


# ──────────────────────────────────────────────────────────────
# 节点：exercise_match — 习题匹配（难度分层 + LLM 适配性评估 + 分两套）
# ──────────────────────────────────────────────────────────────

DIFF_CN = {"easy": "基础", "medium": "提升", "hard": "拓展"}

async def exercise_match_node(state: LessonPrepState) -> dict:
    """从候选习题筛选分层习题，LLM 评估适配性，分课堂练习/课后作业两套。"""
    candidates    = state.get("exercise_candidates", [])
    objectives    = state.get("teaching_objectives", {})
    key_difficult = state.get("key_difficult_points", {})

    if not candidates:
        logger.warning("exercise_match.no_candidates")
        return {"selected_exercises": {"class_exercises": [], "homework_exercises": []}}

    objectives_text = (
        f"知识与技能：{objectives.get('knowledge_skills','')}；"
        f"过程与方法：{objectives.get('process_methods','')}"
    )
    key_text = f"重点：{key_difficult.get('key_points','')}；难点：{key_difficult.get('difficult_points','')}"

    prompt = EXERCISE_MATCH_PROMPT.format(
        objectives=objectives_text,
        key_difficult_points=key_text,
        candidates=_format_candidates(candidates),
    )
    structured = get_structured_llm("lesson_prep", SelectedExercises)
    result: SelectedExercises = await structured.ainvoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ])

    # 校验：过滤 LLM 编造的 exercise_id + 去重（同一题只保留一次）
    valid_ids = {c["exercise_id"] for c in candidates}

    def _keep(selections):
        kept, seen = [], set()
        for s in selections:
            if s.exercise_id in valid_ids and s.exercise_id not in seen:
                kept.append({"exercise_id": s.exercise_id, "reason": s.reason})
                seen.add(s.exercise_id)
        return kept

    # 跨套去重：同一题只保留在课堂练习，课后作业排除已选
    class_sel = _keep(result.class_exercises)
    class_ids = {s["exercise_id"] for s in class_sel}
    homework_sel = [s for s in _keep(result.homework_exercises) if s["exercise_id"] not in class_ids]

    logger.info(
        "exercise_match.done",
        class_n=len(class_sel),
        homework_n=len(homework_sel),
    )
    return {"selected_exercises": {
        "class_exercises":    class_sel,
        "homework_exercises": homework_sel,
    }}


def _format_candidates(candidates: list[dict]) -> str:
    """把候选习题格式化成 prompt 文本（含 ID + 题型 + 难度标签 + 题干）。"""
    parts = []
    for i, c in enumerate(candidates, 1):
        diff = DIFF_CN.get(c.get("difficulty"), c.get("difficulty"))
        parts.append(
            f"【题{i}】ID={c['exercise_id']}, 题型={c.get('question_type','')}, 难度={diff}\n"
            f"题干：{c.get('content','')[:200]}"
        )
    return "\n\n".join(parts)


# ──────────────────────────────────────────────────────────────
# 结构化输出 Schema（逐题质检）
# ──────────────────────────────────────────────────────────────

class QualityVerdict(BaseModel):
    """单道题的适配性质检结论。"""
    passed: bool = Field(..., description="是否通过适配性质检")
    issue:  str = Field(default="", description="未通过时的具体问题")


# ──────────────────────────────────────────────────────────────
# 节点：quality_check — 逐题质检（适配性 + 重新匹配 + 转人工）
# ──────────────────────────────────────────────────────────────

MAX_RESELECT_ROUNDS = 2   # 坏题重新匹配轮数上限

async def quality_check_node(state: LessonPrepState) -> dict:
    """
    逐题质检（适配性，非正确性——题来自已质检题库）：
      坏题优先从候选题池换备选，无备选则标 needs_review 转人工（不丢弃）。
    """
    selected      = state.get("selected_exercises", {})
    candidates    = state.get("exercise_candidates", [])
    objectives    = state.get("teaching_objectives", {})
    key_difficult = state.get("key_difficult_points", {})

    question_map = {c["exercise_id"]: c for c in candidates}
    selected_ids = {s["exercise_id"] for usage in selected.values() for s in usage}

    reports = []
    result = {"class_exercises": [], "homework_exercises": []}

    for usage in ["class_exercises", "homework_exercises"]:
        for s in selected.get(usage, []):
            q = question_map.get(s["exercise_id"])
            if q is None:
                # 题面缺失（如未来生成兜底题未入候选池），保留并标 needs_review
                result[usage].append({**s, "quality_status": "needs_review"})
                reports.append({"exercise_id": s["exercise_id"], "passed": False, "issue": "题面缺失", "status": "needs_review"})
                continue

            verdict = await _check_adaptation(q, objectives, key_difficult)
            rounds = 0
            while not verdict.passed and rounds < MAX_RESELECT_ROUNDS:
                replacement = _pick_replacement(q, candidates, selected_ids)
                if replacement is None:
                    break
                q = replacement
                s = {**s, "exercise_id": replacement["exercise_id"]}  # 换备选题
                verdict = await _check_adaptation(q, objectives, key_difficult)
                rounds += 1

            if verdict.passed:
                result[usage].append({**s, "quality_status": "passed"})
                reports.append({"exercise_id": s["exercise_id"], "passed": True})
            else:
                result[usage].append({**s, "quality_status": "needs_review"})
                reports.append({"exercise_id": s["exercise_id"], "passed": False,
                                "issue": verdict.issue or "适配性不足", "status": "needs_review"})

    logger.info(
        "quality_check.done",
        passed=sum(1 for r in reports if r["passed"]),
        needs_review=sum(1 for r in reports if not r["passed"]),
    )
    return {"selected_exercises": result, "quality_reports": reports}


async def _check_adaptation(question: dict, objectives: dict, key_difficult: dict) -> QualityVerdict:
    """LLM 适配性质检单道题。"""
    q_text = f"[题型={question.get('question_type','')}, 难度={question.get('difficulty','')}]\n{question.get('content','')}"
    opts = question.get("options")
    if opts:
        if isinstance(opts, str):
            opts = json.loads(opts)
        q_text += "\n选项：" + " ".join(f"{o['key']}.{o['text']}" for o in opts)

    prompt = QUALITY_CHECK_PROMPT.format(
        objectives=f"知识与技能：{objectives.get('knowledge_skills','')}",
        key_difficult_points=f"重点：{key_difficult.get('key_points','')}；难点：{key_difficult.get('difficult_points','')}",
        question=q_text,
    )
    structured = get_structured_llm("lesson_prep", QualityVerdict)
    return await structured.ainvoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ])


def _pick_replacement(current: dict, candidates: list[dict], selected_ids: set) -> dict | None:
    """从候选题池换一道未选中的题作备选；无则 None。"""
    for c in candidates:
        if c["exercise_id"] != current["exercise_id"] and c["exercise_id"] not in selected_ids:
            return c
    return None


# ──────────────────────────────────────────────────────────────
# 结构化输出 Schema（板书设计）
# ──────────────────────────────────────────────────────────────

class BoardDesign(BaseModel):
    """板书设计：主板书 + 副板书。"""
    main:      str = Field(..., description="主板书（核心知识点框架）")
    secondary: str = Field(..., description="副板书（演算过程、临时补充）")


# ──────────────────────────────────────────────────────────────
# 节点：lesson_plan_integrate — 教案整合（模板组装 + 板书提炼 + 题面内嵌）
# ──────────────────────────────────────────────────────────────

async def lesson_plan_integrate_node(state: LessonPrepState) -> dict:
    """
    教案整合：
      ① LLM 提炼板书（主板书=知识框架，副板书=演算过程）
      ② 习题题面内嵌（把 selected_exercises 的 exercise_id 展开成完整题面）
      ③ 组装 final_lesson_plan（规则拼接，reflection 留空待反思优化回填）
    """
    board = await _design_board(state)

    candidates = state.get("exercise_candidates", [])
    selected   = state.get("selected_exercises", {})
    class_ex   = _embed_exercises(selected.get("class_exercises", []), candidates)
    homework_ex = _embed_exercises(selected.get("homework_exercises", []), candidates)

    kd = state.get("key_difficult_points", {})
    final_plan = {
        "meta": {
            "subject":     state["subject"],
            "grade":       state["grade"],
            "topic":       state["topic"],
            "lesson_type": state.get("lesson_type", "new"),
            "duration":    state.get("duration", 40),
        },
        "objectives":       state.get("teaching_objectives", {}),
        "key_points":       kd.get("key_points", ""),
        "difficult_points": kd.get("difficult_points", ""),
        "process":          state.get("teaching_process", {}),
        "board":            board,
        "exercises":        {"class": class_ex, "homework": homework_ex},
        "reflection":       "",   # 留空待反思优化回填
    }

    logger.info("lesson_plan_integrate.done", class_n=len(class_ex), homework_n=len(homework_ex))
    return {"final_lesson_plan": final_plan}


async def _design_board(state: LessonPrepState) -> dict:
    """LLM 提炼板书（主板书 + 副板书）。"""
    kd = state.get("key_difficult_points", {})
    process = state.get("teaching_process", {})
    process_text = "\n".join(
        f"{s.get('name','')}: {s.get('teacher_activity','')[:80]}"
        for s in process.get("steps", [])
    ) or "（无）"

    prompt = BOARD_DESIGN_PROMPT.format(
        key_points=kd.get("key_points", ""),
        difficult_points=kd.get("difficult_points", ""),
        process_steps=process_text,
    )
    structured = get_structured_llm("lesson_prep", BoardDesign)
    result: BoardDesign = await structured.ainvoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ])
    return {"main": result.main, "secondary": result.secondary}


def _embed_exercises(selections: list[dict], candidates: list[dict]) -> list[dict]:
    """把 exercise_id 展开成完整题面（题干/选项/答案/解析）。"""
    qmap = {c["exercise_id"]: c for c in candidates}
    result = []
    for s in selections:
        q = qmap.get(s["exercise_id"])
        if q is None:
            result.append({
                "exercise_id":    s["exercise_id"],
                "reason":         s.get("reason", ""),
                "content":        "（题面缺失）",
                "quality_status": s.get("quality_status", ""),
            })
            continue
        result.append({
            "exercise_id":    s["exercise_id"],
            "question_type":  q.get("question_type"),
            "content":        q.get("content"),
            "options":        q.get("options"),
            "answer":         q.get("answer"),
            "analysis":       q.get("analysis"),
            "difficulty":     q.get("difficulty"),
            "reason":         s.get("reason", ""),
            "quality_status": s.get("quality_status", ""),
        })
    return result


# ──────────────────────────────────────────────────────────────
# 结构化输出 Schema（反思优化）
# ──────────────────────────────────────────────────────────────

class ReflectionResult(BaseModel):
    """反思优化：解析教师意见，定位回炉模块。"""
    revision_target: str = Field(..., description="回炉目标（objectives/key_points/process/exercises，空=满意）")
    feedback_note:   str = Field(default="", description="意见摘要")


# ──────────────────────────────────────────────────────────────
# 节点：reflection_optimize — 反思优化（解析教师意见，定位回炉模块）
# ──────────────────────────────────────────────────────────────

async def reflection_optimize_node(state: LessonPrepState) -> dict:
    """
    反思优化（形态1，Human-in-the-Loop）：
      interrupt 暂停 → 暴露教案给教师 → 教师 resume 传意见 → 解析定位回炉模块。
      revision_target 为空 = 满意，进 save；非空 = 回炉对应模块（级联重跑）。
    """
    plan = state.get("final_lesson_plan", {})
    feedback = interrupt({
        "final_lesson_plan": plan,
        "message": "请审阅教案，提出修改意见；满意回复空字符串",
    })
    feedback = str(feedback).strip() if feedback else ""
    if not feedback:
        return {"revision_target": ""}

    result = await _parse_feedback(feedback, state)
    logger.info("reflection_optimize.done", target=result.revision_target or "满意")
    return {"revision_target": result.revision_target}


async def _parse_feedback(feedback: str, state: LessonPrepState) -> ReflectionResult:
    """LLM 解析教师意见，定位回炉模块。"""
    plan_structure = ", ".join(state.get("final_lesson_plan", {}).keys()) or "（无）"
    prompt = REFLECTION_PROMPT.format(feedback=feedback, plan_structure=plan_structure)
    structured = get_structured_llm("lesson_prep", ReflectionResult)
    return await structured.ainvoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ])


# ──────────────────────────────────────────────────────────────
# 节点：save_lesson_plan — 落库（教案 + 知识点关联 + 习题关联 + 操作日志）
# ──────────────────────────────────────────────────────────────

async def save_lesson_plan_node(state: LessonPrepState) -> dict:
    """
    落库 final_lesson_plan：
      ① lesson_plans（教案主体）
      ② lesson_knowledge_rel（教案-知识点关联）
      ③ lesson_exercise_rel（教案-习题关联，usage_type 区分课堂/课后）
      ④ teacher_lesson_history（教师备课操作日志）
    """
    plan       = state.get("final_lesson_plan", {})
    teacher_id = state["teacher_id"]
    kps        = state.get("knowledge_points", [])
    exercises  = state.get("selected_exercises", {})

    lesson_id = str(_uuid.uuid4())
    relation_ids: list[str] = []

    async with AsyncSessionLocal() as session:
        async with session.begin():
            # ① 教案主体
            await session.execute(
                text("""
                    INSERT INTO lesson_plans
                        (lesson_id, teacher_id, subject, grade, topic, duration, lesson_content, status, version)
                    VALUES
                        (:lesson_id, :teacher_id, :subject, :grade, :topic, :duration, CAST(:content AS jsonb), 'draft', 1)
                """),
                {
                    "lesson_id":  _uuid.UUID(lesson_id),
                    "teacher_id": _uuid.UUID(teacher_id),
                    "subject":    state["subject"],
                    "grade":      state["grade"],
                    "topic":      state["topic"],
                    "duration":   state.get("duration", 40),
                    "content":    json.dumps(plan, ensure_ascii=False),
                },
            )

            # ② 教案-知识点关联
            for kp in kps:
                rid = str(_uuid.uuid4())
                await session.execute(
                    text("INSERT INTO lesson_knowledge_rel (id, lesson_id, kp_id) VALUES (:id, :lesson_id, :kp_id)"),
                    {"id": _uuid.UUID(rid), "lesson_id": _uuid.UUID(lesson_id), "kp_id": _uuid.UUID(kp["kp_id"])},
                )
                relation_ids.append(rid)

            # ③ 教案-习题关联（ON CONFLICT DO NOTHING 兜底：同一题重复关联时跳过）
            for usage, usage_type in [("class_exercises", "class"), ("homework_exercises", "homework")]:
                for ex in exercises.get(usage, []):
                    rid = str(_uuid.uuid4())
                    await session.execute(
                        text("INSERT INTO lesson_exercise_rel (id, lesson_id, exercise_id, usage_type) "
                             "VALUES (:id, :lesson_id, :exercise_id, :usage_type) ON CONFLICT DO NOTHING"),
                        {"id": _uuid.UUID(rid), "lesson_id": _uuid.UUID(lesson_id),
                         "exercise_id": _uuid.UUID(ex["exercise_id"]), "usage_type": usage_type},
                    )
                    relation_ids.append(rid)

            # ④ 操作日志
            await session.execute(
                text("INSERT INTO teacher_lesson_history (teacher_id, lesson_id, action) VALUES (:teacher_id, :lesson_id, 'create')"),
                {"teacher_id": _uuid.UUID(teacher_id), "lesson_id": _uuid.UUID(lesson_id)},
            )

    logger.info("save_lesson_plan.done", lesson_id=lesson_id, relations=len(relation_ids))
    return {"saved_lesson_id": lesson_id, "saved_relation_ids": relation_ids}
