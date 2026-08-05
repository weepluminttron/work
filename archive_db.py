import sqlite3
import os
import time
from typing import Optional, List, Dict
import config

# 数据库连接封装，线程安全简易封装
DB_FILE = config.ARCHIVE_DB_PATH

def get_db_conn():
    conn = sqlite3.connect(DB_FILE, timeout=20.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # 多线程并发时避免直接报 "database is locked"
    conn.execute("PRAGMA busy_timeout=20000")
    return conn

def init_db():
    """初始化归档数据表，自动兼容旧表，缺失字段自动新增（方案B核心）"""
    conn = get_db_conn()
    cursor = conn.cursor()

    # 1. 创建表（不存在才新建）
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS document_archive (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject TEXT NOT NULL,
        keypoint TEXT NOT NULL,
        filename TEXT NOT NULL,
        save_path TEXT NOT NULL,
        file_text TEXT,
        create_ts REAL NOT NULL
    )
    ''')
    conn.commit()

    # 2. 检查表字段，缺少file_text自动添加（兼容已有旧数据库）
    cols = cursor.execute("PRAGMA table_info(document_archive);").fetchall()
    col_names = [c[1] for c in cols]
    if "file_text" not in col_names:
        print("⚠️检测到旧数据库，自动新增 file_text 字段")
        cursor.execute("ALTER TABLE document_archive ADD COLUMN file_text TEXT;")
        conn.commit()

    conn.close()
    print("✅归档数据库初始化完成")

def archive_file(subject: str, keypoint: str, file_bytes: bytes, original_filename: str, doc_text: str = "") -> tuple[str, int]:
    """
    保存PDF文件到本地study_docs目录，并写入数据库
    :param subject: 科目
    :param keypoint: 知识点/文档名称
    :param file_bytes: 文件二进制
    :param original_filename: 原始文件名
    :param doc_text: 文档提取文本（存入file_text，供/test id出题）
    :return: (文件存储路径, 新增记录id)
    """
    # 安全文件名处理
    bad_chars = ['\\', '/', ':', '*', '?', '"', '<', '>', '|']
    safe_name = original_filename
    for c in bad_chars:
        safe_name = safe_name.replace(c, "_")

    # 构建存储路径
    subject_dir = os.path.join(config.BASE_DOC_DIR, subject)
    os.makedirs(subject_dir, exist_ok=True)
    save_full_path = os.path.join(subject_dir, safe_name)

    # 写入文件
    with open(save_full_path, "wb") as f:
        f.write(file_bytes)

    # 入库（新增file_text字段）
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute('''
    INSERT INTO document_archive (subject, keypoint, filename, save_path, file_text, create_ts)
    VALUES (?, ?, ?, ?, ?, ?)
    ''', (subject, keypoint, safe_name, save_full_path, doc_text, time.time()))
    new_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return save_full_path, new_id

def create_merged_archive(subject: str, keypoint: str, filename: str, merged_text: str) -> int:
    """创建合并归档记录（不保存实体文件），返回新记录ID"""
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute('''
    INSERT INTO document_archive (subject, keypoint, filename, save_path, file_text, create_ts)
    VALUES (?, ?, ?, ?, ?, ?)
    ''', (subject, keypoint, filename, "", merged_text, time.time()))
    new_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return new_id

def delete_archive_record_by_id(archive_id: int) -> bool:
    """只删除数据库记录（保留磁盘文件），供合并归档使用"""
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM document_archive WHERE id = ?", (archive_id,))
    conn.commit()
    affected = cursor.rowcount
    conn.close()
    return affected > 0

def merge_subject_archives(subject: str) -> dict:
    """把同科目的所有归档合并为一条记录，并彻底清理原记录与原文件"""
    docs = query_by_subject(subject)
    if not docs:
        return {"ok": False, "error": f"没有找到科目「{subject}」的归档文档"}
    parts = []
    for d in docs:
        t = (d.get("file_text") or "").strip()
        parts.append(f"【{d['filename']}】\n{t}")
    merged_text = "\n\n==========\n\n".join(parts)[:20000]
    new_id = create_merged_archive(
        subject,
        f"合并归档（{len(docs)}份文档）",
        f"【合并】{subject}.txt",
        merged_text
    )
    old_ids = [d["id"] for d in docs]
    for d in docs:
        sp = d.get("save_path") or ""
        if sp and os.path.exists(sp):
            try:
                os.remove(sp)
            except Exception as e:
                print(f"⚠️合并时删除原文件失败 {sp}: {e}")
        delete_archive_record_by_id(d["id"])
    return {"ok": True, "new_id": new_id, "old_ids": old_ids, "count": len(docs), "subject": subject}

def get_archive_by_id(archive_id: int) -> Optional[Dict]:
    """【新增】根据归档ID查询单条记录，给 /test id xxx 使用"""
    conn = get_db_conn()
    cursor = conn.cursor()
    row = cursor.execute(
        "SELECT * FROM document_archive WHERE id = ?",
        (archive_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return dict(row)

def query_by_subject(subject: str) -> List[Dict]:
    """根据科目查询所有归档文档"""
    conn = get_db_conn()
    cursor = conn.cursor()
    rows = cursor.execute(
        "SELECT * FROM document_archive WHERE subject = ? ORDER BY create_ts DESC",
        (subject,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_all_archive_items() -> List[Dict]:
    """获取全部归档记录（供向量库重建等需要遍历的场景使用）"""
    conn = get_db_conn()
    cursor = conn.cursor()
    rows = cursor.execute(
        "SELECT * FROM document_archive ORDER BY create_ts ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_all_archive_summary() -> str:
    """生成汇总文本，给 /tip 指令调用
    展示连续视觉序号，按归档时间升序；删除指令使用真实ID
    """
    conn = get_db_conn()
    cursor = conn.cursor()
    # 一次查询取回全部记录，按科目+归档时间排序，避免逐科目重复查询
    rows = cursor.execute("""
        SELECT id, subject, filename, keypoint, create_ts
        FROM document_archive
        ORDER BY subject ASC, create_ts ASC
    """).fetchall()
    conn.close()
    if not rows:
        return "📂暂无归档资料"
    output = "📚已归档科目清单：\n💡【[]内为展示序号，删除请使用真实ID：/del id 数字】\n"
    current_subject = None
    show_index = 1
    for r in rows:
        if r["subject"] != current_subject:
            output += f"\n【{r['subject']}】\n"
            current_subject = r["subject"]
            show_index = 1
        output += f" • [{show_index}] 真实ID:{r['id']} {r['filename']} | {r['keypoint']}\n"
        show_index += 1
    return output

def delete_archive_file(subject: str, keypoint: str, filename: str) -> str:
    """
    删除归档文件 + 数据库记录
    仅匹配 subject + filename，忽略keypoint参数
    返回操作结果文本
    """
    conn = get_db_conn()
    cursor = conn.cursor()
    # 查询条件：科目 + 文件名，不再匹配keypoint
    row = cursor.execute('''
    SELECT save_path, id FROM document_archive
    WHERE subject=? AND filename=?
    ''', (subject, filename)).fetchone()

    if not row:
        conn.close()
        return "❌未找到匹配归档记录，请核对科目、文件名"

    file_path = row["save_path"]
    record_id = row["id"]

    # 删除磁盘文件
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except Exception as e:
        conn.close()
        return f"⚠️数据库记录找到，但文件删除失败：{str(e)}"

    # 删除数据库记录
    cursor.execute("DELETE FROM document_archive WHERE id = ?", (record_id,))
    conn.commit()
    conn.close()
    return f"✅成功删除归档：{filename}"

def delete_archive_by_id(archive_id: int) -> Optional[str]:
    """按真实ID删除归档：删除磁盘文件 + 数据库记录
    返回被删除的文件名；记录不存在返回 None
    """
    conn = get_db_conn()
    cursor = conn.cursor()
    row = cursor.execute(
        "SELECT id, save_path, filename FROM document_archive WHERE id = ?",
        (archive_id,)
    ).fetchone()
    if not row:
        conn.close()
        return None
    file_path = row["save_path"]
    filename = row["filename"]
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except Exception as e:
        conn.close()
        raise e
    cursor.execute("DELETE FROM document_archive WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()
    return filename

def search_archive(keyword: str) -> List[Dict]:
    """简单搜索：匹配科目/知识点/文件名"""
    conn = get_db_conn()
    cursor = conn.cursor()
    like_str = f"%{keyword}%"
    rows = cursor.execute('''
    SELECT * FROM document_archive
    WHERE subject LIKE ? OR keypoint LIKE ? OR filename LIKE ?
    ORDER BY create_ts DESC
    ''', (like_str, like_str, like_str)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

if __name__ == "__main__":
    # 本地测试代码
    print(get_all_archive_summary())
