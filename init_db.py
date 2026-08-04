import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "study_agent.db")
print(f"数据库路径：{DB_PATH}")

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# 创建文档归档表（包含file_text）
cur.execute('''
CREATE TABLE IF NOT EXISTS document_archive (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT,
    file_name TEXT,
    file_text TEXT,
    md_path TEXT,
    create_time TIMESTAMP
)
''')

# 复习记录表（间隔重复算法需要）
cur.execute('''
CREATE TABLE IF NOT EXISTS review_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    archive_id INTEGER,
    review_time TIMESTAMP,
    score INTEGER,
    FOREIGN KEY(archive_id) REFERENCES document_archive(id)
)
''')

conn.commit()
print("✅ 数据表创建成功")

# 打印表结构验证
print("\ndocument_archive 字段：")
for col in cur.execute("PRAGMA table_info(document_archive);").fetchall():
    print(col)

# 列出所有数据表
tables = cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print("\n数据库内所有表:", tables)

conn.close()
