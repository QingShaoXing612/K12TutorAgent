# scripts/demo_prepare_review.py
# 演示前准备（服务运行时执行）：模拟学生提交一份答卷，AI 批改后停在「待教师复核」状态。
# 这样教师(teacher01)登录后，「试卷批改 → 待复核列表」就能看到一条示例，直接演示复核/发布。
#
# 用法（先起服务，再跑本脚本）：
#   PYTHONUTF8=1 PYTHONPATH=. .venv/Scripts/python.exe scripts/demo_prepare_review.py
#
# 说明：exam 批改的 interrupt 状态存在 MemorySaver（内存），服务重启会丢，
#       所以本脚本必须在服务运行期间跑，每次演示前跑一次即可（幂等：先清旧提交再重交）。

import asyncio
import time

import httpx
from sqlalchemy import text

from backend.dependencies import AsyncSessionLocal

BASE_URL = "http://localhost:8000/api/v1"
EXAM_ID = "b3a2c1d0-0000-4000-8000-000000000001"   # 小学数学演示卷
DOCX_PATH = "scripts/manual_tests/math_student_answer.docx"
STUDENT_UN = "student01"
STUDENT_PW = "Student@123456"


def login(username: str, password: str) -> str:
    r = httpx.post(f"{BASE_URL}/auth/login", json={"username": username, "password": password}, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]


async def cleanup_old_submission() -> None:
    """幂等：清掉 student01 对该卷的旧提交，避免 409「已有待确认/已发布结果」。"""
    async with AsyncSessionLocal() as s:
        await s.execute(
            text(
                "DELETE FROM exam_reviews WHERE submission_id IN "
                "(SELECT id FROM exam_submissions WHERE exam_id=:e "
                " AND student_id=(SELECT id FROM users WHERE username='student01'))"
            ),
            {"e": EXAM_ID},
        )
        await s.execute(
            text(
                "DELETE FROM exam_submissions WHERE exam_id=:e "
                "AND student_id=(SELECT id FROM users WHERE username='student01')"
            ),
            {"e": EXAM_ID},
        )
        await s.commit()


def main() -> None:
    asyncio.run(cleanup_old_submission())

    student_token = login(STUDENT_UN, STUDENT_PW)

    with open(DOCX_PATH, "rb") as f:
        r = httpx.post(
            f"{BASE_URL}/exam/submit",
            headers={"Authorization": f"Bearer {student_token}"},
            data={"exam_id": EXAM_ID},
            files={"file": ("math_student_answer.docx", f, "application/octet-stream")},
            timeout=30.0,
        )
    r.raise_for_status()
    submission_id = r.json()["submission_id"]
    print(f"✅ 学生已提交答卷，submission_id: {submission_id[:8]}...")

    print("   AI 批改中（客观题判分 + 简答题 LLM 评分）...")
    for attempt in range(40):
        time.sleep(3)
        r = httpx.get(
            f"{BASE_URL}/exam/my-submissions/{submission_id}",
            headers={"Authorization": f"Bearer {student_token}"},
            timeout=15,
        )
        r.raise_for_status()
        status = r.json()["status"]
        if status == "pending_review":
            print(f"✅ 批改完成，已停在「待教师复核」状态")
            print()
            print("演示路径：")
            print("  teacher01 / Teacher@123456 登录 → 试卷批改 → 待复核列表")
            print("  → 点进看 AI 预批改 → 「通过并发布」 → 学生端即可看到结果")
            return
        if status == "published":
            print("⚠️ 该提交已发布（可能被其他流程发布），请重新运行本脚本")
            return
    print("⚠️ 批改超时，请检查后端日志")


if __name__ == "__main__":
    main()
