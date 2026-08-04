import chromadb
from archive_db import get_all_archive_items, get_archive_by_id
import config
import re
import requests

# ===================== 路径：向量库存放至云硬盘/data =====================
CHROMA_PATH = "/data/chroma_study_kb"
COLLECTION_NAME = "study_docs"
# 相似度阈值，低于该值不纳入上下文
SIM_THRESHOLD = 0.65

# 初始化chroma
chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
collection = chroma_client.get_or_create_collection(
    name=COLLECTION_NAME,
    metadata={"hnsw:space": "cosine"}
)

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

# 【调用硅基流动云端生成向量，替代本地HuggingFace模型】
def get_embedding(text_list: list[str]) -> list[list[float]]:
    headers = {
        "Authorization": f"Bearer {config.EMB_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": config.EMBEDDING_MODEL,
        "input": text_list
    }
    resp = requests.post(f"{config.EMB_BASE_URL}/embeddings", headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    embeddings = [item["embedding"] for item in data["data"]]
    return embeddings

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
        collection.add(
            embeddings=embeds,
            documents=chunks,
            metadatas=metadatas,
            ids=ids
        )
        count += len(chunks)
    print(f"✅知识库重建完成，共载入 {count} 文本片段")

# 新增单篇文档进入向量库（上传PDF归档时自动调用）
def add_archive_to_kb(archive_id: int):
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
    collection.add(embeddings=embeds, documents=chunks, metadatas=metadatas, ids=ids)
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
        query_emb = get_embedding([query])
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

# 对外统一问答入口（给feishu bot调用）
def rag_answer(user_question: str) -> str:
    context = build_rag_context(user_question)
    prompt = f"""
你是学习助理。优先使用提供的本地归档课程资料回答用户问题；
本地资料不足时结合互联网搜索内容；严禁编造不存在的知识点。

【参考资料】
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
