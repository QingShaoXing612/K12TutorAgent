# scripts/build_knowledge_base.py
# 知识库建库完整流水线：读取 → 分块 → Contextual RAG → BGE-M3 嵌入 → 写入 Milvus
# 运行：python scripts/build_knowledge_base.py

import asyncio
import sys
import uuid
from pathlib import Path

# 把项目根目录加入 Python 路径，使得本脚本能 import backend.*
sys.path.insert(0, str(Path(__file__).parent.parent))

from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_core.documents import Document
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
    MarkdownTextSplitter,
)

from backend.core.knowledge_base import (
    BGEMEmbedder,
    KnowledgeBaseClient,
    DocumentChunk,
    generate_chunk_id,
)
from backend.core.llm_factory import get_llm

# ── 常量 ─────────────────────────────────────────────────────

BATCH_SIZE = 12   # BGE-M3 批量推理大小（12 = 速度与显存的经验平衡点）

MAX_CONTEXT_CONCURRENCY = 5    # Contextual 上下文生成的最大并发 LLM 请求数

CONTEXTUAL_CHUNK_PROMPT = """\
<document>
{document_text}
</document>

以下是需要在整个文档中定位的 chunk：
<chunk>
{chunk_content}
</chunk>

请用一句简洁的中文，描述这段内容在整个文档中的位置和作用，以便改善检索效果。
只输出这一句描述，不要加任何前缀或标签。"""


# ── 模块级分块器单例 ──────────────────────────────────────────

_MD_HEADER_SPLITTER = MarkdownHeaderTextSplitter(
    headers_to_split_on=[
        ("#",   "H1"),
        ("##",  "H2"),
        ("###", "H3"),
        ("####", "H4"),
    ],
    strip_headers=False,
)

_CHAR_SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=512,
    chunk_overlap=100,
    separators=["\n\n", "\n", "。", "，", " ", ""],
)


# ── Step 1：读取文档（5.2）─────────────────────────────────────

def load_pdf(file_path: str) -> list[Document]:
    """
    加载 PDF 文档，每页返回一个 Document。

    只提取文字层内容；图片/扫描件页面 page_content 为空字符串，
    不报错（在分块时会过滤掉空页）。

    Args:
        file_path: PDF 文件的本地路径

    Returns:
        list[Document]，每个 Document 对应一页
        metadata 包含 source（文件路径）和 page（页码，从 0 开始）
    """
    loader = PyPDFLoader(file_path)
    pages = loader.load()
    print(f"  [PDF] 加载完成：{len(pages)} 页 ← {Path(file_path).name}")
    return pages


def load_markdown(file_path: str) -> list[Document]:
    """
    加载 Markdown 文档，整个文件作为一个 Document 返回。

    不在这里做标题切分——那是分块步骤的工作。
    这里只负责把文件内容读进内存。

    Args:
        file_path: Markdown 文件的本地路径（.md 或 .markdown）

    Returns:
        list[Document]，只有一个元素，page_content 为文件全文
        metadata 包含 source（文件路径）
    """
    loader = TextLoader(file_path, encoding="utf-8")
    docs = loader.load()
    char_count = len(docs[0].page_content)
    print(f"  [MD]  加载完成：{char_count} 字符 ← {Path(file_path).name}")
    return docs


def load_document(file_path: str) -> list[Document]:
    """
    统一文档加载入口。

    根据文件扩展名自动选择加载器：
        .pdf            → PyPDFLoader（文字层提取）
        .md / .markdown → TextLoader（纯文本读取）

    Args:
        file_path: 文档本地路径

    Returns:
        list[Document]

    Raises:
        ValueError: 不支持的文件类型
        FileNotFoundError: 文件不存在
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在：{file_path}")
    ext = path.suffix.lower()
    if ext == ".pdf":
        return load_pdf(file_path)
    elif ext in (".md", ".markdown"):
        return load_markdown(file_path)
    else:
        raise ValueError(
            f"不支持的文件类型：{ext}\n"
            f"当前支持：.pdf / .md / .markdown\n"
            f"提示：可用 markitdown 将 Word/PPT 转换为 .md 后再导入"
        )


# ── Step 2：智能分块（5.3）─────────────────────────────────────

def split_pdf_documents(pages: list[Document]) -> list[Document]:
    """PDF 文档分块：过滤空页 + RecursiveCharacterTextSplitter"""
    non_empty_pages = [p for p in pages if len(p.page_content.strip()) > 20]
    skipped = len(pages) - len(non_empty_pages)
    if skipped > 0:
        print(f"  过滤空页：{skipped} 页（图片/扫描件页）")

    chunks = _CHAR_SPLITTER.split_documents(non_empty_pages)

    for chunk in chunks:
        filename = Path(chunk.metadata.get("source", "未知文件")).stem
        page_num = chunk.metadata.get("page", 0) + 1
        chunk.metadata["source_name"] = f"{filename} 第{page_num}页"

    print(f"  [PDF] 分块完成：{len(non_empty_pages)} 页 → {len(chunks)} 个 chunk")
    return chunks


def split_markdown_documents(
    docs: list[Document],
    chunk_size: int = 1200,      # 代码类内容默认 1200，纯文字可调低到 600~800
    chunk_overlap: int = 100,
) -> list[Document]:
    """Markdown 文档分块：MarkdownHeaderTextSplitter + MarkdownTextSplitter 两阶段"""
    splitter = MarkdownTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    header_chunks: list[Document] = []
    for doc in docs:
        sections = _MD_HEADER_SPLITTER.split_text(doc.page_content)
        source_path = doc.metadata.get("source", "")
        for section in sections:
            section.metadata["source"] = source_path
        header_chunks.extend(sections)

    final_chunks = splitter.split_documents(header_chunks)

    for chunk in final_chunks:
        source_path = chunk.metadata.get("source", "")
        filename    = Path(source_path).stem if source_path else "未知文件"
        parts = [
            chunk.metadata.get("H1", ""),
            chunk.metadata.get("H2", ""),
            chunk.metadata.get("H3", ""),
            chunk.metadata.get("H4", ""),
        ]
        parts = [p for p in parts if p]
        chunk.metadata["source_name"] = (
            f"{filename} > {' > '.join(parts)}" if parts else filename
        )

    print(f"  [MD]  分块完成：{len(docs)} 个文件 → {len(final_chunks)} 个 chunk")
    return final_chunks


def split_documents(docs: list[Document], file_path: str) -> list[Document]:
    """统一分块入口，根据文件类型自动选择分块策略"""
    ext = Path(file_path).suffix.lower()
    if ext == ".pdf":
        return split_pdf_documents(docs)
    elif ext in (".md", ".markdown"):
        return split_markdown_documents(docs)
    else:
        raise ValueError(f"不支持的文件类型：{ext}")


# ── Step 2.5：Contextual RAG 上下文增强 ───────────────────────

async def generate_chunk_context(
    llm,
    document_text: str,
    chunk_content: str,
    semaphore: asyncio.Semaphore,
) -> str:
    """
    用 LLM 为单个 chunk 生成一句定位描述。

    失败时返回空字符串，调用方保留原始 chunk 文本（降级处理）。

    Args:
        llm:           DeepSeek LLM 实例（via get_llm）
        document_text: 整篇文档全文（截断至 8000 字）
        chunk_content: 当前 chunk 的原始文本
        semaphore:     并发限流（最多 MAX_CONTEXT_CONCURRENCY 个 LLM 请求同时进行）
    """
    async with semaphore:
        try:
            from langchain_core.messages import HumanMessage
            prompt = CONTEXTUAL_CHUNK_PROMPT.format(
                document_text=document_text,
                chunk_content=chunk_content,
            )
            resp = await llm.ainvoke([HumanMessage(content=prompt)])
            ctx = (
                resp.text
                if hasattr(resp, "text") and not callable(resp.text)
                else str(resp.content)
            ).strip()
            return ctx
        except Exception as e:
            print(f"   [warning] 上下文生成失败，保留原始 chunk：{e}")
            return ""


async def add_context(
    chunks: list[Document],
    docs: list[Document],
    concurrency: int = MAX_CONTEXT_CONCURRENCY,
) -> list[Document]:
    """
    Contextual RAG：并发为所有 chunk 生成上下文描述，拼接到 chunk 文本前方。

    拼接后格式：
        "<上下文描述一句话>\\n\\n<原始 chunk 文本>"

    拼接后再做嵌入（embed_chunks），向量同时编码"在哪里"和"说了什么"两层信息。

    Args:
        chunks:      split_documents() 输出的 list[Document]
        docs:        load_document() 输出的原始 list[Document]（用于构建全文参考）
        concurrency: 最大并发 LLM 请求数（默认 5，防止触发 API 限流）

    Returns:
        page_content 已被就地修改（拼接上下文）的 list[Document]
    """
    # 拼接全文供 LLM 参考（截断 8000 字，避免超出模型 context 长度）
    full_doc_text = "\n\n".join(d.page_content for d in docs)[:8000]

    llm       = get_llm("qa", temperature=0)
    semaphore = asyncio.Semaphore(concurrency)

    # 并发调用 LLM，为每个 chunk 生成上下文描述
    contexts = await asyncio.gather(*[
        generate_chunk_context(llm, full_doc_text, c.page_content, semaphore)
        for c in chunks
    ])

    enriched = 0
    for chunk, ctx in zip(chunks, contexts):
        if ctx:
            chunk.page_content = f"{ctx}\n\n{chunk.page_content}"
            enriched += 1

    print(f"  上下文增强完成：{enriched}/{len(chunks)} 个 chunk 已添加描述")
    return chunks


# ── Step 3：BGE-M3 嵌入（5.4）─────────────────────────────────

def embed_chunks(
    chunks: list[Document],
    course_id: str,
    document_id: str,
    tenant_id: str = "tenant_default",
    version: str = "1.0",
    resource_type: str = "knowledge",   # knowledge/lesson_plan/curriculum/paper/material
    subject: str = "",
    grade: str = "",
    source: str = "",
    content_id: str = "",
) -> list[DocumentChunk]:
    """
    对 split_documents() 产出的 chunk 列表做 BGE-M3 嵌入，返回 DocumentChunk 列表。

    BGE-M3 推理为 CPU / GPU-bound，按 BATCH_SIZE 批量处理：
    - 减少模型推理次数（每次推理有固定启动开销）
    - 控制显存/内存峰值（整批一次性推理会爆显存）

    Args:
        chunks:      split_documents() 返回的 list[Document]
        course_id:   所属课程 UUID
        document_id: 文档的 UUID（用于 Milvus 幂等更新，删旧插新）
        tenant_id:   租户 ID，用于 Milvus 多租户过滤
        version:     课程版本号

    Returns:
        list[DocumentChunk]，每项包含 dense + sparse 向量，可直接写入 Milvus
    """
    embedder = BGEMEmbedder.get_instance()   # 单例，首次调用加载模型
    all_doc_chunks: list[DocumentChunk] = []

    total = len(chunks)
    for batch_start in range(0, total, BATCH_SIZE):
        batch = chunks[batch_start: batch_start + BATCH_SIZE]
        texts = [c.page_content for c in batch]

        # BGE-M3 批量推理：同时拿到 dense 和 sparse
        dense_vecs, sparse_vecs = embedder.encode(texts, batch_size=BATCH_SIZE)

        for i, (chunk, dense, sparse) in enumerate(zip(batch, dense_vecs, sparse_vecs)):
            global_index = batch_start + i    # 在整个文档中的顺序编号

            all_doc_chunks.append(DocumentChunk(
                id=generate_chunk_id(chunk.page_content, document_id, global_index),
                content=chunk.page_content,
                embedding=dense,
                sparse_embedding=sparse,
                course_id=course_id,
                document_id=document_id,
                source_name=chunk.metadata.get("source_name", ""),
                chunk_type=chunk.metadata.get("chunk_type", "text"),
                chunk_index=global_index,
                version=version,
                tenant_id=tenant_id,
                resource_type=resource_type,
                subject=subject,
                grade=grade,
                source=source,
                content_id=content_id,
            ))

        done = min(batch_start + BATCH_SIZE, total)
        print(f"  嵌入进度：{done}/{total}")

    print(f"  嵌入完成：{len(all_doc_chunks)} 个 DocumentChunk")
    return all_doc_chunks


# ── Step 4：写入 Milvus ────────────────────────────────────────

def write_to_milvus(doc_chunks: list[DocumentChunk]) -> None:
    """
    将 embed_chunks() 产出的 DocumentChunk 列表写入 Milvus。

    先按 document_id 删除同文档旧版本 chunk，再批量 upsert，
    保证文档更新时不残留旧数据。
    """
    if not doc_chunks:
        print("  ⚠️  无 chunk 可写入，跳过")
        return

    kb          = KnowledgeBaseClient()
    document_id = doc_chunks[0].document_id

    print(f"  🗑️  删除旧版本 chunk（document_id={document_id[:8]}…）")
    kb.delete_document_chunks(document_id)

    written = kb.upsert_chunks(doc_chunks)
    print(f"  ✅ 写入完成：{written} 个 chunk → knowledge_domain")


# ── 主流水线 ─────────────────────────────────────────────────

async def build_pipeline(
    file_path:   str,
    course_id:   str,
    document_id: str,
    tenant_id:   str = "tenant_default",
    version:     str = "1.0",
    use_context: bool = True,
    resource_type: str = "knowledge",   # knowledge/lesson_plan/curriculum/paper/material
    subject: str = "",
    grade: str = "",
    source: str = "",
    content_id: str = "",
) -> None:
    """
    知识库建库完整流水线（五步）：

      Step 1   读取文档（PyPDFLoader / TextLoader）
      Step 2   智能分块（MarkdownHeaderTextSplitter / RecursiveCharacterTextSplitter）
      Step 2.5 Contextual RAG 上下文增强（LLM 并发，可跳过）
      Step 3   BGE-M3 嵌入（dense + sparse 双向量）
      Step 4   写入 Milvus（MilvusClient upsert）
    """
    print(f"\n{'='*55}")
    print(f" TeachMate 知识库构建")
    print(f" 文件      ：{file_path}")
    print(f" 课程      ：{course_id}")
    print(f" 文档 ID   ：{document_id}")
    print(f" 租户      ：{tenant_id}")
    print(f" Contextual RAG：{'启用' if use_context else '跳过（--no-context）'}")
    print(f"{'='*55}\n")

    # Step 1：读取
    print("📖 Step 1/4  读取文档…")
    docs = load_document(file_path)

    # Step 2：分块
    print("\n✂️  Step 2/4  智能分块…")
    chunks = split_documents(docs, file_path)

    # Step 2.5：Contextual RAG（可选）
    if use_context and chunks:
        print(f"\n🧠 Step 2.5  Contextual RAG 上下文增强"
              f"（并发={MAX_CONTEXT_CONCURRENCY}）…")
        chunks = await add_context(chunks, docs)

    # Step 3：嵌入
    print("\n🔢 Step 3/4  BGE-M3 嵌入…")
    doc_chunks = embed_chunks(
        chunks,
        course_id=course_id,
        document_id=document_id,
        tenant_id=tenant_id,
        version=version,
        resource_type=resource_type,
        subject=subject,
        grade=grade,
        source=source,
        content_id=content_id,
    )

    # Step 4：写入
    print("\n💾 Step 4/4  写入 Milvus…")
    write_to_milvus(doc_chunks)

    print(f"\n🎉 完成！共处理 {len(doc_chunks)} 个 chunk")
    print(f"   document_id = {document_id}")
    print(f"   ⚠️  更新此文档时请保留此 document_id")


# ── CLI 入口 ─────────────────────────────────────────────────
# 灌入 3 份中小学数学语料（教案/课标/论文），替换早期电商课程语料。
# 运行前先重跑 scripts/init_milvus.py 清空旧集合（drop 重建）。
# 运行：python scripts/build_knowledge_base.py

async def _build_all() -> None:
    """依次建库：小学数学 3 份 + 高中数学 5 份（课标/论文按年级复用）+ 理化 2 份，同一事件循环内完成（BGEMEmbedder 单例复用）。"""
    COURSE_K12  = "4f0a2b1c-0000-4000-8000-000000000001"   # 小学（六年级）课程
    COURSE_HIGH = "4f0a2b1c-0000-4000-8000-000000000002"   # 高中课程
    TENANT_ID   = "tenant_default"
    VERSION     = "1.0"
    USE_CONTEXT = True          # False = 跳过 Contextual RAG（快速调试，不消耗 API 配额）

    # 语料：每份独立 document_id + resource_type，备课按 resource_type 分组召回；
    # 检索严格按 subject+grade 精确匹配，故课标/论文等跨年级资源按年级复用灌入（grade 不同）。
    DOCS = [
        # ── 小学数学（原有）──
        {
            "file":          "./samples/lesson_plan_sample.md",
            "resource_type": "lesson_plan",
            "subject":       "数学",
            "grade":         "六年级",
            "source":        "圆的周长教案样例",
            "content_id":    "",
            "course_id":     COURSE_K12,
        },
        {
            "file":          "./samples/curriculum_sample.md",
            "resource_type": "curriculum",
            "subject":       "数学",
            "grade":         "六年级",
            "source":        "义务教育数学课程标准2022·图形与几何（第三学段）",
            "content_id":    "",
            "course_id":     COURSE_K12,
        },
        {
            "file":          "./samples/paper_sample.md",
            "resource_type": "paper",
            "subject":       "数学",
            "grade":         "六年级",
            "source":        "数形结合思想在分数概念教学中的运用（论文节选）",
            "content_id":    "",
            "course_id":     COURSE_K12,
        },
        # ── 高中数学教案（高一/高二/高三）──
        {
            "file":          "./samples/high_math_lesson_function.md",
            "resource_type": "lesson_plan",
            "subject":       "数学",
            "grade":         "高一",
            "source":        "函数的概念与性质教案（高一数学）",
            "content_id":    "",
            "course_id":     COURSE_HIGH,
        },
        {
            "file":          "./samples/high_math_lesson_conic.md",
            "resource_type": "lesson_plan",
            "subject":       "数学",
            "grade":         "高二",
            "source":        "椭圆及其标准方程教案（高二数学）",
            "content_id":    "",
            "course_id":     COURSE_HIGH,
        },
        {
            "file":          "./samples/high_math_lesson_probability.md",
            "resource_type": "lesson_plan",
            "subject":       "数学",
            "grade":         "高三",
            "source":        "随机变量及其分布教案（高三数学）",
            "content_id":    "",
            "course_id":     COURSE_HIGH,
        },
        # ── 高中数学课标（跨年级资源，单份灌入，grade=高中 表通用）──
        {
            "file":          "./samples/high_math_curriculum.md",
            "resource_type": "curriculum",
            "subject":       "数学",
            "grade":         "高中",
            "source":        "普通高中数学课程标准2017版2020修订·主线与核心素养",
            "content_id":    "",
            "course_id":     COURSE_HIGH,
        },
        # ── 高中数学论文（跨年级资源，单份灌入，grade=高中 表通用）──
        {
            "file":          "./samples/high_math_paper.md",
            "resource_type": "paper",
            "subject":       "数学",
            "grade":         "高中",
            "source":        "数形结合思想在高中数学函数教学中的应用（论文节选）",
            "content_id":    "",
            "course_id":     COURSE_HIGH,
        },
        # ── 少量其他学科（高一）──
        {
            "file":          "./samples/high_physics_lesson_newton.md",
            "resource_type": "lesson_plan",
            "subject":       "物理",
            "grade":         "高一",
            "source":        "牛顿第二定律教案（高一物理）",
            "content_id":    "",
            "course_id":     COURSE_HIGH,
        },
        {
            "file":          "./samples/high_chemistry_lesson_redox.md",
            "resource_type": "lesson_plan",
            "subject":       "化学",
            "grade":         "高一",
            "source":        "氧化还原反应教案（高一化学）",
            "content_id":    "",
            "course_id":     COURSE_HIGH,
        },
        # ── 主科空白补齐 + 理化延展（高二）──
        {
            "file":          "./samples/high_chinese_lesson_quanxue.md",
            "resource_type": "lesson_plan",
            "subject":       "语文",
            "grade":         "高一",
            "source":        "《劝学》教案（高一语文）",
            "content_id":    "",
            "course_id":     COURSE_HIGH,
        },
        {
            "file":          "./samples/high_english_lesson_attributive_clause.md",
            "resource_type": "lesson_plan",
            "subject":       "英语",
            "grade":         "高一",
            "source":        "定语从句教案（高一英语）",
            "content_id":    "",
            "course_id":     COURSE_HIGH,
        },
        {
            "file":          "./samples/high_physics_lesson_momentum.md",
            "resource_type": "lesson_plan",
            "subject":       "物理",
            "grade":         "高二",
            "source":        "动量守恒定律教案（高二物理）",
            "content_id":    "",
            "course_id":     COURSE_HIGH,
        },
        {
            "file":          "./samples/high_chemistry_lesson_equilibrium.md",
            "resource_type": "lesson_plan",
            "subject":       "化学",
            "grade":         "高二",
            "source":        "化学平衡教案（高二化学）",
            "content_id":    "",
            "course_id":     COURSE_HIGH,
        },
    ]

    for doc in DOCS:
        await build_pipeline(
            file_path=doc["file"],
            course_id=doc["course_id"],
            document_id=str(uuid.uuid4()),   # 每份语料独立 document_id
            tenant_id=TENANT_ID,
            version=VERSION,
            use_context=USE_CONTEXT,
            resource_type=doc["resource_type"],
            subject=doc["subject"],
            grade=doc["grade"],
            source=doc["source"],
            content_id=doc["content_id"],
        )


if __name__ == "__main__":
    asyncio.run(_build_all())
