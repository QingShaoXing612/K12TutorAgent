# backend/api/v1/homework.py
# 作业模块：独立 REST CRUD（不进备课/学情 agent），学生真实作答回流 student_practice_records。

import json
import uuid as _uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from backend.dependencies import get_current_user, AsyncSessionLocal
from backend.core.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)


# ── 请求模型 ──────────────────────────────────────────────────

class CreateHomeworkRequest(BaseModel):
    class_id:     str = Field(..., description="班级 ID（裸 UUID）")
    subject:      str = Field(..., min_length=1, max_length=64)
    grade:        str = Field(..., min_length=1, max_length=32)
    title:        str = Field(..., min_length=1, max_length=128)
    exercise_ids: list[str] = Field(..., min_length=1, description="习题 ID 列表")


class SubmitRequest(BaseModel):
    answers: list[dict] = Field(..., min_length=1, description="作答 [{exercise_id, answer}]")


# ── 工具函数 ──────────────────────────────────────────────────

def _to_uuid(v):
    try:
        return _uuid.UUID(str(v)) if v else None
    except (ValueError, TypeError, AttributeError):
        return None


def _normalize_answer(a: str) -> str:
    """客观题对答案归一化（对齐 exam 的 _normalize_answer：大写/去空格/去中英逗号）。"""
    return (a or "").upper().replace(" ", "").replace("，", "").replace(",", "").strip()


def _maybe_load(v):
    """asyncpg 对 jsonb 列可能返回 JSON 字符串，统一转 dict/list。"""
    return json.loads(v) if isinstance(v, str) else v


# ── 端点 ──────────────────────────────────────────────────────

@router.post("/homework", status_code=status.HTTP_201_CREATED)
async def create_homework(
    req: CreateHomeworkRequest,
    current_user: dict = Depends(get_current_user),
):
    """教师布置作业：选习题 + 指定班级。"""
    if current_user["role"] not in ("teacher", "admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅教师可布置作业")

    class_id = _to_uuid(req.class_id)
    ex_ids = [_to_uuid(e) for e in req.exercise_ids]

    async with AsyncSessionLocal() as session:
        rows = await session.execute(text(
            "SELECT exercise_id FROM exercise_bank WHERE exercise_id = ANY(:ids)"
        ), {"ids": ex_ids})
        found = {str(r.exercise_id) for r in rows}
        missing = [e for e in req.exercise_ids if e not in found]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"习题不存在: {missing}",
            )

        result = await session.execute(text("""
            INSERT INTO assignments (tenant_id, teacher_id, class_id, subject, grade, title)
            VALUES (:tenant, :teacher, :class_id, :subject, :grade, :title)
            RETURNING id
        """), {
            "tenant": current_user["tenant_id"],
            "teacher": _to_uuid(current_user["user_id"]),
            "class_id": class_id,
            "subject": req.subject,
            "grade": req.grade,
            "title": req.title,
        })
        assignment_id = str(result.scalar())

        for seq, ex in enumerate(ex_ids, 1):
            await session.execute(text("""
                INSERT INTO assignment_exercises (assignment_id, exercise_id, seq)
                VALUES (:aid, :eid, :seq)
            """), {"aid": _to_uuid(assignment_id), "eid": ex, "seq": seq})

        await session.commit()

    logger.info("homework.created", assignment_id=assignment_id, exercises=len(ex_ids))
    return {"assignment_id": assignment_id, "exercise_count": len(ex_ids)}


@router.get("/homework/{assignment_id}")
async def get_homework(
    assignment_id: str,
    current_user: dict = Depends(get_current_user),
):
    """查看作业详情（不含答案；学生若已提交，附上该生的历史作答与对错）。"""
    aid = _to_uuid(assignment_id)
    async with AsyncSessionLocal() as session:
        hw = (await session.execute(text(
            "SELECT id, class_id, subject, grade, title, status FROM assignments WHERE id = :id"
        ), {"id": aid})).first()
        if not hw:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="作业不存在")

        ex_rows = await session.execute(text("""
            SELECT e.exercise_id, e.question_type, e.content, e.options, e.score, e.answer
            FROM assignment_exercises ae
            JOIN exercise_bank e ON ae.exercise_id = e.exercise_id
            WHERE ae.assignment_id = :id
            ORDER BY ae.seq
        """), {"id": aid})

        # 学生历史提交（答案 + 对错 + 得分）
        submitted_map = {}
        if current_user["role"] == "student":
            spr_rows = await session.execute(text("""
                SELECT exercise_id, answer, is_correct, score
                FROM student_practice_records
                WHERE assignment_id = :aid AND student_id = :sid
            """), {"aid": aid, "sid": current_user["user_id"]})
            submitted_map = {str(r.exercise_id): r for r in spr_rows}

    exercises = []
    for r in ex_rows:
        ex = {
            "exercise_id": str(r.exercise_id),
            "question_type": r.question_type,
            "content": r.content,
            "options": _maybe_load(r.options),
            "score": r.score,
        }
        spr = submitted_map.get(str(r.exercise_id))
        if spr:
            ex["student_answer"] = spr.answer or ""
            ex["is_correct"] = spr.is_correct
            ex["student_score"] = spr.score
            ex["correct_answer"] = r.answer or ""  # 已提交后附上正确答案
        exercises.append(ex)

    return {
        "assignment_id": str(hw.id),
        "class_id": str(hw.class_id) if hw.class_id else None,
        "subject": hw.subject,
        "grade": hw.grade,
        "title": hw.title,
        "status": hw.status,
        "exercises": exercises,
    }


@router.post("/homework/{assignment_id}/submit")
async def submit_homework(
    assignment_id: str,
    req: SubmitRequest,
    current_user: dict = Depends(get_current_user),
):
    """学生提交作答：自动判分（对答案），回流 student_practice_records（幂等，重交覆盖）。"""
    if current_user["role"] != "student":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅学生可提交作业")

    aid = _to_uuid(assignment_id)
    student_id = _to_uuid(current_user["user_id"])

    async with AsyncSessionLocal() as session:
        hw = (await session.execute(text(
            "SELECT subject, grade FROM assignments WHERE id = :id"
        ), {"id": aid})).first()
        if not hw:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="作业不存在")

        ex_rows = await session.execute(text("""
            SELECT e.exercise_id, e.answer, e.score, e.kp_id, e.knowledge_tag
            FROM assignment_exercises ae
            JOIN exercise_bank e ON ae.exercise_id = e.exercise_id
            WHERE ae.assignment_id = :id
        """), {"id": aid})
        ex_map = {str(r.exercise_id): r for r in ex_rows}

        # 幂等：先删该学生该作业的旧作答，再插入
        await session.execute(text(
            "DELETE FROM student_practice_records WHERE student_id = :sid AND assignment_id = :aid"
        ), {"sid": student_id, "aid": aid})

        results = []
        for ans in req.answers:
            eid = _to_uuid(ans.get("exercise_id"))
            if str(eid) not in ex_map:
                continue  # 忽略不属于本作业的题
            ex = ex_map[str(eid)]
            student_ans = ans.get("answer", "")
            is_correct = _normalize_answer(student_ans) == _normalize_answer(ex.answer)
            score = ex.score if is_correct else 0

            await session.execute(text("""
                INSERT INTO student_practice_records (
                    tenant_id, student_id, exercise_id, assignment_id, kp_id, knowledge_tag,
                    subject, grade, source, answer, is_correct, score
                ) VALUES (
                    :tenant, :sid, :eid, :aid, :kp, :tag, :subject, :grade,
                    'homework', :answer, :correct, :score
                )
            """), {
                "tenant": current_user["tenant_id"],
                "sid": student_id,
                "eid": eid,
                "aid": aid,
                "kp": ex.kp_id,
                "tag": ex.knowledge_tag,
                "subject": hw.subject,
                "grade": hw.grade,
                "answer": student_ans,
                "correct": is_correct,
                "score": score,
            })
            results.append({"exercise_id": str(eid), "is_correct": is_correct, "score": score})

        await session.commit()

    total = sum(r["score"] for r in results)
    full = sum(ex_map[r["exercise_id"]].score for r in results)
    logger.info("homework.submitted", assignment_id=assignment_id,
                student=str(student_id)[:8], score=f"{total}/{full}")
    return {
        "assignment_id": assignment_id,
        "results": results,
        "score": total,
        "full_score": full,
    }


@router.get("/homework")
async def list_homework(current_user: dict = Depends(get_current_user)):
    """作业列表：教师看自己布置的，学生看自己班级的。"""
    uid = _to_uuid(current_user["user_id"])
    role = current_user["role"]

    async with AsyncSessionLocal() as session:
        if role in ("teacher", "admin"):
            rows = await session.execute(text("""
                SELECT a.id, a.title, a.subject, a.grade, a.status, a.created_at,
                       (SELECT count(*) FROM assignment_exercises ae WHERE ae.assignment_id = a.id) AS exercise_count,
                       FALSE AS submitted
                FROM assignments a
                WHERE a.teacher_id = :uid
                ORDER BY a.created_at DESC
            """), {"uid": uid})
        else:
            # 学生：先查自己的 class_id，再查该班级的作业
            cid = (await session.execute(text(
                "SELECT class_id FROM users WHERE id = :uid"
            ), {"uid": uid})).scalar()
            if not cid:
                return {"items": []}
            rows = await session.execute(text("""
                SELECT a.id, a.title, a.subject, a.grade, a.status, a.created_at,
                       (SELECT count(*) FROM assignment_exercises ae WHERE ae.assignment_id = a.id) AS exercise_count,
                       EXISTS(SELECT 1 FROM student_practice_records spr
                              WHERE spr.assignment_id = a.id AND spr.student_id = :uid) AS submitted
                FROM assignments a
                WHERE a.class_id = :cid
                ORDER BY a.created_at DESC
            """), {"cid": cid, "uid": uid})

        items = [
            {
                "assignment_id": str(r.id),
                "title": r.title,
                "subject": r.subject,
                "grade": r.grade,
                "status": r.status,
                "exercise_count": r.exercise_count,
                "submitted": bool(r.submitted),
                "created_at": str(r.created_at),
            }
            for r in rows
        ]

    return {"items": items}


@router.delete("/homework/{assignment_id}")
async def delete_homework(
    assignment_id: str,
    current_user: dict = Depends(get_current_user),
):
    """教师删除自己布置的作业（作答记录 / 选题关联一并清理）。"""
    if current_user["role"] not in ("teacher", "admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅教师可删除作业")

    aid = _to_uuid(assignment_id)
    async with AsyncSessionLocal() as session:
        async with session.begin():
            # 作答记录无外键 CASCADE，先手动删
            await session.execute(text(
                "DELETE FROM student_practice_records WHERE assignment_id = :id"
            ), {"id": aid})
            # 删作业（assignment_exercises 由 ON DELETE CASCADE 连带删除）
            result = await session.execute(text(
                "DELETE FROM assignments WHERE id = :id AND teacher_id = :tid RETURNING id"
            ), {"id": aid, "tid": _to_uuid(current_user["user_id"])})
            deleted = result.fetchone()

    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="作业不存在")
    return {"assignment_id": assignment_id, "deleted": True}


@router.get("/classes")
async def list_classes(
    grade: str = "",
    current_user: dict = Depends(get_current_user),
):
    """班级列表（布置作业/学情报告下拉用），可选按年级过滤，含学生数。"""
    sql = """
        SELECT c.id, c.name, c.grade,
               (SELECT count(*) FROM users u WHERE u.class_id = c.id AND u.role = 'student') AS student_count
        FROM classes c
        WHERE c.tenant_id = :tid
    """
    params: dict = {"tid": current_user["tenant_id"]}
    if grade:
        sql += " AND c.grade = :grade"
        params["grade"] = grade
    sql += " ORDER BY c.grade, c.name"
    async with AsyncSessionLocal() as session:
        rows = await session.execute(text(sql), params)
    return {"items": [
        {"class_id": str(r.id), "name": r.name, "grade": r.grade, "student_count": r.student_count or 0}
        for r in rows
    ]}


@router.get("/exercises")
async def list_exercises(
    subject: Optional[str] = None,
    grade: Optional[str] = None,
    kp_ids: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """习题库（教师选题用，不含答案）。可按 subject/grade 或知识点（kp_ids 逗号分隔）过滤。"""
    sql = """
        SELECT exercise_id, question_type, content, options, score, knowledge_tag, difficulty
        FROM exercise_bank
        WHERE quality_status = 'checked'
    """
    params = {}
    if subject:
        sql += " AND subject = :subject"
        params["subject"] = subject
    if grade:
        sql += " AND grade = :grade"
        params["grade"] = grade
    if kp_ids:
        kp_list = [_to_uuid(k) for k in kp_ids.split(",") if k.strip()]
        kp_list = [k for k in kp_list if k]
        if kp_list:
            sql += " AND kp_id = ANY(:kps)"
            params["kps"] = kp_list
    sql += " ORDER BY created_at"

    async with AsyncSessionLocal() as session:
        rows = await session.execute(text(sql), params)

    return {"items": [
        {
            "exercise_id": str(r.exercise_id),
            "question_type": r.question_type,
            "content": r.content,
            "options": _maybe_load(r.options),
            "score": r.score,
            "knowledge_tag": r.knowledge_tag,
            "difficulty": r.difficulty,
        }
        for r in rows
    ]}
