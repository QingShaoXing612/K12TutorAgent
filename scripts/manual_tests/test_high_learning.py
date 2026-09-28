# scripts/manual_tests/test_high_learning.py
# 高中学情报告 e2e（需先起服务：python -m backend.main）
# 依赖 seed_high_data.py 已灌高一数学 practice（8 条）+ 干预策略（4 条）

import sys
import httpx

sys.path.insert(0, ".")

BASE_URL = "http://localhost:8000/api/v1"
CLASS_ID = "c1a55e00-0000-4000-8000-000000000001"


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
    section("① 教师登录")
    token = login("teacher01", "Teacher@123456")
    print("teacher01 登录成功")

    section("② 生成高一数学学情报告（跑完整 10 节点图，约 60-90 秒）")
    resp = httpx.post(
        f"{BASE_URL}/learning-analysis/generate",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "session_id": "e2e-high-learning-001",
            "class_id": CLASS_ID,
            "subject": "数学",
            "grade": "高一",
            "time_range": {},
            "knowledge_scope": "",
            "teacher_requirement": "重点关注薄弱学生",
            "report_type": "class",
            "template": "",   # 空串 → _infer_template 按 grade 推断 senior
        },
        timeout=180.0, trust_env=False,
    )
    resp.raise_for_status()
    body = resp.json()

    report_id = body.get("saved_report_id", "")
    validation = body.get("validation", {})
    rc = body.get("report_content", {})

    print(f"saved_report_id  : {report_id}")
    print(f"validation.passed : {validation.get('passed')}")
    print(f"validation.issues : {validation.get('issues')}")
    print(f"report 模块       : {list(rc.keys())}")
    print(f"掌握度 kp 数      : {len(rc.get('mastery', []))}")
    print(f"分层数            : {len(rc.get('stratification', []))}")
    print(f"典型错题数        : {len(rc.get('errors', []))}")
    print(f"推断 template     : {rc.get('meta', {}).get('template')}")

    # 断言：落库 + 质检通过 + 有掌握度数据 + 分层 3 层
    assert report_id, "质检通过应落库"
    assert validation.get("passed") is True, f"质检应通过，issues={validation.get('issues')}"
    assert len(rc.get("mastery", [])) >= 1, "应有掌握度数据"
    assert len(rc.get("stratification", [])) == 3, "应三层（含空层）"
    assert rc.get("meta", {}).get("template") == "senior", "应推断出 senior 模板"

    section("✅ 高中学情报告 e2e 全部通过")
