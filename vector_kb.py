import chromadb
from archive_db import get_all_archive_items, get_archive_by_id
import config
import re
import requests
import threading
import os

# ===================== 路径：向量库存放至云硬盘/data =====================
CHROMA_PATH = os.getenv("CHROMA_PATH", "/data/chroma_study_kb")
COLLECTION_NAME = "study_docs"
# 相似度阈值，低于该值不纳入上下文
SIM_THRESHOLD = 0.65

# ===================== 本地免费向量模型（无需硅基流动余额） =====================
# 按顺序尝试的本地多语言模型（不同 fastembed 版本支持情况不同，全部为384维）
LOCAL_EMBEDDING_MODELS = [
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "intfloat/multilingual-e5-small",
    "BAAI/bge-small-en-v1.5",
]
EMBEDDING_DIM = 384
_local_embedder = None
_rebuilding = False
_rebuild_lock = threading.Lock()

# 初始化chroma
chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
collection = chroma_client.get_or_create_collection(
    name=COLLECTION_NAME,
    metadata={"hnsw:space": "cosine"}
)


def _get_local_embedder():
    """加载本地多语言向量模型（首次使用需要下载模型文件）"""
    global _local_embedder
    if _local_embedder is None:
        # 国内服务器下载模型：走镜像站并禁用 Xet 协议（避免 401 下载失败）
        import os
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
        from fastembed import TextEmbedding
        last_err = ""
        for model_name in LOCAL_EMBEDDING_MODELS:
            print(f"⏳尝试加载本地向量模型：{model_name}")
            try:
                _local_embedder = TextEmbedding(model_name=model_name)
                print(f"✅本地向量模型加载完成：{model_name}")
                return _local_embedder
            except Exception as e:
                last_err = str(e)
                print(f"⚠️模型 {model_name} 加载失败：{e}")
                continue
        supported = [m.get("model") for m in TextEmbedding.list_supported_models()]
        print(f"❌所有候选模型都不可用，fastembed 支持列表：{supported}")
        print(f"最后错误：{last_err}")
        raise ValueError("本地向量模型不可用，请安装支持多语言的 fastembed 版本")
    return _local_embedder


def _ensure_collection_dimension():
    """旧版向量库是硅基流动 bge-m3（1024维），本地模型是384维，维度不一致时自动重建空库"""
    global collection
    try:
        sample = collection.get(limit=1, include=["embeddings"])
        embeds = sample.get("embeddings")
        if embeds is None:
            embeds = []
        elif not isinstance(embeds, list):
            embeds = list(embeds)
        if embeds and len(embeds[0]) != EMBEDDING_DIM:
            print(f"⚠️检测到旧向量维度 {len(embeds[0])}，本地模型为 {EMBEDDING_DIM}，自动删除旧向量库")
            try:
                chroma_client.delete_collection(COLLECTION_NAME)
            except Exception:
                pass
            collection = chroma_client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"}
            )
    except Exception as e:
        print(f"⚠️检查向量库维度失败：{e}")


def _ensure_ready():
    """确保向量库与本地模型一致；为空且有归档时自动重建"""
    global collection
    _ensure_collection_dimension()
    try:
        if collection.count() > 0:
            return
    except Exception:
        return
    items = get_all_archive_items()
    if not items:
        return
    global _rebuilding
    with _rebuild_lock:
        if _rebuilding:
            return
        _rebuilding = True
        try:
            print("🔄检测到知识库为空，正在用本地向量模型重建（首次可能较慢）...")
            rebuild_kb()
        finally:
            _rebuilding = False


# 【自研文本分割，完全替代RecursiveCharacterTextSplitter】
def split_text_custom(text: str, chunk_size=600, chunk_overlap=100):
    separators = ["\n\n", "\n", "。", "！", "？", "，"]
    chunks = []
    start = 0
    text_len = len(text)
    while start < text_len:
        end = min(start + chunk_size, text_len)
        cut_pos = end
        # 就近寻找分隔符
        for sep in separators:
            pos = text.rfind(sep, start, end)
            if pos > start:
                cut_pos = pos + len(sep)
                break
        chunk = text[start:cut_pos].strip()
        if chunk:
            chunks.append(chunk)
        # 修复：保证每次循环都向前推进，避免短文档/无合适分隔符时死循环
        next_start = cut_pos - chunk_overlap
        if next_start <= start:
            next_start = cut_pos
        start = next_start
    return chunks

def clean_text(txt: str) -> str:
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip()

# 【调用本地多语言向量模型生成向量（免费，不依赖硅基流动余额）】
def get_embedding(text_list: list[str]) -> list[list[float]]:
    embedder = _get_local_embedder()
    vecs = list(embedder.embed(text_list))
    return [v.tolist() for v in vecs]


def get_query_embedding(query: str) -> list[float]:
    """生成单个查询向量（与文档向量同一模型）"""
    embedder = _get_local_embedder()
    if hasattr(embedder, "query_embed"):
        vec = embedder.query_embed(query)
        return vec.tolist()
    vecs = list(embedder.embed([query]))
    return vecs[0].tolist()

# 重建整个知识库（初始化使用）
def rebuild_kb():
    print("🔄 重建本地知识库...")
    global collection
    try:
        chroma_client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    collection = chroma_client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"}
    )
    # 获取全部归档记录（按归档时间升序）
    summary_list = get_all_archive_items()
    count = 0
    for item in summary_list:
        # 适配你的summary结构，假设item为字典，包含id
        if not isinstance(item, dict):
            continue
        aid = item.get("id")
        if aid is None:
            continue
            
        row = get_archive_by_id(aid)
        if not row:
            continue
        doc_text = row["file_text"]
        subject = row["subject"]
        filename = row["filename"]
        if len(doc_text.strip()) < 50:
            continue
        chunks = split_text_custom(clean_text(doc_text))
        if not chunks:
            continue
        ids = [f"{aid}_{i}" for i in range(len(chunks))]
        metadatas = [
            {"archive_id": aid, "subject": subject, "filename": filename}
            for _ in chunks
        ]
        embeds = get_embedding(chunks)
        collection.upsert(
            embeddings=embeds,
            documents=chunks,
            metadatas=metadatas,
            ids=ids
        )
        count += len(chunks)
    print(f"✅知识库重建完成，共载入 {count} 文本片段")

# 新增单篇文档进入向量库（上传PDF归档时自动调用）
def add_archive_to_kb(archive_id: int):
    _ensure_ready()
    row = get_archive_by_id(archive_id)
    if not row:
        return
    doc_text = row["file_text"]
    subject = row["subject"]
    filename = row["filename"]
    clean_txt = clean_text(doc_text)
    if len(clean_txt) < 50:
        print(f"⚠️归档ID {archive_id} 文本过短，跳过入库")
        return
    chunks = split_text_custom(clean_txt)
    ids = [f"{archive_id}_{i}" for i in range(len(chunks))]
    metadatas = [
        {"archive_id": archive_id, "subject": subject, "filename": filename}
        for _ in chunks
    ]
    embeds = get_embedding(chunks)
    collection.upsert(embeddings=embeds, documents=chunks, metadatas=metadatas, ids=ids)
    print(f"📄归档ID {archive_id} 已加入向量知识库")

# 删除指定归档对应的向量片段（配套delete_archive_file调用）
def remove_archive_from_kb(archive_id: int):
    try:
        all_items = collection.get(
            where={"archive_id": archive_id}
        )
        del_ids = all_items.get("ids", [])
        if del_ids:
            collection.delete(ids=del_ids)
            print(f"🗑️归档ID {archive_id} 向量数据已清理")
    except Exception as e:
        print(f"❌清理向量库失败 archive_id={archive_id}: {str(e)}")

# 查询知识库，返回【过滤后】相关片段 + 相似度
def query_knowledge(query: str, top_k=4):
    try:
        _ensure_ready()
        query_emb = [get_query_embedding(query)]
        res = collection.query(
            query_embeddings=query_emb,
            n_results=top_k
        )
        documents = res["documents"][0]
        metadatas = res["metadatas"][0]
        distances = res["distances"][0]  # cos距离:越小越相似 0=完全相同
        sim_list = [1 - d for d in distances]

        # 按照相似度阈值过滤
        filtered = []
        for doc, meta, sim in zip(documents, metadatas, sim_list):
            if sim >= SIM_THRESHOLD:
                filtered.append({
                    "text": doc,
                    "meta": meta,
                    "similarity": sim
                })
        if not filtered:
            return {
                "chunks": [],
                "meta": [],
                "similarity": [],
                "max_sim": 0.0
            }
        # 重新拆分成原有返回格式，兼容上层调用
        chunks_out = [x["text"] for x in filtered]
        meta_out = [x["meta"] for x in filtered]
        sim_out = [x["similarity"] for x in filtered]
        return {
            "chunks": chunks_out,
            "meta": meta_out,
            "similarity": sim_out,
            "max_sim": max(sim_out)
        }
    except Exception as e:
        print(f"❌向量库查询异常: {str(e)}")
        return {
            "chunks": [],
            "meta": [],
            "similarity": [],
            "max_sim": 0.0
        }

# Tavily联网搜索
def web_search(query: str) -> str:
    url = "https://api.tavily.com/search"
    payload = {
        "api_key": config.TAVILY_API_KEY,
        "query": query,
        "search_depth": "basic",
        "max_results": 4
    }
    try:
        res = requests.post(url, json=payload, timeout=20)
        if res.status_code != 200:
            return ""
        data = res.json()
        parts = []
        for item in data.get("results", []):
            parts.append(f"【网页来源】{item['title']}\n{item['content']}")
        return "\n\n".join(parts)
    except Exception as e:
        print(f"联网搜索异常：{e}")
        return ""

# 构建RAG上下文
def build_rag_context(user_query: str) -> str:
    kb_result = query_knowledge(user_query)
    context_blocks = []
    # 本地归档文档片段
    for txt, meta in zip(kb_result["chunks"], kb_result["meta"]):
        fn = meta["filename"]
        subj = meta["subject"]
        aid = meta["archive_id"]
        context_blocks.append(f"【本地归档｜ID:{aid}｜{subj}｜{fn}】\n{txt}")

    # 本地没有足够相关资料，启用联网补充
    if len(kb_result["chunks"]) == 0 and getattr(config, "ENABLE_AUTO_WEB_SEARCH", True):
        web_data = web_search(user_query)
        if web_data:
            context_blocks.append("【互联网搜索资料】\n" + web_data)

    return "\n\n=====\n\n".join(context_blocks)

# 对外统一问答入口（给网页版/飞书 bot 调用，支持多轮记忆）
def rag_answer(user_question: str, history: list = None) -> str:
    context = build_rag_context(user_question)
    history_block = ""
    if history:
        lines = ["【最近对话】"]
        for item in history[-6:]:
            role = "用户" if item.get("role") == "user" else "助手"
            lines.append(f"{role}：{item.get('text', '')[:300]}")
        history_block = "\n".join(lines) + "\n\n"
    prompt = f"""
你是学习助理。优先使用提供的本地归档课程资料回答用户问题；
本地资料不足时结合互联网搜索内容；严禁编造不存在的知识点。
请结合【最近对话】理解用户的追问，回答要连贯。

{history_block}【参考资料】
{context}

用户问题：{user_question}
"""
    headers = {
        "Authorization": f"Bearer {config.LLM_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": config.LLM_MODEL,
        "messages": [
            {"role": "system", "content": "专业学习助手，回答条理清晰，基于参考资料作答。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.15
    }
    try:
        resp = requests.post(config.LLM_API_URL, json=payload, headers=headers, timeout=30)
        if resp.status_code != 200:
            return "大模型接口调用失败，请稍后重试。"
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"rag_answer异常：{e}")
        return "问答服务发生异常，请稍后重试。"
