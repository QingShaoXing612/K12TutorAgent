# scripts/manual_tests/test_homework.py
# 作业模块端到端：教师选题布置 → 学生查列表/详情 → 提交判分（对 2 错 1）

import sys
import uuid
import httpx

sys.path.insert(0, ".")

BASE_URL = "http://localhost:8000/api/v1"
CLASS_ID = "c1a55e00-0000-4000-8000-000000000001"


def login(username, password):
    r = httpx.post(f"{BASE_URL}/auth/login", json={"username": username, "password": password}, trust_env=False)
    r.raise_for_status()
    return r.json()["access_token"]


def find_by(ex_items, keyword):
    return next(x for x in ex_items if keyword in x["content"])


def main():
    th = {"Authorization": f"Bearer {login('teacher01', 'Teacher@123456')}"}
    sh = {"Authorization": f"Bearer {login('student01', 'Student@123456')}"}

    # ① 教师查习题库
    items = httpx.get(f"{BASE_URL}/exercises?subject=数学&grade=六年级", headers=th, trust_env=False).json()["items"]
    print(f"① 习题库：{len(items)} 道题")
    assert len(items) >= 12, f"习题库应 ≥12 道，实际 {len(items)}"

    # 选 3 道已知题（分数乘法 / 分数除法 / 比），答案已知
    q_mul = find_by(items, "3/4 × 2/5")   # 答案 3/10
    q_div = find_by(items, "2/3 是 12")   # 答案 18
    q_ratio = find_by(items, "12 : 18")   # 答案 2:3
    exercise_ids = [q_mul["exercise_id"], q_div["exercise_id"], q_ratio["exercise_id"]]

    # ② 教师布置作业
    title = f"作业 e2e {uuid.uuid4().hex[:6]}"
    r = httpx.post(f"{BASE_URL}/homework", json={
        "class_id": CLASS_ID, "subject": "数学", "grade": "六年级",
        "title": title, "exercise_ids": exercise_ids,
    }, headers=th, trust_env=False)
    r.raise_for_status()
    aid = r.json()["assignment_id"]
    print(f"② 布置作业：{aid[:8]}…（{r.json()['exercise_count']} 题）")

    # ③ 教师列表应含刚布置的
    tlist = httpx.get(f"{BASE_URL}/homework", headers=th, trust_env=False).json()["items"]
    assert any(x["assignment_id"] == aid for x in tlist), "教师列表应含刚布置的作业"
    print(f"③ 教师作业列表：{len(tlist)} 条 ✅")

    # ④ 学生列表应含该作业（同班级）
    slist = httpx.get(f"{BASE_URL}/homework", headers=sh, trust_env=False).json()["items"]
    assert any(x["assignment_id"] == aid for x in slist), "学生列表应含该作业（同班级）"
    print(f"④ 学生作业列表：{len(slist)} 条 ✅")

    # ⑤ 学生查详情（3 题，不含答案）
    detail = httpx.get(f"{BASE_URL}/homework/{aid}", headers=sh, trust_env=False).json()
    assert len(detail["exercises"]) == 3, f"应 3 题，实际 {len(detail['exercises'])}"
    assert all("answer" not in ex for ex in detail["exercises"]), "详情不应泄漏答案"
    print(f"⑤ 作业详情：{len(detail['exercises'])} 题（无答案泄漏）✅")

    # ⑥ 学生提交：对 2 错 1（分数乘法对 / 分数除法对 / 比错）
    answers = [
        {"exercise_id": q_mul["exercise_id"], "answer": "3/10"},   # 对
        {"exercise_id": q_div["exercise_id"], "answer": "18"},     # 对
        {"exercise_id": q_ratio["exercise_id"], "answer": "3:4"},  # 错（应为 2:3）
    ]
    sub = httpx.post(f"{BASE_URL}/homework/{aid}/submit", json={"answers": answers}, headers=sh, trust_env=False)
    sub.raise_for_status()
    res = sub.json()
    correct = [r["is_correct"] for r in res["results"]]
    assert correct == [True, True, False], f"判分应 [对,对,错]，实际 {correct}"
    assert res["score"] == 15 and res["full_score"] == 20, f"应 15/20，实际 {res['score']}/{res['full_score']}"
    print(f"⑥ 提交判分：{res['score']}/{res['full_score']}，逐题 {['✓' if c else '✗' for c in correct]} ✅")

    print("\n✅ 作业模块端到端全链路通过（布置 → 列表 → 详情 → 提交判分）")


if __name__ == "__main__":
    main()
