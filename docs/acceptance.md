# 验收数据汇总表

> K12TutorAgent 四大 Agent + RAG 评测 + 统一入口的验收结果汇总。每项标注验证来源与时间，区分「实跑」与「历史记录」。

## 一、四大 Agent

| Agent | 规模 | 关键特性 | e2e 测试 | 结果 | 验证来源 |
|---|---|---|---|---|---|
| 智能问答 qa | 10 节点 | 意图分类 + HyDE/多查询改写 + BGE 精排 + RAG/联网/直答三分支 + 多轮记忆 | `test_qa.py`（8 场景） | ✅ 全绿 | 2026-09-21 实跑 |
| 试卷批改 exam | 9 节点 | 两轨批改（客观规则/简答 LLM）+ interrupt 教师复核 | `test_exam.py`（HTTP e2e） | ✅ 全绿 | 2026-09-21 实跑 |
| 智能备课 lesson_prep | 10 节点 | 并行目标∥重难点 + 逐题质检 + interrupt 反思回炉 | `test_lesson_prep.py`（HTTP e2e） | ✅ 全绿 | 2026-09-21 实跑 |
| 学情报告 learning_analysis | 10 节点 | 并行掌握度∥错题 + 分层画像 + 质检防篡改 + 3 跨 Agent seam | `test_learning_analysis.py`（HTTP e2e） | ✅ 全绿 | 2026-09-21 实跑 |

## 二、RAG 质量评测（RAGAS 风格，DeepSeek LLM-as-judge）

评测集 = `samples/` 下 3 份中小学数学语料（圆的周长教案 / 义务教育数学课标 / 分数数形结合论文），基础集 15 题（单跳）+ 困难集 10 题（多跳/跨 chunk/对抗）。

| 指标 | 基础集 | 困难集 |
|---|---|---|
| 答案忠实度 faithfulness | 1.000 | 0.908 |
| 答案相关性 answer_relevancy | 0.870 | 0.830 |
| 上下文精确率 context_precision | 0.763 | 0.739 |
| 上下文召回率 context_recall | 1.000 | 0.767 |

- 裁判校准（负样本判别力）：**+1.00**（有据 1.00 / 幻觉 0.00 / 无关上下文 0.00 / 半有据 0.50）。
- 边界：困难集跨文档题召回偏低（「直径/半径关系」召回 0.00、「教学反思 vs 论文结论」精率 0.28 召回 0.50），根因是语料库较小（15 chunk）+ 跨文档综合题需多路召回，QA 的 BROAD 路径（Multi-Query）已具备，修法=扩充语料 + 查询分解。
- **2026-09-22 实跑**（换中小学语料后重测），详见 [`ragas_report.md`](ragas_report.md)。

## 三、统一入口路由（unified_chat，SSE）

| 输入 | 路由结果 | 结果 |
|---|---|---|
| 「你好」 | 规则前置拦截（零 LLM） | ✅ |
| 「帮我备课」 | lesson_prep + 引导跳转 | ✅ |
| 「帮我批改试卷」 | exam + 引导跳转 | ✅ |
| 「出一份学情报告」 | learning_analysis + 引导跳转 | ✅ |
| 「根据学情报告帮我备课」 | multi_agent pipeline 计划 | ✅ |
| 学科问题 | qa 直连流式（progress→token→meta→done） | ✅ |

**2026-09-20 实跑**（登录 teacher01 → `POST /api/v1/chat/stream`）。

## 四、跨 Agent 协同（3 seam，全部接线）

| seam | 说明 | 端点 |
|---|---|---|
| ① 一键备课 | 学情报告 → 预填薄弱点生成教案 | `POST /learning-analysis/report/{id}/generate-lesson` |
| ② 数据回流 | 作业提交判分 → 写入 student_practice_records（带 kp_id） | `POST /homework/{id}/submit` |
| ③ 教案当干预参考 | 干预策略带 source_lesson_id 软信号 | 学情分析节点6 检索 |

## 验证来源说明

- 四个 Agent 的 e2e 均于 **2026-09-21 在系统集成后的 main.py 上统一重跑**（`scripts/manual_tests/` 下 4 个 test 脚本），全部通过。
- 重跑暴露并修复了两个集成后的问题：
  1. `seed_lesson_data.py` 整表 `DELETE FROM exercise_bank` 撞 `student_practice_records` 外键（exercise_bank 已被学情/作业模块共享引用）——已加依赖表清理。
  2. `main.py` 预热用 `run_in_executor` 后台线程加载 transformers 模型，meta-device 权重 dispatch 失败（QueryClassifier 报 `Tensor.item() cannot be called on meta tensors`）——已改为同步顺序加载。
