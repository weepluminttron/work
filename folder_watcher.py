import os
import time
import threading
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from pypdf import PdfReader
import config
from llm_summary import generate_knowledge_points, llm_request

# 记录已经处理过的文件，避免重复触发
processed_files = dict()  # {filepath: timestamp} 用于过期清理
LOCK = threading.Lock()

# 允许处理的后缀
SUPPORT_SUFFIX = {".pdf"}
# 记录多久之后允许重新处理同一个文件（秒）
COOL_DOWN_SEC = 120

def extract_pdf_raw_text(file_path: str) -> str:
    """本地PDF提取文本，文件系统读取"""
    try:
        reader = PdfReader(file_path, strict=False)
        full_text = ""
        for page in reader.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n"
        return full_text.strip()
    except Exception as e:
        print(f"❌读取PDF失败 {file_path} | {str(e)}")
        return ""

def safe_filename(filename: str) -> str:
    """清理文件名，移除非法字符防止保存失败"""
    bad_chars = ['\\', '/', ':', '*', '?', '"', '<', '>', '|']
    for c in bad_chars:
        filename = filename.replace(c, "_")
    return filename

def auto_detect_subject(text: str, filename: str) -> str:
    """AI自动识别科目，替代直接使用文件名"""
    prompt = f"""
根据PDF文件名和文档开头内容，只输出科目名称，不要多余文字。
示例输出：操作系统、线性代数、计算机网络、德语A1
文件名：{filename}
文档片段：{text[:2000]}
"""
    res = llm_request(prompt).strip()
    if len(res) > 30 or len(res) < 2:
        return os.path.splitext(filename)[0]
    return res

def handle_new_pdf(file_path: str):
    """处理新增PDF主逻辑"""
    base_name = os.path.basename(file_path)
    print(f"\n📂检测到新文件：{base_name}")

    # 等待文件写入完成（防止复制中读取损坏文件）
    time.sleep(1.2)

    # 文件可能已经被移走/删除
    if not os.path.exists(file_path):
        print(f"⚠️文件已消失：{base_name}，跳过处理")
        return

    raw_text = extract_pdf_raw_text(file_path)
    if len(raw_text) < 30:
        print(f"⚠️{base_name} 文本过少，跳过生成笔记")
        return

    # AI识别科目
    subject_name = auto_detect_subject(raw_text, base_name)
    print(f"🤖识别科目：{subject_name}，开始整理笔记")
    md_content = generate_knowledge_points(raw_text, subject=subject_name)

    # 确保笔记目录存在
    os.makedirs(config.NOTE_SAVE_DIR, exist_ok=True)

    # 保存md文件
    md_filename = safe_filename(f"{subject_name}_{os.path.splitext(base_name)[0]}_笔记.md")
    save_path = os.path.join(config.NOTE_SAVE_DIR, md_filename)

    with open(save_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"✅笔记已归档：{save_path}")

    # 推送通知到飞书（启用请取消注释）
    try:
        from feishu_bot import send_msg
        notify_text = f"""📖【文件夹自动处理完成】
文件名：{base_name}
识别科目：{subject_name}
笔记保存路径：{save_path}
"""
        send_msg(config.ALLOW_OPEN_ID, notify_text)
    except Exception as e:
        print(f"ℹ️飞书消息推送跳过：{str(e)}")


class FileChangeHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        file_path = event.src_path
        ext = os.path.splitext(file_path)[1].lower()

        if ext not in SUPPORT_SUFFIX:
            return

        now = time.time()
        with LOCK:
            # 冷却时间内不重复处理
            last_run = processed_files.get(file_path, 0)
            if now - last_run < COOL_DOWN_SEC:
                return
            processed_files[file_path] = now

        # 子线程执行，不阻塞监听
        task_thread = threading.Thread(target=handle_new_pdf, args=(file_path,), daemon=True)
        task_thread.start()

def clean_stale_records():
    """后台定时清理过期文件记录，防止内存持续上涨"""
    while True:
        time.sleep(600)
        now = time.time()
        with LOCK:
            remove_list = []
            for path, ts in processed_files.items():
                if now - ts > COOL_DOWN_SEC * 3:
                    remove_list.append(path)
            for p in remove_list:
                del processed_files[p]
        if remove_list:
            print(f"🧹清理监听缓存记录 {len(remove_list)} 条")

def start_folder_watcher():
    # 启动缓存清理线程
    threading.Thread(target=clean_stale_records, daemon=True).start()

    # 先校验监控目录
    if not os.path.isdir(config.WATCH_FOLDER):
        print(f"❌监控目录不存在：{config.WATCH_FOLDER}")
        return

    observer = Observer()
    event_handler = FileChangeHandler()
    observer.schedule(
        event_handler,
        path=config.WATCH_FOLDER,
        recursive=False  # 需要监听子文件夹改为True
    )
    observer.start()
    print(f"\n👀文件夹监听已启动 | 监控目录：{config.WATCH_FOLDER}")
    print("💡将PDF放入目录，自动解析 → AI生成Markdown笔记 → 推送飞书通知")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    start_folder_watcher()
