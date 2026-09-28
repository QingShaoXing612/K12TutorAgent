# scripts/manual_tests/test_supervisor.py
# supervisor 主状态机端到端：qa（真挂 subgraph 回答）+ lesson_prep（引导）

import asyncio
import sys
import uuid
import httpx

sys.path.insert(0, ".")

BASE_URL = "http://localhost:8000/api/v1"

from langchain_core.messages import HumanMessage
from backend.core.supervisor import build_supervisor_graph, pipeline_node, clarify_node


def login(username, password):
    r = httpx.post(f"{BASE_URL}/auth/login", json={"username": username, "password": password}, trust_env=False)
    r.raise_for_status()
    return r.json()


def base_state(user):
    return {
        "messages": [],
        "next": "",
        "reason": "",
        "student_id": user["user_id"],
        "tenant_id": "tenant_default",
        "session_id": f"sup-{uuid.uuid4().hex[:8]}",
        "course_id": None,
        "original_query": "",
        "query_type": "PRECISE",
        "rewritten_queries": [],
        "hyde_document": None,
        "ranked_chunks": [],
        "confidence": 0.0,
        "is_high_confidence": False,
        "web_search_results": [],
        "answer": "",
        "sources": [],
        "answer_mode": "",
        "existing_summary": None,
        "should_summarize": False,
        "enable_web_search": False,
        "fallback_used": False,
        "structured_output": None,
        "guidance": {},
        "pipeline_plan": {},
    }


async def main():
    user = login("teacher01", "Teacher@123456")
    graph = build_supervisor_graph()

    # 场景① qa：supervisor 决策 qa → qa subgraph 回答 → supervisor 决策 finish
    s1 = base_state(user)
    s1["messages"] = [HumanMessage(content="圆的周长公式是什么？")]
    r1 = await graph.ainvoke(s1, config={"configurable": {"thread_id": f"sup-qa-{uuid.uuid4().hex[:6]}"}})
    print(f"① qa 场景：answer_mode={r1['answer_mode']}，answer={r1['answer'][:50]}...")
    assert r1["answer"], "qa 应产生回答"
    assert r1["answer_mode"] in ("rag", "llm_direct", "general"), f"answer_mode 异常：{r1['answer_mode']}"

    # 场景② lesson_prep：supervisor 决策 lesson_prep → 引导节点 → finish
    s2 = base_state(user)
    s2["messages"] = [HumanMessage(content="帮我备一节课")]
    r2 = await graph.ainvoke(s2, config={"configurable": {"thread_id": f"sup-lp-{uuid.uuid4().hex[:6]}"}})
    print(f"② lesson_prep 场景：action_url={r2['guidance'].get('action_url')}，next={r2['next']}")
    assert r2["guidance"].get("action_url") == "/lesson-prep", "lesson_prep 应返回引导卡片"

    # 场景③④：直接调 pipeline / clarify 节点（确定性，不依赖 LLM 路由）
    r3 = await pipeline_node(base_state(user))
    assert r3["pipeline_plan"]["steps"], "pipeline 节点应返回协同计划 steps"
    assert r3["messages"], "pipeline 节点应返回 AI 消息（触发收敛）"
    print(f"③ pipeline 节点：title={r3['pipeline_plan']['title']}，steps={len(r3['pipeline_plan']['steps'])}")

    r4 = await clarify_node(base_state(user))
    assert r4["guidance"]["message"], "clarify 节点应返回追问引导"
    assert r4["messages"], "clarify 节点应返回 AI 消息（触发收敛）"
    print(f"④ clarify 节点：message={r4['guidance']['message'][:20]}...")

    print("\n✅ supervisor 主状态机四场景跑通（qa 直连 + 引导 + 协同计划 + 追问）")


if __name__ == "__main__":
    asyncio.run(main())
