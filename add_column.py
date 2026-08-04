# add_column.py
import os
import sqlite3

# 强制锁定和bot同一目录！不要使用相对路径歧义
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "study_agent.db")

print(f"正在操作数据库路径：{DB_PATH}")
conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

try:
    cur.execute('ALTER TABLE document_archive ADD COLUMN file_text TEXT;')
    conn.commit()
    print("✅ 字段 file_text 添加成功")
except Exception as e:
    print(f"提示：{e}")

# 查询表结构确认
res = cur.execute("PRAGMA table_info(document_archive);").fetchall()
print("\n当前数据表结构：")
for item in res:
    print(item)

conn.close()
