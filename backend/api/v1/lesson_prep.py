# backend/api/v1/lesson_prep.py

import json
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage
from langgraph.types import Command
from sqlalchemy import text
from sse_starlette.sse import EventSourceResponse

from backend.agents.lesson_prep.graph import build_lesson_prep_graph
from backend.core.memory import build_config
from backend.dependencies import get_current_user, AsyncSessionLocal
from backend.core.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)

# 懒加载编译图：checkpointer 在应用启动（lifespan）后才初始化
_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        _graph = build_lesson_prep_graph()
    return _graph

LESSON_TYPES = {"new", "exercise", "review"}   # 新授课 / 习题课 / 复习课


# ── 请求 / 响应模型 ───────────────────────────────────────────

class GenerateRequest(BaseModel):
    session_id:         str = Field(..., description="会话 ID")
    subject:            str = Field(..., min_length=1, max_length=64, description="科目")
    grade:              str = Field(..., min_length=1, max_length=32, description="年级")
    topic:              str = Field(..., min_length=1, max_length=128, description="章节/知识点主题")
    lesson_type:        str = Field("new", description="课型 new/exercise/review")
    duration:           int = Field(40, ge=1, le=180, description="课时（分钟）")
    teacher_requirement: str = Field("", max_length=512, description="教师补充需求（自由文本）")


class ConfirmRequest(BaseModel):
    session_id: str = Field(..., description="会话 ID")
    feedback:   str = Field("", max_length=1024, description="修改意见；空字符串 = 满意")


# ── 端点 ──────────────────────────────────────────────────────

@router.post("/generate")
async def generate(
    req: GenerateRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    触发备课生成：跑图走到反思优化 interrupt 暂停，返回完整教案供教师审阅。
    """
    if req.lesson_type not in LESSON_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"不支持的课型：{req.lesson_type}，可选 {sorted(LESSON_TYPES)}",
        )

    initial_state = {
        "messages":           [HumanMessage(content=f"备课：{req.subject} {req.grade} {req.topic}")],
        "teacher_id":         current_user["user_id"],
        "tenant_id":          current_user["tenant_id"],
        "session_id":         req.session_id,
        "course_id":          None,
        "subject":            req.subject,
        "grade":              req.grade,
        "topic":              req.topic,
        "lesson_type":        req.lesson_type,
        "duration":           req.duration,
        "teacher_requirement": req.teacher_requirement,
        "knowledge_points":   [],
        "retrieval_queries":  [],
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
    config = build_config(current_user["user_id"], req.session_id)

    try:
        result = await _get_graph().ainvoke(initial_state, config=config)
    except Exception as e:
        logger.error("lesson_prep.generate_error", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "AGENT_ERROR", "message": str(e)},
        )

    return {
        "session_id":        req.session_id,
        "final_lesson_plan": result.get("final_lesson_plan", {}),
        "status":            "pending_review",
    }


# ── 流式生成：SSE 推送节点进度 + 最终教案 ─────────────────────

_PROGRESS_LABELS = {
    "requirement_parse":     "解析需求",
    "semantic_retrieve":     "检索相关资源",
    "objective_generate":    "生成教学目标",
    "key_point_analyze":     "分析重点难点",
    "process_generate":      "设计教学流程",
    "exercise_match":        "匹配习题",
    "quality_check":         "习题质检",
    "lesson_plan_integrate": "整合教案",
    "reflection_optimize":   "反思优化",
    "save_lesson_plan":      "保存教案",
}


def _sse(data: dict) -> dict:
    return {"data": json.dumps(data, ensure_ascii=False)}


@router.post("/generate/stream")
async def generate_stream(
    req: GenerateRequest,
    current_user: dict = Depends(get_current_user),
):
    """备课流式生成（SSE）：推送节点进度 + 教案（反思优化 interrupt 暂停）。"""
    if req.lesson_type not in LESSON_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"不支持的课型：{req.lesson_type}，可选 {sorted(LESSON_TYPES)}",
        )

    initial_state = {
        "messages":           [HumanMessage(content=f"备课：{req.subject} {req.grade} {req.topic}")],
        "teacher_id":         current_user["user_id"],
        "tenant_id":          current_user["tenant_id"],
        "session_id":         req.session_id,
        "course_id":          None,
        "subject":            req.subject,
        "grade":              req.grade,
        "topic":              req.topic,
        "lesson_type":        req.lesson_type,
        "duration":           req.duration,
        "teacher_requirement": req.teacher_requirement,
        "knowledge_points":   [],
        "retrieval_queries":  [],
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
                "type": "plan",
                "final_lesson_plan": result.get("final_lesson_plan", {}),
                "status": "pending_review",
            })
            yield _sse({"type": "done"})
        except Exception as e:
            logger.error("lesson_prep.stream_error", error=str(e), exc_info=True)
            yield _sse({"type": "error", "message": str(e)})

    return EventSourceResponse(event_generator())


@router.get("/sessions/{session_id}/plan")
async def get_lesson_plan(
    session_id: str,
    current_user: dict = Depends(get_current_user),
):
    """教师读取暂停时（interrupt）保存的教案状态。"""
    config = build_config(current_user["user_id"], session_id)
    try:
        snapshot = await _get_graph().aget_state(config)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"找不到备课状态：{e}")

    if not snapshot or not snapshot.values:
        raise HTTPException(status_code=404, detail="备课尚未完成或记录不存在")

    return {
        "session_id":        session_id,
        "final_lesson_plan": snapshot.values.get("final_lesson_plan", {}),
        "revision_target":   snapshot.values.get("revision_target", ""),
    }


@router.post("/confirm")
async def confirm(
    req: ConfirmRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    教师审阅后确认：feedback 为空 = 满意（进 save 落库）；非空 = 提意见（回炉重跑）。
    """
    config = build_config(current_user["user_id"], req.session_id)

    try:
        graph = _get_graph()
        # 先确认会话状态存在且含 subject：session_id 丢失/过期时，Command(resume) 会让
        # graph 从 requirement_parse_node 从头跑，state["subject"] 抛 KeyError。这里提前拦截。
        snapshot = await graph.aget_state(config)
        if not snapshot or not snapshot.values or "subject" not in snapshot.values:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="备课会话不存在或已过期，请重新生成教案",
            )
        result = await graph.ainvoke(Command(resume=req.feedback), config=config)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("lesson_prep.confirm_error", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "AGENT_ERROR", "message": str(e)},
        )

    # 满意 → save 落库完成
    if result.get("saved_lesson_id"):
        return {
            "session_id":      req.session_id,
            "status":          "saved",
            "saved_lesson_id": result["saved_lesson_id"],
        }

    # 提意见 → 回炉重跑后再次暂停，返回新教案
    return {
        "session_id":        req.session_id,
        "status":            "pending_review",
        "final_lesson_plan": result.get("final_lesson_plan", {}),
        "revision_target":   result.get("revision_target", ""),
    }


def _maybe_load(v):
    """asyncpg 对 jsonb 列可能返回 JSON 字符串，统一转 dict/list。"""
    return json.loads(v) if isinstance(v, str) else v


def _to_uuid(v):
    try:
        return _uuid.UUID(str(v)) if v else None
    except (ValueError, TypeError, AttributeError):
        return None


@router.get("/plans")
async def list_my_plans(
    current_user: dict = Depends(get_current_user),
):
    """列出当前教师已保存的教案（按更新时间倒序）。"""
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(text(
            "SELECT lesson_id, subject, grade, topic, duration, status, created_at "
            "FROM lesson_plans WHERE teacher_id = :tid ORDER BY updated_at DESC LIMIT 50"
        ), {"tid": current_user["user_id"]})).mappings().all()

    return {"items": [
        {
            "lesson_id":  str(r["lesson_id"]),
            "subject":    r["subject"],
            "grade":      r["grade"],
            "topic":      r["topic"],
            "duration":   r["duration"],
            "status":     r["status"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]}


@router.get("/plans/{lesson_id}")
async def get_lesson_plan_detail(
    lesson_id: str,
    current_user: dict = Depends(get_current_user),
):
    """查看单个已保存教案的完整内容。"""
    lid = _to_uuid(lesson_id)
    if not lid:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="教案不存在")

    async with AsyncSessionLocal() as session:
        row = (await session.execute(text(
            "SELECT lesson_id, subject, grade, topic, duration, lesson_content, status, created_at "
            "FROM lesson_plans WHERE lesson_id = :id AND teacher_id = :tid"
        ), {"id": lid, "tid": current_user["user_id"]})).mappings().first()

        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="教案不存在")

        kp_rows = await session.execute(text(
            "SELECT kp_id FROM lesson_knowledge_rel WHERE lesson_id = :id"
        ), {"id": lid})
        kp_ids = [str(r[0]) for r in kp_rows]

    return {
        "lesson_id":      str(row["lesson_id"]),
        "subject":        row["subject"],
        "grade":          row["grade"],
        "topic":          row["topic"],
        "duration":       row["duration"],
        "status":         row["status"],
        "created_at":     row["created_at"].isoformat() if row["created_at"] else None,
        "lesson_content": _maybe_load(row["lesson_content"]),
        "kp_ids":         kp_ids,
    }


@router.delete("/plans/{lesson_id}")
async def delete_lesson_plan(
    lesson_id: str,
    current_user: dict = Depends(get_current_user),
):
    """删除已保存的教案（关联表 lesson_knowledge_rel / lesson_exercise_rel / history 由 ON DELETE CASCADE 自动清理）。"""
    lid = _to_uuid(lesson_id)
    if not lid:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="教案不存在")

    async with AsyncSessionLocal() as session:
        async with session.begin():
            # 先解除干预策略对该教案的引用（source_lesson_id 外键无 CASCADE，否则删除会被阻止报外键错误）
            await session.execute(text(
                "UPDATE intervention_strategies SET source_lesson_id = NULL WHERE source_lesson_id = :id"
            ), {"id": lid})
            result = await session.execute(text(
                "DELETE FROM lesson_plans WHERE lesson_id = :id AND teacher_id = :tid RETURNING lesson_id"
            ), {"id": lid, "tid": current_user["user_id"]})
            deleted = result.fetchone()

    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="教案不存在")

    return {"lesson_id": lesson_id, "deleted": True}
