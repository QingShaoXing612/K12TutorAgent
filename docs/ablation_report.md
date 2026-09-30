# 检索链消融对比报告

- 评测集：高中 13 题（独立评测集：高中数学/物理/化学核心题）
- 命中口径两级：① 文档级 = source_name 文件名 == 预期来源；② 答案级 = top-1 chunk 内容含参考答案关键词
- 三配置统一先召回 10 候选，再取 top-1 判定

## 命中率对比

| 口径 | dense-only | hybrid | hybrid+rerank |
|---|---|---|---|
| 文档级 top-1 命中 | 13/13 (100%) | 13/13 (100%) | 12/13 (92%) |
| 答案级 top-1 命中 | 9/13 (69%) | 9/13 (69%) | 12/13 (92%) |

## 逐题明细

| 题目 | 预期来源 | dense(doc/ans) | hybrid(doc/ans) | rerank(doc/ans) |
|---|---|---|---|---|
| 函数的三要素是什么？ | high_math_lesson_function | ✓/✓ | ✓/✓ | ✓/✗ |
| 函数的单调性如何定义？ | high_math_lesson_function | ✓/✓ | ✓/✓ | ✗/✓ |
| 指数函数 y=a^x 中 a 的取值范围是什 | high_math_lesson_function | ✓/✗ | ✓/✗ | ✓/✓ |
| 对数函数与指数函数是什么关系？ | high_math_lesson_function | ✓/✗ | ✓/✗ | ✓/✓ |
| 椭圆的离心率公式是什么？取值范围如何？ | high_math_lesson_conic | ✓/✗ | ✓/✗ | ✓/✓ |
| 椭圆标准方程中 a、b、c 满足什么关系？ | high_math_lesson_conic | ✓/✗ | ✓/✗ | ✓/✓ |
| 椭圆的定义是什么？ | high_math_lesson_conic | ✓/✓ | ✓/✓ | ✓/✓ |
| 二项分布 X~B(n,p) 的概率公式是什么 | high_math_lesson_probability | ✓/✓ | ✓/✓ | ✓/✓ |
| 正态分布的概率密度曲线关于什么对称？ | high_math_lesson_probability | ✓/✓ | ✓/✓ | ✓/✓ |
| 高中数学学科核心素养包括哪些？ | high_math_curriculum | ✓/✓ | ✓/✓ | ✓/✓ |
| 高中数学的函数主线包括哪些内容？ | high_math_curriculum | ✓/✓ | ✓/✓ | ✓/✓ |
| 牛顿第二定律的公式是什么？ | high_physics_lesson_newton | ✓/✓ | ✓/✓ | ✓/✓ |
| 氧化还原反应的本质是什么？ | high_chemistry_lesson_redox | ✓/✓ | ✓/✓ | ✓/✓ |

