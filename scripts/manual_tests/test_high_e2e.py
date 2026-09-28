# scripts/manual_tests/test_high_e2e.py
# 高中链路端到端测试（需先起服务：python -m backend.main）
# 覆盖：① 高一数学备课（依赖知识点+语料+习题）② 高二数学问答（RAG 命中高中语料）

import sys
import httpx

sys.path.insert(0, ".")

BASE_URL = "http://localhost:8000/api/v1"


def login(username: str, password: str) -> str:
    resp = httpx.post(
        f"{BASE_URL}/auth/login",
        json={"username": username, "password": password},
        trust_env=False,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def section(t: str):
    print(f"\n{'='*60}\n  {t}\n{'='*60}")


if __name__ == "__main__":
    # ── ① 教师登录 ────────────────────────────────────────────
    section("① 教师登录")
    t_token = login("teacher01", "Teacher@123456")
    print("teacher01 登录成功")

    # ── ② 高一数学备课（跑完整 10 节点图，约 60-90 秒）────────
    section("② 高一数学备课（函数的概念与性质）")
    resp = httpx.post(
        f"{BASE_URL}/lesson-prep/generate",
        headers={"Authorization": f"Bearer {t_token}"},
        json={
            "session_id": "e2e-high-prep-001",
            "subject": "数学", "grade": "高一",
            "topic": "函数的概念与性质",
            "lesson_type": "new", "duration": 45,
            "teacher_requirement": "重点讲清单调性与奇偶性的定义",
        },
        timeout=180.0, trust_env=False,
    )
    resp.raise_for_status()
    plan = resp.json()["final_lesson_plan"]
    print(f"教案 topic: {plan.get('meta', {}).get('topic')}")
    print(f"教学目标维度: {list(plan.get('objectives', {}).keys())}")
    print(f"板书主板书: {plan.get('board', {}).get('main', '')[:50]}...")

    # ── ③ 高二数学问答（验证 RAG 命中高中语料）────────────────
    section("③ 高二数学问答（椭圆离心率）")
    s_token = login("student01", "Student@123456")
    resp = httpx.post(
        f"{BASE_URL}/qa/chat",
        headers={"Authorization": f"Bearer {s_token}"},
        json={
            "session_id": "e2e-high-qa-001",
            "message": "椭圆的离心率怎么计算",
            "enable_web_search": False,
        },
        timeout=120.0, trust_env=False,
    )
    resp.raise_for_status()
    res = resp.json()
    print(f"answer_mode: {res.get('answer_mode')}")
    print(f"confidence : {res.get('confidence')}")
    print(f"sources    : {res.get('sources')}")
    print(f"answer     : {res.get('answer', '')[:200]}...")

    section("高中端到端测试完成 ✅")
