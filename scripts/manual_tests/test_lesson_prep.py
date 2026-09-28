# scripts/manual_tests/test_lesson_prep.py
# 备课生产 agent HTTP 端到端测试（需先起服务：python -m backend.main）

import sys
import httpx

sys.path.insert(0, ".")

BASE_URL    = "http://localhost:8000/api/v1"
TEACHER_UN  = "teacher01"
TEACHER_PW  = "Teacher@123456"


def login(username: str, password: str) -> str:
    resp = httpx.post(
        f"{BASE_URL}/auth/login",
        json={"username": username, "password": password},
        trust_env=False,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print("="*60)


if __name__ == "__main__":
    # ── ① 教师登录 ────────────────────────────────────────────
    print_section("① 教师登录")
    token = login(TEACHER_UN, TEACHER_PW)
    print(f"教师 token 获取成功：{token[:20]}...")

    session_id = "e2e-lesson-prep-001"

    # ── ② 生成教案（完整 10 节点图，约 60-90 秒）──────────────
    print_section("② 生成教案（跑完整图，请等待）")
    resp = httpx.post(
        f"{BASE_URL}/lesson-prep/generate",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "session_id": session_id,
            "subject": "数学", "grade": "六年级",
            "topic": "圆的周长与面积",
            "lesson_type": "new", "duration": 40,
            "teacher_requirement": "重点讲解周长公式推导过程",
        },
        timeout=180.0, trust_env=False,
    )
    resp.raise_for_status()
    plan = resp.json()["final_lesson_plan"]
    print(f"教案 topic: {plan.get('meta', {}).get('topic')}")
    print(f"教学目标维度: {list(plan.get('objectives', {}).keys())}")
    print(f"板书主板书: {plan.get('board', {}).get('main', '')[:30]}...")

    # ── ③ GET 读教案 ──────────────────────────────────────────
    print_section("③ GET 读取暂停状态")
    resp = httpx.get(
        f"{BASE_URL}/lesson-prep/sessions/{session_id}/plan",
        headers={"Authorization": f"Bearer {token}"},
        trust_env=False,
    )
    resp.raise_for_status()
    print(f"GET 读取成功，revision_target: {resp.json().get('revision_target', '')!r}")

    # ── ④ confirm 提意见（回炉改教学目标）──────────────────────
    print_section("④ confirm 提意见（回炉改目标）")
    resp = httpx.post(
        f"{BASE_URL}/lesson-prep/confirm",
        headers={"Authorization": f"Bearer {token}"},
        json={"session_id": session_id, "feedback": "教学目标不够突出算理，请重写"},
        timeout=180.0, trust_env=False,
    )
    resp.raise_for_status()
    r = resp.json()
    print(f"confirm 状态: {r.get('status')}  revision_target: {r.get('revision_target', '')!r}")

    # ── ⑤ confirm 满意（save 落库）────────────────────────────
    print_section("⑤ confirm 满意（落库）")
    resp = httpx.post(
        f"{BASE_URL}/lesson-prep/confirm",
        headers={"Authorization": f"Bearer {token}"},
        json={"session_id": session_id, "feedback": ""},
        timeout=60.0, trust_env=False,
    )
    resp.raise_for_status()
    r = resp.json()
    print(f"状态: {r.get('status')}  saved_lesson_id: {r.get('saved_lesson_id', '')[:8]}...")

    print_section("端到端测试全部通过 ✅")
