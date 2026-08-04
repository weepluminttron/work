import os
import sqlite3
import time
from pathlib import Path
import config

# 从配置读取数据库路径
DB_PATH = config.ARCHIVE_DB_PATH
BASE_DOC_DIR = Path(config.BASE_DOC_DIR)
BASE_DOC_DIR.mkdir(exist_ok=True)

# 初始化数据表
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # 归档文件表
    cur.execute('''
    CREATE TABLE IF NOT EXISTS archive (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject TEXT NOT NULL,        -- 科目
        knowledge_point TEXT NOT NULL, -- 知识点
        filename TEXT NOT NULL,
        save_path TEXT NOT NULL,
        upload_time REAL
    )
    ''')
    conn.commit()
    conn.close()

init_db()

# 保存文件到对应层级目录，并写入数据库
def archive_file(subject: str, knowledge_point: str, source_bytes: bytes, original_filename: str) -> str:
    """
    subject:科目名称
    knowledge_point:知识点
    source_bytes:pdf二进制内容
    original_filename:原始文件名
    return:保存路径
    """
    # 路径清洗，防止非法字符
    def clean_name(s: str):
        illegal = r'\/:*?"<>|'
        for c in illegal:
            s = s.replace(c, "_")
        return s.strip()

    subj_clean = clean_name(subject)
    kp_clean = clean_name(knowledge_point)
    file_clean = clean_name(original_filename)

    save_folder = BASE_DOC_DIR / subj_clean / kp_clean
    save_folder.mkdir(parents=True, exist_ok=True)
    full_path = save_folder / file_clean

    # 写入磁盘
    with open(full_path, "wb") as f:
        f.write(source_bytes)

    # 入库
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
    INSERT INTO archive(subject, knowledge_point, filename, save_path, upload_time)
    VALUES (?, ?, ?, ?, ?)
    ''', (subj_clean, kp_clean, original_filename, str(full_path), time.time()))
    conn.commit()
    conn.close()
    return str(full_path)

# 查询所有归档汇总
def get_all_archive_summary() -> str:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT subject, knowledge_point, filename FROM archive ORDER BY subject, knowledge_point")
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return "📂暂无归档资料！上传PDF后使用指令标注科目知识点完成归类。"

    # 组装树形文本
    tree = {}
    for subj, kp, fname in rows:
        if subj not in tree:
            tree[subj] = {}
        if kp not in tree[subj]:
            tree[subj][kp] = []
        tree[subj][kp].append(fname)

    output = "📚【已归档资料清单】\n"
    for subj, kp_dict in tree.items():
        output += f"\n▸ {subj}\n"
        for kp, filelist in kp_dict.items():
            output += f"  ▫ {kp}\n"
            for f in filelist:
                output += f"     - {f}\n"
    return output

# 根据科目查询文件
def get_subject_files(subject_name: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT knowledge_point, filename, save_path FROM archive WHERE subject = ?", (subject_name,))
    res = cur.fetchall()
    conn.close()
    return res

# =====================【新增：删除归档文件函数】=====================
def delete_archive_file(subject: str, knowledge_point: str, filename: str) -> str:
    """
    删除归档：同时删除磁盘文件 + SQLite数据库记录
    :param subject: 清洗后的科目名称
    :param knowledge_point: 清洗后的知识点名称
    :param filename: 原始文件名
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # 查询这条记录
    cur.execute('''
        SELECT id, save_path FROM archive
        WHERE subject = ? AND knowledge_point = ? AND filename = ?
    ''', (subject, knowledge_point, filename))
    row = cur.fetchone()

    if not row:
        conn.close()
        return f"❌找不到记录：{subject} → {knowledge_point} → {filename}"

    record_id, save_path = row
    file_path = Path(save_path)

    # 1. 删除磁盘文件
    try:
        if file_path.exists():
            file_path.unlink()
    except Exception as e:
        conn.close()
        raise Exception(f"磁盘文件删除失败：{str(e)}")

    # 2. 删除数据库记录
    cur.execute("DELETE FROM archive WHERE id = ?", (record_id,))
    conn.commit()
    conn.close()

    # 尝试清理空文件夹（逐级向上）
    try:
        kp_dir = file_path.parent
        subj_dir = kp_dir.parent
        if len(list(kp_dir.iterdir())) == 0:
            kp_dir.rmdir()
            if len(list(subj_dir.iterdir())) == 0:
                subj_dir.rmdir()
    except:
        pass

    return f"✅删除成功\n科目：{subject}\n知识点：{knowledge_point}\n文件：{filename}"
