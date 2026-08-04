import os
import time
import json
import datetime
import subprocess
from dotenv import load_dotenv
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import openai
import telebot
from scipy.special import expit

# 加载环境变量
load_dotenv()
openai.api_key = os.getenv("LLM_API_KEY")
openai.base_url = os.getenv("LLM_BASE_URL")

# 全局路径配置
WATCH_DIR = os.getenv("WATCH_FOLDER")
MD_DIR = os.getenv("MD_OUTPUT")
MEM_DIR = os.getenv("MEMORY_DB")
LOG_DIR = "/home/ubuntu/study_bot/logs"
TG_TOKEN = os.getenv("TG_BOT_TOKEN")
ADMIN_ID = int(os.getenv("TG_ADMIN_ID"))
MODEL = os.getenv("LLM_MODEL")

# 日志工具
def log(msg):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{now}] {msg}\n"
    with open(f"{LOG_DIR}/run.log", "a", encoding="utf-8") as f:
        f.write(line)
    print(line.strip())

# 1. PDF 文本提取
def pdf_to_text(pdf_path) -> str:
    try:
        result = subprocess.check_output(
            ["pdftotext", "-layout", "-nopgbrk", pdf_path, "-"],
            timeout=60
        )
        return result.decode("utf-8", errors="ignore")
    except Exception as e:
        log(f"PDF解析失败 {pdf_path}: {str(e)}")
        return ""

# 2. LLM 统一调用函数
def llm_chat(prompt: str) -> str:
    try:
        resp = openai.ChatCompletion.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )
        return resp["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log(f"大模型调用失败: {str(e)}")
        return ""

# 3. PDF自动处理：提取要点生成Markdown归档
def process_pdf(file_path):
    filename = os.path.basename(file_path)
    log(f"检测到新课件：{filename}")
    text = pdf_to_text(file_path)
    if len(text) < 100:
        log("PDF文本过短，跳过处理")
        return

    # 拆分科目、章节
    file_stem = filename.replace(".pdf", "")
    prompt_classify = f"""
下面是课件文件名和全文内容，输出JSON，字段：subject(科目), chapter(章节), core_theme(核心主题)
文件名：{file_stem}
课件内容片段：{text[:2000]}
只输出JSON，不要额外文字
"""
    classify_raw = llm_chat(prompt_classify)
    try:
        meta = json.loads(classify_raw)
        subject = meta["subject"]
        chapter = meta["chapter"]
    except:
        subject = file_stem.split("_")[0]
        chapter = file_stem.split("_")[1] if "_" in file_stem else "未知章节"

    # 生成结构化Markdown笔记
    prompt_md = f"""
你是专业学习助理，根据下面课件全文，生成完整Markdown学习笔记：
1. 一级标题：科目+章节名称
2. 二级标题：核心知识点、重难点总结、易混淆考点
3. 每条知识点简洁清晰，重点加粗
4. 最后生成3道基础自测题
课件全文：{text[:6000]}
"""
    md_content = llm_chat(prompt_md)
    save_name = f"{subject}_{chapter}.md"
    subject_folder = os.path.join(MD_DIR, subject)
    os.makedirs(subject_folder, exist_ok=True)
    md_path = os.path.join(subject_folder, save_name)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    log(f"笔记归档完成：{md_path}")

    # 写入间隔重复记忆库
    mem_path = os.path.join(MEM_DIR, f"{subject}_memory.json")
    mem_data = {}
    if os.path.exists(mem_path):
        with open(mem_path, "r", encoding="utf-8") as f:
            mem_data = json.load(f)
    mem_data[save_name] = {
        "last_review": str(datetime.date.today()),
        "interval": 1,
        "score": 2.5
    }
    with open(mem_path, "w", encoding="utf-8") as f:
        json.dump(mem_data, f, ensure_ascii=False, indent=2)

# 4. 文件监听事件类（监控PDF新增）
class PDFWatchHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        fp = event.src_path
        if fp.lower().endswith(".pdf"):
            time.sleep(2)
            process_pdf(fp)

# 5. 间隔重复算法：生成每日复习清单
def get_daily_review():
    today = datetime.date.today()
    review_list = []
    for fname in os.listdir(MEM_DIR):
        if not fname.endswith(".json"):
            continue
        subj = fname.replace("_memory.json", "")
        with open(os.path.join(MEM_DIR, fname), "r", encoding="utf-8") as f:
            mem = json.load(f)
        for note_name, data in mem.items():
            last_rev = datetime.date.fromisoformat(data["last_review"])
            interval = data["interval"]
            next_rev = last_rev + datetime.timedelta(days=interval)
            if next_rev <= today:
                review_list.append(f"【{subj}】{note_name} 间隔{interval}天需复习")
    if not review_list:
        return "今日无到期复习内容"
    return "今日复习清单：\n" + "\n".join(review_list)

# 6. 指令处理（TG机器人远程交互）
bot = telebot.TeleBot(TG_TOKEN)
@bot.message_handler(func=lambda m: m.from_user.id == ADMIN_ID)
def handle_command(msg):
    text = msg.text.strip()
    log(f"收到远程指令：{text}")
    # 指令1：生成章节自测题
    if "考我" in text and "章" in text:
        prompt_exam = f"""
根据下面学习笔记目录，提取对应科目章节内容，生成10道选择/简答自测题，附带答案
用户指令：{text}
笔记目录路径：{MD_DIR}
"""
        res = llm_chat(prompt_exam)
        bot.send_message(ADMIN_ID, res)
    # 指令2：查看今日复习计划
    elif "今日复习" in text or "复习建议" in text:
        rev = get_daily_review()
        bot.send_message(ADMIN_ID, rev)
    # 指令3：刷新全部PDF重新归档
    elif "重新解析全部课件" in text:
        files = [os.path.join(WATCH_DIR, f) for f in os.listdir(WATCH_DIR) if f.lower().endswith(".pdf")]
        bot.send_message(ADMIN_ID, f"开始批量处理{len(files)}个PDF...")
        for f in files:
            process_pdf(f)
        bot.send_message(ADMIN_ID, "全部课件重新归档完成")
    else:
        bot.send_message(ADMIN_ID, "可用指令示例：\n1.帮我把《操作系统》第5章总结10道题考我\n2.今日复习建议\n3.重新解析全部课件")

# 7. 定时早间推送复习提醒
def morning_reminder():
    while True:
        now = datetime.datetime.now()
        if now.hour == int(os.getenv("REMIND_HOUR")) and now.minute == 0:
            rev_text = get_daily_review()
            try:
                bot.send_message(ADMIN_ID, "【早间复习推送】\n" + rev_text)
                log("早间复习提醒推送完成")
            except Exception as e:
                log(f"推送失败：{str(e)}")
            time.sleep(3600)
        time.sleep(60)

# 主入口：监听+机器人+定时提醒多线程
if __name__ == "__main__":
    log("===== 数字学伴Agent启动成功 =====")
    # 启动文件监听
    observer = Observer()
    observer.schedule(PDFWatchHandler(), WATCH_DIR, recursive=False)
    observer.start()
    log(f"文件夹监听已开启：{WATCH_DIR}")

    # 后台启动定时推送线程
    import threading
    remind_thread = threading.Thread(target=morning_reminder, daemon=True)
    remind_thread.start()

    # 启动TG机器人
    bot_thread = threading.Thread(target=bot.polling, daemon=True)
    bot_thread.start()

    # 常驻主线程
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
