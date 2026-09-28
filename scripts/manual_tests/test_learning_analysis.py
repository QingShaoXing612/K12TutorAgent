# scripts/manual_tests/test_learning_analysis.py
# 学情分析 agent HTTP 端到端测试（需先起服务：PYTHONUTF8=1 .venv/Scripts/python.exe -m backend.main）
# 依赖 seed_learning_data.py 已灌数据（班级 c1a55e00 + 16 practice + 6 策略）

import sys
import httpx

sys.path.insert(0, ".")

BASE_URL    = "http://localhost:8000/api/v1"
TEACHER_UN  = "teacher01"
TEACHER_PW  = "Teacher@123456"
CLASS_ID    = "c1a55e00-0000-4000-8000-000000000001"


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

    session_id = "e2e-learning-analysis-001"

    # ── ② 生成学情报告（10 节点图，含 2 次 LLM，约 60-90 秒）───
    print_section("② 生成学情报告（跑完整图，请等待）")
    resp = httpx.post(
        f"{BASE_URL}/learning-analysis/generate",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "session_id": session_id,
            "class_id": CLASS_ID,
            "subject": "数学",
            "grade": "六年级",
            "time_range": {},
            "knowledge_scope": "",
            "teacher_requirement": "重点关注后进生",
            "report_type": "class",
            "template": "primary",
        },
        timeout=180.0, trust_env=False,
    )
    resp.raise_for_status()
    body = resp.json()

    report_id = body.get("saved_report_id", "")
    validation = body.get("validation", {})
    report_content = body.get("report_content", {})

    print(f"saved_report_id: {report_id}")
    print(f"validation.passed: {validation.get('passed')}")
    print(f"validation.issues: {validation.get('issues')}")
    print(f"report_content 模块: {list(report_content.keys())}")
    print(f"掌握度 kp 数: {len(report_content.get('mastery', []))}")
    print(f"分层数: {len(report_content.get('stratification', []))}")
    print(f"典型错题数: {len(report_content.get('errors', []))}")

    # 断言：落库成功 + 质检通过 + 报告结构完整
    assert report_id, "质检通过应落库，saved_report_id 不应为空"
    assert validation.get("passed") is True, f"质检应通过，issues={validation.get('issues')}"
    assert "meta" in report_content and "mastery" in report_content
    assert len(report_content.get("mastery", [])) >= 1, "应有掌握度数据"
    assert len(report_content.get("stratification", [])) == 3, "应三层（含空层）"

    # ── ③ GET 读落库报告 ──────────────────────────────────────
    print_section("③ GET 读取落库报告")
    resp = httpx.get(
        f"{BASE_URL}/learning-analysis/report/{report_id}",
        headers={"Authorization": f"Bearer {token}"},
        trust_env=False,
    )
    resp.raise_for_status()
    saved = resp.json()
    print(f"status: {saved.get('status')}")
    print(f"metrics.kp_mastery 数: {len(saved.get('metrics', {}).get('kp_mastery', []))}")
    print(f"report_content.meta: {list(saved.get('report_content', {}).get('meta', {}).keys())}")
    assert saved.get("status") == "draft"
    assert saved.get("report_content", {}).get("meta", {}).get("subject") == "数学"

    # ── ④ 教学建议抽查（LLM 三键非空）────────────────────────
    print_section("④ 教学建议抽查")
    suggestions = report_content.get("suggestions", {}).get("teaching", {})
    for k, v in suggestions.items():
        print(f"  【{k}】{str(v)[:60]}...")
    assert all(suggestions.get(k) for k in ("分层建议", "错题补救", "后续教学计划")), "三键应非空"

    print_section("✅ 学情分析 e2e 全部通过")
