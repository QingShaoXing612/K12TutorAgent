# backend/api/v1/learning_analysis.py

import json
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage
from sqlalchemy import text
from sse_starlette.sse import EventSourceResponse

from backend.agents.learning_analysis.graph import build_learning_analysis_graph
from backend.agents.lesson_prep.graph import build_lesson_prep_graph
from backend.core.memory import build_config
from backend.dependencies import get_current_user, AsyncSessionLocal
from backend.core.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)

# 懒加载编译图：checkpointer 在应用启动（lifespan）后才初始化
_graph = None
_lesson_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        _graph = build_learning_analysis_graph()
    return _graph


def _get_lesson_graph():
    global _lesson_graph
    if _lesson_graph is None:
        _lesson_graph = build_lesson_prep_graph()
    return _lesson_graph


# ── 请求 / 响应模型 ───────────────────────────────────────────

class GenerateRequest(BaseModel):
    session_id:         str = Field(..., description="会话 ID")
    class_id:           str = Field(..., description="班级 ID（裸 UUID）")
    subject:            str = Field(..., min_length=1, max_length=64, description="科目")
    grade:              str = Field(..., min_length=1, max_length=32, description="年级")
    data_source:        str = Field("practice", description="数据源 practice(日常练习)/exam(考试批改)")
    time_range:         dict = Field(default_factory=dict, description="数据时间范围 {start, end}，空=全部历史")
    exam_ids:           list[str] = Field(default_factory=list, description="考试批次 ID 列表（data_source=exam 时用）")
    knowledge_scope:    str = Field("", max_length=128, description="分析知识点范围（自由文本，如「分数乘除」）")
    teacher_requirement: str = Field("", max_length=512, description="教师补充需求（自由文本）")
    report_type:        str = Field("class", description="报告类型 class/student")
    template:           str = Field("primary", description="报告模板 primary/junior/senior")


def _maybe_load(v):
    """asyncpg 对 jsonb 列可能返回 JSON 字符串，统一转 dict/list。"""
    return json.loads(v) if isinstance(v, str) else v


# ── 端点 ──────────────────────────────────────────────────────

@router.post("/generate")
async def generate(
    req: GenerateRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    触发生成班级学情报告：直线跑 10 节点（无 interrupt），质检通过则落库。
    质检不过（validation.passed=False）则不落库，返回 issues 供排查。
    """
    initial_state = {
        "messages":           [HumanMessage(content=f"学情分析：{req.subject} {req.grade}")],
        "teacher_id":         current_user["user_id"],
        "tenant_id":          current_user["tenant_id"],
        "session_id":         req.session_id,
        "subject":            req.subject,
        "grade":              req.grade,
        "class_id":           req.class_id,
        "report_type":        req.report_type,
        "template":           req.template,
        "data_source":        req.data_source,
        "time_range":         req.time_range,
        "exam_ids":           req.exam_ids,
        "knowledge_scope":    req.knowledge_scope,
        "teacher_requirement": req.teacher_requirement,
    }
    config = build_config(current_user["user_id"], req.session_id)

    try:
        result = await _get_graph().ainvoke(initial_state, config=config)
    except Exception as e:
        logger.error("learning_analysis.generate_error", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "AGENT_ERROR", "message": str(e)},
        )

    return {
        "session_id":      req.session_id,
        "saved_report_id": result.get("saved_report_id", ""),
        "validation":      result.get("validation", {}),
        "report_content":  result.get("report_content", {}),
    }


# ── 流式生成：SSE 推送节点进度 + 最终报告 ─────────────────────

_PROGRESS_LABELS = {
    "requirement_parse":      "解析需求",
    "data_fetch":             "拉取学情数据",
    "mastery_calc":           "计算知识点掌握度",
    "typical_error":          "分析典型错题",
    "stratify":               "学生分层画像",
    "intervention_retrieval": "检索干预策略",
    "suggestion_gen":         "生成教学建议",
    "report_integration":     "整合报告",
    "report_validation":      "报告质检",
    "persistence":            "保存报告",
}


def _sse(data: dict) -> dict:
    return {"data": json.dumps(data, ensure_ascii=False)}


@router.post("/generate/stream")
async def generate_stream(
    req: GenerateRequest,
    current_user: dict = Depends(get_current_user),
):
    """学情报告流式生成（SSE）：推送各节点进度 + 最终报告。"""
    initial_state = {
        "messages":           [HumanMessage(content=f"学情分析：{req.subject} {req.grade}")],
        "teacher_id":         current_user["user_id"],
        "tenant_id":          current_user["tenant_id"],
        "session_id":         req.session_id,
        "subject":            req.subject,
        "grade":              req.grade,
        "class_id":           req.class_id,
        "report_type":        req.report_type,
        "template":           req.template,
        "data_source":        req.data_source,
        "time_range":         req.time_range,
        "exam_ids":           req.exam_ids,
        "knowledge_scope":    req.knowledge_scope,
        "teacher_requirement": req.teacher_requirement,
    }
    config = build_config(current_user["user_id"], req.session_id)

    async def event_generator():
        graph = _get_graph()
        yielded = set()
        try:
            async for event in graph.astream_events(initial_state, config=config, version="v2"):
                if event["event"] != "on_chain_start":
                    continue
                node = event.get("metadata", {}).get("langgraph_node", "")
                if node in _PROGRESS_LABELS and node not in yielded:
                    yielded.add(node)
                    yield _sse({"type": "progress", "stage": _PROGRESS_LABELS[node]})
            snapshot = await graph.aget_state(config)
            result = snapshot.values if snapshot else {}
            yield _sse({
                "type": "report",
                "saved_report_id": result.get("saved_report_id", ""),
                "validation": result.get("validation", {}),
                "report_content": result.get("report_content", {}),
            })
            yield _sse({"type": "done"})
        except Exception as e:
            logger.error("learning_analysis.stream_error", error=str(e), exc_info=True)
            yield _sse({"type": "error", "message": str(e)})

    return EventSourceResponse(event_generator())


@router.get("/reports")
async def list_reports(current_user: dict = Depends(get_current_user)):
    """列出当前教师生成的所有学情报告（按时间倒序）。"""
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(text(
            "SELECT id, subject, grade, class_id, report_type, status, created_at "
            "FROM learning_reports WHERE teacher_id = :tid ORDER BY created_at DESC LIMIT 50"
        ), {"tid": current_user["user_id"]})).mappings().all()
    return {"items": [
        {
            "report_id":  str(r["id"]),
            "subject":    r["subject"],
            "grade":      r["grade"],
            "class_id":   str(r["class_id"]) if r["class_id"] else None,
            "report_type": r["report_type"],
            "status":     r["status"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]}


@router.delete("/report/{report_id}")
async def delete_report(report_id: str, current_user: dict = Depends(get_current_user)):
    """删除学情报告（learning_reports 独立表，无关联表）。"""
    try:
        lid = _uuid.UUID(report_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="报告不存在")

    async with AsyncSessionLocal() as session:
        result = await session.execute(text(
            "DELETE FROM learning_reports WHERE id = :id AND teacher_id = :tid RETURNING id"
        ), {"id": lid, "tid": current_user["user_id"]})
        deleted = result.fetchone()
        await session.commit()

    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="报告不存在")

    return {"report_id": report_id, "deleted": True}


@router.get("/exams")
async def list_exams(subject: str = "", current_user: dict = Depends(get_current_user)):
    """列出试卷（考试批次），可选按科目过滤。"""
    async with AsyncSessionLocal() as session:
        if subject:
            rows = (await session.execute(text(
                "SELECT id, title FROM exams WHERE tenant_id = :tid AND subject = :subject ORDER BY created_at DESC"
            ), {"tid": current_user["tenant_id"], "subject": subject})).mappings().all()
        else:
            rows = (await session.execute(text(
                "SELECT id, title FROM exams WHERE tenant_id = :tid ORDER BY created_at DESC"
            ), {"tid": current_user["tenant_id"]})).mappings().all()
    return {"items": [{"exam_id": str(r["id"]), "title": r["title"]} for r in rows]}


class CheckDataRequest(BaseModel):
    class_id:    str
    subject:     str
    grade:       str
    data_source: str = "practice"
    time_range:  dict = Field(default_factory=dict)
    exam_ids:    list[str] = Field(default_factory=list)


@router.post("/check-data")
async def check_data(req: CheckDataRequest):
    """检查所选数据源是否有作答记录（前端据此置灰「生成报告」按钮 + 提示无数据）。"""
    from datetime import datetime, timezone
    def _dt(v, default):
        if not v:
            return default
        if isinstance(v, str):
            return datetime.fromisoformat(v).replace(tzinfo=timezone.utc)
        return v
    start = _dt(req.time_range.get("start"), datetime(1970, 1, 1, tzinfo=timezone.utc))
    end = _dt(req.time_range.get("end"), datetime(9999, 12, 31, tzinfo=timezone.utc))

    async with AsyncSessionLocal() as session:
        if req.data_source == "exam":
            if req.exam_ids:
                count = (await session.execute(text("""
                    SELECT count(*) FROM exam_reviews r
                    JOIN exam_submissions s ON r.submission_id = s.id
                    JOIN exams e ON s.exam_id = e.id
                    WHERE s.student_id IN (SELECT id FROM users WHERE class_id = :c AND role='student')
                      AND r.final_score IS NOT NULL AND s.status IN ('reviewed','published')
                      AND e.subject = :subject
                      AND s.exam_id = ANY(:eids)
                """), {"c": req.class_id, "subject": req.subject, "eids": [_uuid.UUID(x) for x in req.exam_ids]})).scalar()
            else:
                count = (await session.execute(text("""
                    SELECT count(*) FROM exam_reviews r
                    JOIN exam_submissions s ON r.submission_id = s.id
                    JOIN exams e ON s.exam_id = e.id
                    WHERE s.student_id IN (SELECT id FROM users WHERE class_id = :c AND role='student')
                      AND r.final_score IS NOT NULL AND s.status IN ('reviewed','published')
                      AND e.subject = :subject
                """), {"c": req.class_id, "subject": req.subject})).scalar()
        else:
            count = (await session.execute(text("""
                SELECT count(*) FROM student_practice_records r
                JOIN users u ON r.student_id = u.id
                WHERE u.class_id = :c AND u.role='student'
                  AND r.subject = :subject AND r.grade = :grade
                  AND r.recorded_at >= :start
                  AND r.recorded_at <= :end
            """), {"c": req.class_id, "subject": req.subject, "grade": req.grade,
                   "start": start, "end": end})).scalar()

    return {"has_data": (count or 0) > 0, "count": count or 0}


@router.get("/report/{report_id}")
async def get_report(
    report_id: str,
    current_user: dict = Depends(get_current_user),
):
    """读取已落库的学情报告（learning_reports 表）。"""
    async with AsyncSessionLocal() as session:
        row = (await session.execute(text(
            "SELECT scope, metrics, report_content, status "
            "FROM learning_reports WHERE id = :id"
        ), {"id": report_id})).first()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="报告不存在")

    return {
        "report_id":      report_id,
        "status":         row.status,
        "scope":          _maybe_load(row.scope),
        "metrics":        _maybe_load(row.metrics),
        "report_content": _maybe_load(row.report_content),
    }


class GenerateLessonRequest(BaseModel):
    kp_ids:      list[str] = Field(default_factory=list, description="要备课的薄弱知识点 ID 列表；空 = 用掌握度最低的一个")
    lesson_type: str = Field("review", description="课型 new/exercise/review")


@router.post("/report/{report_id}/generate-lesson")
async def generate_lesson_from_report(
    report_id: str,
    req: GenerateLessonRequest,
    current_user: dict = Depends(get_current_user),
):
    """seam① 一键备课：学情报告的薄弱知识点 → 预填 lesson_prep 生成针对性教案。"""
    async with AsyncSessionLocal() as session:
        row = (await session.execute(text(
            "SELECT subject, grade, report_content FROM learning_reports WHERE id = :id"
        ), {"id": report_id})).first()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="报告不存在")

    report_content = _maybe_load(row.report_content)
    weak_kps = report_content.get("suggestions", {}).get("weak_kps", [])
    if not weak_kps:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="报告无薄弱知识点，无需备课",
        )

    # 选择要备课的知识点：指定 kp_ids 则按勾选顺序取，否则取最薄弱的一个
    if req.kp_ids:
        weak_map = {w.get("kp_id"): w for w in weak_kps}
        missing = [k for k in req.kp_ids if k not in weak_map]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"指定的薄弱知识点不在报告中: {missing}",
            )
        targets = [weak_map[k] for k in req.kp_ids]
    else:
        targets = [weak_kps[0]]  # 节点7 已按 mastery_score 升序，最薄弱在前

    kp_names = [t["kp_name"] for t in targets]
    topic = "、".join(kp_names)  # 多知识点合成一个备课主题 → 一份教案

    kp_desc = "；".join(
        f"「{t['kp_name']}」掌握度 {t['mastery_score']}"
        + (f"（{t.get('suggestion', '')}）" if t.get("suggestion") else "")
        for t in targets
    )
    teacher_requirement = (
        f"学情报告显示以下知识点掌握薄弱：{kp_desc}。"
        f"请围绕这些知识点设计一节针对性复习课。"
    )

    initial_state = {
        "messages":            [HumanMessage(content=f"一键备课：{topic}")],
        "teacher_id":          current_user["user_id"],
        "tenant_id":           current_user["tenant_id"],
        "session_id":          f"analysis-lesson-{report_id[:8]}",
        "course_id":           None,
        "subject":             row.subject,
        "grade":               row.grade,
        "topic":               topic,
        "lesson_type":         req.lesson_type,
        "duration":            40,
        "teacher_requirement": teacher_requirement,
        "knowledge_points":    [],
        "retrieval_queries":   [],
        "retrieved_resources": {},
        "exercise_candidates": [],
        "teaching_objectives": {},
        "key_difficult_points": {},
        "teaching_process":    {},
        "selected_exercises":  {},
        "quality_reports":     [],
        "reselect_count":      0,
        "final_lesson_plan":   {},
        "teacher_feedback":    "",
        "revision_target":     "",
        "saved_lesson_id":     "",
        "saved_relation_ids":  [],
    }
    config = build_config(current_user["user_id"], initial_state["session_id"])

    try:
        result = await _get_lesson_graph().ainvoke(initial_state, config=config)
    except Exception as e:
        logger.error("learning_analysis.generate_lesson_error", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "AGENT_ERROR", "message": str(e)},
        )

    return {
        "report_id":           report_id,
        "kp_ids":              [t["kp_id"] for t in targets],
        "kp_names":            kp_names,
        "topic":               topic,
        "teacher_requirement": teacher_requirement,
        "final_lesson_plan":   result.get("final_lesson_plan", {}),
    }
