# 简历项目经历 — K12TutorAgent

> 口径红线（已与代码核对）：技术栈以代码为准 = PostgreSQL / Milvus / DeepSeek / LangGraph / asyncio；**不写** MySQL / Redis / Celery / Chroma / PaddleOCR / Qwen / OCR / 图像识别（试卷批改只收 `.docx`，「拍照答疑」实为文本 RAG）。

---

## 项目一：K12 多智能体教研助教系统（K12TutorAgent）

**项目背景**：中小学教师日常存在备课、试卷批阅、学情分析、习题答疑等重复性工作，传统工具缺少全流程 AI 辅助能力，人工工作量大；通用大模型直接回答学科问题易脱离课程体系、产生幻觉。基于此搭建覆盖 K12 全学段（1~12 年级）的多智能体教研助教系统 K12TutorAgent。

**技术栈**：Python、FastAPI、LangGraph、MCP、PostgreSQL、Milvus、BGE-M3 / BGE-Reranker、MiniLM、DeepSeek API、RAGAS

**主要职责**：
- **整体架构设计**：基于 LangGraph 搭建顶层 supervisor 主状态机 + Subgraph 子图架构，封装智能问答、试卷批改、智能备课、学情报告四大业务 Agent（各 9~10 节点）；设计意图识别 Agent（规则层 + MiniLM 微调）与 orchestrator 图懒加载缓存，完成意图解析、多 Agent 路由、跨 Agent 计划编排。
- **工具与存储设计**：基于 MCP 协议封装知识库检索、联网搜索工具服务；采用 PostgreSQL + Milvus 双层存储方案，PostgreSQL 承载业务数据与 LangGraph 检查点（AsyncPostgresSaver 持久化），Milvus 承载向量检索。
- **业务流程开发**：定义四大 Agent 完整节点流程与 interrupt 人机协同交互，实现教师复核、备课反思回炉；统一入口 SSE 流式输出（progress → token → meta）。
- **链路编排与提示词工程**：完成 RAG 问答、试卷批阅、备课质检、学情报告全链路编排；编写阅卷、报告生成专用 Prompt，区分小学、初中、高中三套学情渲染模板，实现业务分支隔离。

**项目优化与技术创新**：
1. **Agent 架构优化**：采用 Subgraph 封装内部业务节点，解决顶层状态机节点泛滥问题；图编译结果按 AgentType 懒加载缓存，降低冷启动开销。
2. **上下文开销优化**：实现会话超限 LLM 自动摘要压缩（多轮记忆），基于意图分类结果按需加载历史上下文，避免全量历史传入导致 Token 持续膨胀。
3. **大模型输出稳定性优化**：三层兜底重试 + 结构化日志；主观阅卷引入置信度打分，低置信样本自动进入 interrupt 人工复核，降低模型幻觉风险；知识点检索限定候选集合，杜绝编造超纲知识点。
4. **自动化评测体系**：搭建基于 RAGAS 四指标的全链路评测框架（DeepSeek LLM-as-judge 复刻），人工构建基础/困难/高中三组评测集，完成裁判校准（负样本判别力 +1.00），评估生成内容准确性、合规性。

**项目成果**：
- **检索质量**：RAGAS 忠实度 0.990、召回 0.942（高中集），基础集忠实度与召回双 1.000；BGE 精排消融实验将「含答案 chunk 排第一」比例由 **69% 提升至 92%（+23pp）**。
- **意图路由**：MiniLM 微调做 general/specialized 二分类，程序化测试集准确率 96.1%。
- **业务闭环**：试卷批改 e2e 20/20、问答 8 场景、备课回炉、学情全链路全绿（任务通过率 100%），覆盖 K12 全学段 1~12 年级。
- **可复用资产**：意图分类训练集 1277 条（覆盖数学/语文/英语/科学/物理/化学 6 科）；RAGAS 四指标评测框架 + 38 题评测集（基础/困难/高中）+ 检索消融脚本；4 个 LangGraph Agent 状态机模板（问答 10 / 批改 9 / 备课 10 / 学情 10 节点）；2 个 MCP 工具（知识库检索 / 联网搜索）；K12 建库灌数脚本（含幂等 DB 迁移）。

---

## 附一：口径红线（为什么技术栈/成果必须这么写）

> 参考模板里，技术栈含 MySQL / Redis / Celery / Chroma / PaddleOCR / Qwen，成果含「成功率 74%→91%」「P95 7.2s→4.3s」「耗时 -53%」等几十个数字——**那是另一个项目的数据，本项目没有**，照搬即简历造假。
>
> 本项目真实口径（已逐行核对代码与评测脚本）：
> - 技术栈 = PostgreSQL（非 MySQL）、Milvus（非 Chroma）、DeepSeek（非 Qwen）、asyncio（非 Celery/Redis）；**无 OCR**（非 PaddleOCR）。
> - 唯一有基线的相对指标 = BGE 精排 69%→92%（答案级，`scripts/ablate_retrieval.py` 实跑）。其余均为绝对指标（RAGAS 分数、MiniLM 准确率、e2e 通过率）。
> - 「耗时降低」「P95 Latency」「超时率」等本项目未做过压测/基线，不写。

## 附二：数据来源

- 检索消融：`docs/ablation_report.md`（`scripts/ablate_retrieval.py`）
- RAGAS 评测：`docs/ragas_report.md`
- e2e 验收：`docs/acceptance.md`
