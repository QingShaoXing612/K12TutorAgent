# backend/api/v1/unified_chat.py
# 统一 AI 助手入口：supervisor 主状态机调度 → 流式执行
#
# SSE 事件类型：
#   routing_decision  路由决策结果（agent_type / reason / confidence / execution_mode）
#   progress          QA 流水线进度提示（检索知识库...等）
#   token             QA Agent 流式 token
#   guidance          非 QA 意图的引导消息（含跳转链接）
#   pipeline_plan     多 Agent 协同计划（steps 数组，含各步 label/desc/action_url）
#   meta              QA 回答完毕后的元数据（answer_mode / sources 等）
#   done              流结束信号
#   error             异常

import json
import re
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse
from langchain_core.messages import HumanMessage

from backend.core.memory import build_thread_id                  # thread_id 工具
from backend.dependencies import get_current_user                # 认证依赖
from backend.core.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)


# ── 路由决策 → 前端展示映射（supervisor next 标签 → 中文名 / 执行模式）──
_AGENT_DISPLAY = {
    "qa":                "智能问答",
    "exam":              "试卷批改",
    "lesson_prep":       "智能备课",
    "learning_analysis": "学情报告",
    "pipeline":          "多Agent协同",
    "clarify":           "智能问答",
}

_EXECUTION_MODE = {
    "qa":                "single",
    "exam":              "single",
    "lesson_prep":       "single",
    "learning_analysis": "single",
    "pipeline":          "pipeline",
    "clarify":           "clarify",
}

# ── QA Agent 节点进度提示（与 qa.py 的节点名保持一致）────────
_PROGRESS_LABELS = {
    "classify_query":      "理解问题中...",
    "hyde_generate":       "理解问题中...",
    "multi_query_rewrite": "改写查询中...",
    "retrieve":            "召回相关文档...",
    "web_search":          "搜索互联网...",
    "generate_general":    "思考中...",
}
_GENERATE_NODES = {"generate_rag", "generate_direct", "generate_general"}  # QA 的三个生成节点

# ── 去除末尾标点空白，用于关键词精确比对 ──────────────────────────
_STRIP_TAIL_RE = re.compile(r"[\s!！?？。~～,.，。]+$")

# ── 类别一：问候（你好 / 在吗）────────────────────────────────────
_HELLO_KEYWORDS = frozenset([
    "你好", "您好", "hi", "hello", "hey", "哈喽", "嗨",
    "在吗", "在不在", "在线吗", "有人吗",
])

# ── 类别二：感谢（谢谢 / 辛苦了 / 太棒了）────────────────────────
_THANKS_KEYWORDS = frozenset([
    "谢谢", "感谢", "多谢", "谢了", "非常感谢", "万分感谢",
    "辛苦了", "辛苦", "麻烦了", "不好意思",
    "太棒了", "太好了", "厉害", "厉害了", "牛", "牛啊", "牛逼",
    "好的好的", "明白了", "懂了", "知道了", "收到",
])

# ── 类别三：道别（再见 / bye）──────────────────────────────────────
_BYE_KEYWORDS = frozenset([
    "再见", "拜拜", "拜", "88", "886", "bye", "goodbye", "byebye",
    "下次见", "下次再聊", "先走了", "先撤了", "溜了", "闪了",
])

# ── 类别四：身份询问（你是谁 / 介绍你自己）── 正则匹配 ──────────────
_IDENTITY_RE = re.compile(
    r"(你|您)(是谁|叫什么|的名字|是什么|是.*AI|是.*机器人|是.*助手)"
    r"|介绍.{0,4}(你自己|自己|一下)"
    r"|你是谁"
    r"|你叫(啥|什么名)",
    re.IGNORECASE,
)

# ── 类别五：功能询问（你能做什么 / 怎么用）── 正则匹配 ──────────────
_CAPABILITY_RE = re.compile(
    r"(你|您)(能|可以|会).{0,6}(做|帮|干)"
    r"|(你|您).{0,4}(功能|用途|能力|特点)"
    r"|怎么(用|使用)(你|您|这个)?"
    r"|(使用说明|帮助菜单|help|usage)"
    r"|你能帮(我|忙)吗",
    re.IGNORECASE,
)
# ── 多 Agent 能力说明（HELLO / IDENTITY / CAPABILITY 共享结尾）────
_MULTI_AGENT_TIP = (
    "- **多 Agent 协同**：描述综合需求（如「根据这次学情报告帮我备课」），"
    "AI 将自动串联学情报告 + 智能备课两个 Agent 协同为您服务\n"
)

# ── 五类回复模板（节选问候，其余结构相同）──────────────────────────
_REPLY_HELLO = (
    "您好！我是 TeachMate AI 助教。\n\n"
    "我可以帮您：\n"
    "- **智能问答**：直接输入问题，我会从知识库中检索并解答\n"
    "- **试卷批改**：告诉我「我要批改试卷」，上传 Word 答卷，AI 完成两轨批改和薄弱点分析\n"
    "- **智能备课**：告诉我「帮我备课」，输入章节主题，AI 生成教案与习题\n"
    "- **学情报告**：告诉我「出学情报告」，选择班级，AI 分析掌握度与分层画像\n"
    + _MULTI_AGENT_TIP
    + "\n请问有什么可以帮到您？"
)

_REPLY_THANKS = (
    "不客气，很高兴能帮到您！\n\n"
    "如果还有其他问题，随时告诉我，我随时在线。"
)

_REPLY_BYE = (
    "再见！祝您教学顺利，期待下次与您交流。"
)

_REPLY_IDENTITY = (
    "我是 **TeachMate AI 助教**，一个面向 K12 教学场景的智能辅助系统。\n\n"
    "我由多个专业 Agent 协同构成：\n"
    "- **智能问答 Agent**：基于 RAG 知识库，即时解答学科问题\n"
    "- **试卷批改 Agent**：AI 两轨并行批改 + 知识薄弱点分析\n"
    "- **智能备课 Agent**：生成教案与习题，支持反思回炉优化\n"
    "- **学情报告 Agent**：班级掌握度 + 分层画像 + 教学建议\n"
    + _MULTI_AGENT_TIP
    + "\n有什么可以帮到您吗？"
)

_REPLY_CAPABILITY = (
    "我能为您提供以下功能：\n\n"
    "**单 Agent 直达**\n"
    "- 直接输入学科问题 → 智能问答（RAG 知识库检索）\n"
    "- 「批改试卷」 → 上传 Word 答卷，AI 完成批改并分析薄弱点\n"
    "- 「帮我备课」 → 输入章节主题，生成教案 + 习题\n"
    "- 「出学情报告」 → 选择班级，分析掌握度与分层画像\n\n"
    "**多 Agent 协同**\n"
    + _MULTI_AGENT_TIP
    + "\n直接告诉我您的需求，我会自动路由到最合适的 Agent。"
)


def _pre_filter(text: str) -> str | None:
    """
    规则前置拦截，返回模板回复字符串；无命中返回 None（继续正常路由）。
    五类社交/元场景均为零 Token 消耗（不调 LLM）。
    """
    t = text.strip()                                 # 去首尾空白
    t_lower = _STRIP_TAIL_RE.sub("", t.lower())      # 转小写 + 去末尾标点，得到比对用文本

    # 类别一：问候（精确匹配整句）
    if t_lower in _HELLO_KEYWORDS:
        return _REPLY_HELLO

    # 类别二：感谢（精确匹配整句）
    if t_lower in _THANKS_KEYWORDS:
        return _REPLY_THANKS

    # 类别三：道别（精确匹配整句）
    if t_lower in _BYE_KEYWORDS:
        return _REPLY_BYE

    # 类别四：身份询问（正则，用原文 t 而非去标点的 t_lower）
    if _IDENTITY_RE.search(t):
        return _REPLY_IDENTITY

    # 类别五：功能询问（正则）
    if _CAPABILITY_RE.search(t):
        return _REPLY_CAPABILITY

    return None                                      # 五类都没命中 → 交给 supervisor 路由


class UnifiedChatRequest(BaseModel):
    session_id: str = Field(..., description="会话 ID")
    message:    str = Field(..., min_length=1, max_length=2000, description="用户输入")  # 非空、限长


def _sse(data: dict) -> dict:
    """把 dict 包成 sse_starlette 要的格式：{"data": "<JSON字符串>"}。ensure_ascii=False 保留中文。"""
    return {"data": json.dumps(data, ensure_ascii=False)}


@router.post("/stream")
async def unified_chat_stream(
    req: UnifiedChatRequest,                          # 请求体：session_id + message
    current_user: dict = Depends(get_current_user),   # 认证依赖
):
    """
    统一 AI 助手流式接口（SSE）。

    请求：{session_id, message}
    响应：text/event-stream，事件序列随 supervisor 路由结果不同而不同

    流程：
        1. 规则前置拦截（五类社交/元场景，零 Token 直接返回）
        2. 跑 supervisor 主状态机（LLM 决策 next → qa 流式 / 引导 / 协同计划 / 追问）
        3. 逐事件翻译为 SSE（routing_decision / progress / token / guidance / pipeline_plan / meta / done）
    """

    async def event_generator():                      # 异步生成器：逐个 yield SSE 事件
        # ── Step 0：规则前置拦截（零 Token，五类模板回复）──────────
        pre_reply = _pre_filter(req.message)          # 命中则返回模板字符串，否则 None
        if pre_reply is not None:
            yield _sse({"type": "token", "content": pre_reply})  # 模板内容当作一个 token 事件推出
            yield _sse({"type": "done"})              # 直接结束
            return                                    # 不再走 supervisor（省 LLM）

        # ── Step 1：跑 supervisor 主状态机（真实调度，非 if/else）──
        from backend.core.supervisor import get_supervisor_graph
        graph = get_supervisor_graph()                # 懒加载缓存的 supervisor 图

        thread_id = build_thread_id(current_user["user_id"], req.session_id)
        initial_state = {                             # 构造 SupervisorState 输入（对齐 test_supervisor base_state）
            "messages":           [HumanMessage(content=req.message)],
            "next":               "",
            "reason":             "",
            "student_id":         current_user["user_id"],
            "tenant_id":          current_user["tenant_id"],
            "session_id":         req.session_id,
            "course_id":          None,
            "original_query":     req.message,
            "query_type":         "PRECISE",
            "rewritten_queries":  [],
            "hyde_document":      None,
            "ranked_chunks":      [],
            "confidence":         0.0,
            "is_high_confidence": False,
            "web_search_results": [],
            "answer":             "",
            "sources":            [],
            "answer_mode":        "",
            "existing_summary":   None,
            "should_summarize":   False,
            "enable_web_search":  False,
            "fallback_used":      False,
            "structured_output":  None,
            "guidance":           {},
            "pipeline_plan":      {},
        }
        config = {"configurable": {"thread_id": thread_id}}

        answer_mode = None
        confidence  = 0.0
        sources: list[str] = []                       # 引用来源

        try:
            async for event in graph.astream_events(initial_state, config=config, version="v2"):
                evt  = event["event"]                 # 事件类型
                name = event.get("name", "")          # 节点/边/模型名（区分节点本体 vs 条件边）
                node = event.get("metadata", {}).get("langgraph_node", "")  # 图节点名

                # ① supervisor 决策（on_chain_end 且 name==supervisor，排除 _route_by_next 条件边）
                if evt == "on_chain_end" and name == "supervisor":
                    out = event["data"].get("output", {})
                    next_label = out.get("next", "finish")
                    if next_label != "finish":        # finish 是收敛信号，不推路由卡片
                        yield _sse({
                            "type":           "routing_decision",
                            "agent_type":     next_label,
                            "agent_display":  _AGENT_DISPLAY.get(next_label, next_label),
                            "confidence":     round(0.85, 4),
                            "reason":         out.get("reason", ""),
                            "execution_mode": _EXECUTION_MODE.get(next_label, "single"),
                        })

                # ② qa 子图进度（name==node 排除条件边，避免重复「理解问题中」）
                elif evt == "on_chain_start" and name == node and node in _PROGRESS_LABELS:
                    yield _sse({"type": "progress", "stage": _PROGRESS_LABELS[node]})

                # ③ qa 子图 token 流（on_chat_model_stream，name 是 ChatOpenAI 等模型名）
                elif evt == "on_chat_model_stream" and node in _GENERATE_NODES:
                    chunk = event["data"].get("chunk")
                    if chunk and chunk.content:
                        yield _sse({"type": "token", "content": chunk.content})

                # ④ qa 生成节点结束 → 回填 answer_mode / sources / confidence（供最后 meta）
                elif evt == "on_chain_end" and name == node and node in _GENERATE_NODES:
                    output = event["data"].get("output", {})
                    if output.get("answer_mode"):
                        answer_mode = output["answer_mode"]
                    if output.get("sources"):
                        sources = output["sources"]
                    confidence = (output.get("structured_output") or {}).get("confidence", confidence)

                # ⑤ 引导节点（exam/lesson_prep/learning_analysis/clarify）→ guidance
                elif evt == "on_chain_end" and name in ("exam", "lesson_prep", "learning_analysis", "clarify"):
                    g = event["data"].get("output", {}).get("guidance", {})
                    if g:
                        yield _sse({
                            "type":         "guidance",
                            "message":      g.get("message", ""),
                            "action_label": g.get("action_label", ""),
                            "action_url":   g.get("action_url", ""),
                        })

                # ⑥ pipeline 节点（multi_agent）→ pipeline_plan
                elif evt == "on_chain_end" and name == "pipeline":
                    plan = event["data"].get("output", {}).get("pipeline_plan", {})
                    if plan:
                        yield _sse({"type": "pipeline_plan", **plan})

        except Exception as e:                        # supervisor 执行异常 → 推 error 并结束
            logger.error("unified_chat.supervisor_stream_error", error=str(e), exc_info=True)
            yield _sse({"type": "error", "message": "服务异常，请稍后重试"})
            yield _sse({"type": "done"})
            return

        # ── Step 2：qa 答完 → 推 meta + done ────────────────────
        if answer_mode:
            yield _sse({"type": "meta", "answer_mode": answer_mode, "confidence": confidence, "sources": sources})
        yield _sse({"type": "done"})                  # 所有分支最终都推一个 done 收尾

    return EventSourceResponse(event_generator())     # 用 SSE 响应包装生成器
