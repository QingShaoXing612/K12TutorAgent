import hashlib
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from pymilvus import MilvusClient, AnnSearchRequest, WeightedRanker

from backend.config import get_settings
from backend.core.logger import get_logger

logger = get_logger(__name__)
backend_path = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

COLLECTION_NAME = "knowledge_domain"

# ──────────────────────────────────────────────────────────────
# BGE-M3 本地嵌入模型（进程内单例，dense + sparse 双输出）
# ──────────────────────────────────────────────────────────────

class BGEMEmbedder:
    """
    BGE-M3 本地嵌入模型单例。

    一次推理同时输出：
      - dense 向量（1024 维浮点数组，用于语义相似度检索）
      - sparse 向量（{token_id: weight} 字典，用于关键词精确检索）

    进程内单例：首次调用 get_instance() 时加载模型（约5-15秒），
    后续调用直接返回同一实例，不重复加载。

    用法：
        embedder = BGEMEmbedder.get_instance()
        dense, sparse = embedder.encode_query("圆的周长公式是什么？")
    """

    _instance: Optional["BGEMEmbedder"] = None   # 单例持有

    def __init__(self, model_path: str):
        # ── 兼容性补丁1：FlagEmbedding 1.3.x 依赖 transformers 内部函数 ──
        # transformers>=5.0 移除了 is_torch_fx_available，
        # 但当前锁定 transformers==4.51.0 不受影响。
        # 此补丁作为保险，避免未来升级时报 ImportError。
        import importlib.util as _ilu
        from transformers.utils import import_utils as _tf_iu
        if not hasattr(_tf_iu, "is_torch_fx_available"):
            _tf_iu.is_torch_fx_available = (
                lambda: _ilu.find_spec("torch.fx") is not None
            )

        # ── 兼容性补丁2：修复 XLMRobertaModel 不接受 dtype 参数的问题 ──
        # 某些 transformers 版本中 XLMRobertaModel.__init__() 不接受 dtype 关键字参数，
        # 但 FlagEmbedding 内部会传入该参数，导致 TypeError。
        # 通过子类覆盖，在调用父类前丢弃 dtype 参数。
        from transformers.models.xlm_roberta import modeling_xlm_roberta as _xlm
        _OriginalXLMRoberta = _xlm.XLMRobertaModel

        class _PatchedXLMRobertaModel(_OriginalXLMRoberta):
            def __init__(self, config, **kwargs):
                kwargs.pop("dtype", None)  # 丢弃 FlagEmbedding 传入的 dtype
                super().__init__(config, **kwargs)

        _xlm.XLMRobertaModel = _PatchedXLMRobertaModel

        import torch
        from FlagEmbedding import BGEM3FlagModel

        logger.info("bge_m3.loading", model_path=model_path)

        # ── fp16 仅在 CUDA 上启用，MPS（Apple M系列）不启用 ──
        # MPS 在 BGE-M3 attention 矩阵乘法上会触发 LLVM ERROR，
        # CPU 模式下用 fp32，速度稍慢但稳定。
        _use_fp16 = torch.cuda.is_available()

        self._model = BGEM3FlagModel(
            model_name_or_path=model_path,
            use_fp16=_use_fp16,
        )
        logger.info("bge_m3.loaded", use_fp16=_use_fp16)

    @classmethod
    def get_instance(cls) -> "BGEMEmbedder":
        """获取单例（首次调用时加载模型，后续复用）"""
        if cls._instance is None:
            bge3_path = os.path.join(backend_path, get_settings().bge_m3_model_path)
            cls._instance = BGEMEmbedder(bge3_path)
        return cls._instance

    def encode(
        self,
        texts: list[str],
        batch_size: int = 12,
    ) -> tuple[list[list[float]], list[dict]]:
        """
        批量编码文本，同时返回 dense 和 sparse 两种向量。

        Args:
            texts:      待编码的文本列表
            batch_size: 单次推理批大小，越大速度越快但显存占用越多；
                        12 是 16GB 显存 / 统一内存下的经验值

        Returns:
            (dense_vecs, sparse_vecs)
              dense_vecs:  list of 1024-dim float 向量，每项对应 texts[i]
              sparse_vecs: list of {token_id: weight} 字典，每项对应 texts[i]
        """
        output = self._model.encode(
            texts,
            batch_size=batch_size,
            max_length=8192,            # BGE-M3 支持最长 8192 token，覆盖大多数 chunk
            return_dense=True,          # 输出稠密语义向量
            return_sparse=True,         # 输出稀疏关键词向量
            return_colbert_vecs=False,  # ColBERT 多向量表示，本项目不用
        )

        dense_vecs = output["dense_vecs"].tolist()   # numpy → Python list

        # sparse: numpy.float16 → Python float
        # 必须转换！LangGraph MemorySaver 用 msgpack 序列化 State，
        # msgpack 不支持 numpy.float16，会在运行时抛 TypeError。
        sparse_vecs = [
            {int(k): float(v) for k, v in d.items()}
            for d in output["lexical_weights"]
        ]

        return dense_vecs, sparse_vecs

    def encode_query(self, text: str) -> tuple[list[float], dict]:
        """
        编码单条查询，返回 (dense_vec, sparse_vec)。

        查询时调用此方法（而非 encode），batch_size=1 避免不必要的 padding。

        Returns:
            (dense_vec, sparse_vec)
              dense_vec:  1024-dim float 列表
              sparse_vec: {token_id: weight} 字典
        """
        dense_list, sparse_list = self.encode([text], batch_size=1)
        return dense_list[0], sparse_list[0]


# ──────────────────────────────────────────────────────────────
# 数据类：DocumentChunk（建库写入 Milvus 的数据结构）
# ──────────────────────────────────────────────────────────────

@dataclass
class DocumentChunk:
    """
    准备写入 Milvus 的单个文档块，字段与 Milvus Schema 一一对应。

    id:               全局唯一 ID（MD5 of content + document_id + chunk_index）
    content:          chunk 文本（Contextual RAG 模式下含 LLM 生成的上下文描述前缀）
    embedding:        Dense 向量（BGE-M3，1024 维）
    sparse_embedding: Sparse 向量（{token_id: weight}，BGE-M3 lexical weights）
    source_name:      来源标注（检索结果展示用，如 "圆的周长教案 > 板书设计"）
    """
    id:               str
    content:          str
    embedding:        list[float]
    sparse_embedding: dict
    course_id:        str
    document_id:      str
    source_name:      str
    chunk_type:       str                  # "text" / "code" / "table"
    chunk_index:      int
    version:          str
    tenant_id:        str = "tenant_default"
    updated_at:       int = field(default_factory=lambda: int(time.time()))
    # 教学素材资源类型（同集合扩字段，跨类型召回 + 按类型过滤）
    resource_type:    str = "knowledge"    # knowledge/lesson_plan/curriculum/paper/material
    subject:          str = ""             # 学科
    grade:            str = ""             # 年级
    source:           str = ""             # 素材来源
    content_id:       str = ""             # 关联外部主键（可选）


# ──────────────────────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────────────────────

def generate_chunk_id(content: str, document_id: str, chunk_index: int) -> str:
    """
    生成 chunk 全局唯一 ID（MD5 散列）。

    用 document_id + chunk_index + content 前缀组合，确保：
    - 同一文档不同位置的 chunk 不冲突
    - 内容不变时 ID 稳定（幂等重建时不会重复插入）

    注意：此函数后续会作为 KnowledgeBaseClient 的静态方法重新出现（5.6），
    届时 build_knowledge_base.py 会改为调用 KnowledgeBaseClient.generate_chunk_id()。
    """
    raw = f"{document_id}_{chunk_index}_{content[:50]}"
    return hashlib.md5(raw.encode()).hexdigest()

class KnowledgeBaseClient:
    """
    Milvus 知识库客户端（MilvusClient 版）。

    单 Collection 设计（knowledge_domain），按 tenant_id 字段过滤实现多租户隔离。
    本节实现写入方法，5.6 节追加检索方法。

    单例连接：_client 是类变量，整个进程只创建一次 MilvusClient 连接。
    """

    _client: Optional["MilvusClient"] = None
    _loaded: bool = False

    # ── 检索配置 ─────────────────────────────────────────────
    ANN_EF = 64       # HNSW 搜索时的候选集大小（精度/速度平衡点）
    VECTOR_TOP_K = 10 # Hybrid 召回的候选数量，传给 Reranker 精排

    def __init__(self):
        if KnowledgeBaseClient._client is None:
            settings = get_settings()
            uri = f"http://{settings.milvus_host}:{settings.milvus_port}"
            KnowledgeBaseClient._client = MilvusClient(uri=uri)
            logger.info("milvus.connected", uri=uri)

        if not KnowledgeBaseClient._loaded:
            try:
                KnowledgeBaseClient._client.load_collection(COLLECTION_NAME)
            except Exception:
                pass   # init_milvus.py 尚未运行时忽略
            KnowledgeBaseClient._loaded = True

    # ── 写入：批量 Upsert ────────────────────────────────────

    def upsert_chunks(self, chunks: list) -> int:
        """
        批量写入文档块（Upsert：primary key 存在则更新，不存在则插入）。

        MilvusClient 行格式写入：每行一个 dict，key = 字段名，字段顺序无关。
        """
        if not chunks:
            return 0

        data = [
            {
                "id":               c.id,
                "embedding":        c.embedding,
                "sparse_embedding": c.sparse_embedding,
                "content":          c.content[:4096],
                "chunk_index":      c.chunk_index,
                "document_id":      c.document_id,
                "course_id":        c.course_id,
                "tenant_id":        c.tenant_id,
                "source_name":      c.source_name,
                "chunk_type":       c.chunk_type,
                "version":          c.version,
                "updated_at":       c.updated_at,
                "resource_type":    c.resource_type,
                "subject":          c.subject,
                "grade":            c.grade,
                "source":           c.source,
                "content_id":       c.content_id,
            }
            for c in chunks
        ]

        self._client.upsert(collection_name=COLLECTION_NAME, data=data)
        logger.info("knowledge_base.chunks_upserted", count=len(chunks))
        return len(chunks)

    # ── 写入：删除指定文档的所有 chunk ──────────────────────

    def delete_document_chunks(self, document_id: str) -> None:
        """
        删除指定文档的所有 chunk（文档更新时先删后插，幂等重建）。
        对 document_id 转义，防止 filter 表达式注入。
        """
        safe_id = document_id.replace('"', '\\"')
        self._client.delete(
            collection_name=COLLECTION_NAME,
            filter=f'document_id == "{safe_id}"',
        )
        logger.info("knowledge_base.document_deleted", document_id=document_id)

    # ── 工具方法 ─────────────────────────────────────────────

    @staticmethod
    def generate_chunk_id(content: str, document_id: str, chunk_index: int) -> str:
        """生成 chunk 唯一 ID（MD5）。内容+位置不变则 ID 不变，支持幂等 upsert。"""
        raw = f"{document_id}_{chunk_index}_{content[:50]}"
        return hashlib.md5(raw.encode()).hexdigest()

    # ── 检索：Hybrid（Dense + Sparse → WeightedRanker）─────

    def _hybrid_search(
            self,
            query_embedding: list[float],
            query_sparse: dict,
            top_k: int,
            filters: Optional[str] = None,
    ) -> list[dict]:
        """
        对 knowledge_domain 做 Hybrid 检索（Dense + Sparse → WeightedRanker 融合）。

        两个 AnnSearchRequest 分别构造 Dense 和 Sparse 检索请求，
        由 Milvus 在服务端并行执行后，用 WeightedRanker 加权融合排序。

        Args:
            query_embedding: Dense Query 向量（1024 维，来自 encode_query）
            query_sparse:    Sparse Query 向量（{token_id: weight}，来自 encode_query）
            top_k:           每路召回数量（融合后同样取 top_k）
            filters:         Milvus bool 表达式，如 'tenant_id == "xxx"'

        Returns:
            候选文档列表，每项含 "content" / "score" / "metadata"。
            score 是 WeightedRanker 的加权排序信号，不是概率，
            直接交给 5.7 节的 Reranker 做精细打分。
        """
        try:
            # ── Dense ANN 检索请求 ─────────────────────────────────────
            # COSINE 度量匹配 BGE-M3 dense 向量（L2 归一化后等价于余弦相似度）
            # ef=64：HNSW 搜索时的候选集大小，越大精度越高，64 是精度/速度平衡点
            dense_req = AnnSearchRequest(
                data=[query_embedding],
                anns_field="embedding",
                param={
                    "metric_type": "COSINE",
                    "params": {"ef": self.ANN_EF},
                },
                limit=top_k,
                expr=filters,
            )

            # ── Sparse 关键词检索请求 ──────────────────────────────────
            # IP（内积）是 BGE-M3 lexical_weights 的标准度量
            sparse_req = AnnSearchRequest(
                data=[query_sparse],
                anns_field="sparse_embedding",
                param={"metric_type": "IP"},
                limit=top_k,
                expr=filters,
            )

            output_fields = [
                "content", "source_name", "chunk_type",
                "course_id", "document_id", "chunk_index",
                "resource_type", "subject", "grade", "source", "content_id",
            ]

            # ── WeightedRanker(0.7, 0.3) ──────────────────────────────
            # 第一个权重对应第一个请求（Dense），第二个对应第二个请求（Sparse）
            # 两路结果在 Milvus 服务端并行检索，融合后返回
            results = self._client.hybrid_search(
                collection_name=COLLECTION_NAME,
                reqs=[dense_req, sparse_req],
                ranker=WeightedRanker(0.7, 0.3),
                limit=top_k,
                output_fields=output_fields,
            )

            # ── 解析结果 ───────────────────────────────────────────────
            # MilvusClient 的 hybrid_search 结果用 distance 字段存融合后的分数
            # 这个分数是排序信号，不是概率，不做任何额外处理，直接传给 Reranker
            candidates = []
            for hit in results[0]:
                candidates.append({
                    "content": hit["entity"].get("content") or "",
                    "score": hit.get("distance") or 0.0,
                    "metadata": {
                        "source_name": hit["entity"].get("source_name") or "",
                        "chunk_type": hit["entity"].get("chunk_type") or "text",
                        "course_id": hit["entity"].get("course_id") or "",
                        "document_id": hit["entity"].get("document_id") or "",
                        "chunk_index": hit["entity"].get("chunk_index") or 0,
                        "resource_type": hit["entity"].get("resource_type") or "",
                        "subject": hit["entity"].get("subject") or "",
                        "grade": hit["entity"].get("grade") or "",
                        "source": hit["entity"].get("source") or "",
                        "content_id": hit["entity"].get("content_id") or "",
                    },
                })

            logger.info(
                "knowledge_base.hybrid_search_done",
                candidates=len(candidates),
            )
            return candidates

        except Exception as e:
            logger.error("knowledge_base.hybrid_search_failed", error=str(e))
            return []

    @staticmethod
    def _build_filter(
        tenant_id: str,
        course_id: Optional[str] = None,
        resource_type: Optional[str] = None,
        subject: Optional[str] = None,
        grade: Optional[str] = None,
    ) -> str:
        """
        构建 Milvus bool 过滤表达式。
        对 tenant_id / course_id / resource_type / subject / grade 做转义，防止 filter 表达式注入。
        """
        safe_tenant = tenant_id.replace('"', '\\"')
        expr = f'tenant_id == "{safe_tenant}"'
        if course_id:
            safe_course = course_id.replace('"', '\\"')
            expr += f' and course_id == "{safe_course}"'
        if resource_type:
            safe_rt = resource_type.replace('"', '\\"')
            expr += f' and resource_type == "{safe_rt}"'
        if subject:
            safe_subject = subject.replace('"', '\\"')
            expr += f' and subject == "{safe_subject}"'
        if grade:
            safe_grade = grade.replace('"', '\\"')
            expr += f' and grade == "{safe_grade}"'
        return expr


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from dotenv import load_dotenv
    load_dotenv(".env.local")

    # ── 测试一：单条查询编码 ─────────────────────────────────
    embedder = BGEMEmbedder.get_instance()
    query = "圆的周长计算公式是什么？"
    dense, sparse = embedder.encode_query(query)
    print(f"dense 维度：{len(dense)}，sparse 词数：{len(sparse)}")
    print(f"dense 前5维：{dense[:5]}")
    print(f"sparse 前5项：{list(sparse.items())[:5]}")

    # ── 测试二：批量文本编码 ─────────────────────────────────
    texts = [
        "圆的周长计算公式是 C = πd = 2πr。",
        "数形结合借助面积模型和数轴模型帮助学生建立分数的直观。",
        "义务教育数学课程标准要求学生探索并掌握圆的周长和面积公式。",
    ]
    dense_vecs, sparse_vecs = embedder.encode(texts, batch_size=12)
    print(f"\n批量编码：{len(dense_vecs)} 条，每条 dense={len(dense_vecs[0])} 维")
    for i, (d, s) in enumerate(zip(dense_vecs, sparse_vecs)):
        print(f"  [{i}] sparse 词数：{len(s)}")

    # ── 测试三：混合检索（复用测试一的 dense/sparse）──────
    kb = KnowledgeBaseClient()
    candidates = kb._hybrid_search(
        query_embedding=dense,
        query_sparse=sparse,
        top_k=10,
        filters='tenant_id == "tenant_default"',
    )

    print(f"\n召回候选数：{len(candidates)}")
    for i, c in enumerate(candidates[:3]):
        print(f"\n[{i+1}] score={c['score']:.4f}  来源：{c['metadata']['source_name']}")
        print(f"     {c['content'][:80]}...")