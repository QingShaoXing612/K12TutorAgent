# backend/core/orchestrator.py
# Orchestrator：Agent 图注册与懒加载（统一入口用）
#
# 轻量定位：只负责「Agent 类型定义 + 编译图缓存」。exam / lesson_prep / learning_analysis
# 的完整调用逻辑（含 interrupt 恢复）已在各自 api/v1/*.py 实现，统一入口对它们只做引导跳转，
# 不在此直连调用——避免用通用 state 硬套状态各异的 agent，避免重复实现。

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from backend.core.logger import get_logger

logger = get_logger(__name__)


class ExecutionMode(str, Enum):
    """执行模式：决定一个请求怎么跑。"""
    SINGLE   = "single"     # 单 Agent 直达（QA 流式）
    PIPELINE = "pipeline"   # 多 Agent 串联（如 学情报告 → 一键备课）
    CLARIFY  = "clarify"    # 澄清对话（意图不明，需追问）


class AgentType(str, Enum):
    """本项目的四个 Agent 类型（继承 str，可直接当字符串用）。"""
    QA                = "qa"                 # 智能问答
    EXAM              = "exam"               # 试卷批改
    LESSON_PREP       = "lesson_prep"        # 智能备课
    LEARNING_ANALYSIS = "learning_analysis"  # 学情报告


class Orchestrator:
    """Agent 图注册表：按需懒加载各 Agent 的编译图，供统一入口复用（避免重复 build）。"""

    def __init__(self):
        # Agent 图注册表（懒加载，key=AgentType，value=编译后的 LangGraph）
        self._agent_graphs: dict[AgentType, Any] = {}
        logger.info("orchestrator.initialized")

    def _get_agent_graph(self, agent_type: AgentType) -> Any:
        """
        懒加载 Agent 的 LangGraph 编译图。

        读取/写入：self._agent_graphs（图缓存字典）
        首次访问某 Agent 时才 import 并 build 它的图，之后复用缓存。
        """
        if agent_type not in self._agent_graphs:        # 缓存里没有 → 首次加载
            if agent_type == AgentType.QA:
                from backend.agents.qa.graph import build_qa_graph
                self._agent_graphs[agent_type] = build_qa_graph()

            elif agent_type == AgentType.EXAM:
                from backend.agents.exam.graph import build_exam_graph
                self._agent_graphs[agent_type] = build_exam_graph()

            elif agent_type == AgentType.LESSON_PREP:
                from backend.agents.lesson_prep.graph import build_lesson_prep_graph
                self._agent_graphs[agent_type] = build_lesson_prep_graph()

            elif agent_type == AgentType.LEARNING_ANALYSIS:
                from backend.agents.learning_analysis.graph import build_learning_analysis_graph
                self._agent_graphs[agent_type] = build_learning_analysis_graph()

            else:
                raise ValueError(f"未知 AgentType: {agent_type}")

            logger.info(
                "orchestrator.agent_graph_loaded",
                agent_type=agent_type.value,
            )

        return self._agent_graphs[agent_type]           # 返回（缓存的）编译图


# ──────────────────────────────────────────────────────────────
# 模块级单例（应用生命周期内复用）
# ──────────────────────────────────────────────────────────────
_orchestrator_instance: Optional[Orchestrator] = None    # 全局唯一实例（初始为空）


def get_orchestrator() -> Orchestrator:
    """获取 Orchestrator 单例（FastAPI 依赖注入使用）"""
    global _orchestrator_instance
    if _orchestrator_instance is None:                   # 第一次调用才创建
        _orchestrator_instance = Orchestrator()
    return _orchestrator_instance                        # 之后都返回同一个实例
