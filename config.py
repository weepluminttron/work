import os

# ===================== 本地密钥读取（.env 文件，不入库） =====================
def _load_local_env():
    """读取 config.py 同目录下的 .env，把密钥载入环境变量（已存在的环境变量优先）。"""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value

_load_local_env()

# ===================== 网页版与二级密码 =====================
WEB_PASSWORD = os.getenv("WEB_PASSWORD", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")


def check_admin_password(pwd: str) -> bool:
    """二级密码校验：配置了 ADMIN_PASSWORD 用它；没配置则回退到网页登录密码"""
    if not pwd:
        return False
    if ADMIN_PASSWORD:
        return pwd == ADMIN_PASSWORD
    return bool(WEB_PASSWORD) and pwd == WEB_PASSWORD

# ===================== 本地文件监控配置（文件夹监听模块） =====================
WATCH_FOLDER = os.path.expanduser("~/Downloads/课程资料")
NOTE_SAVE_DIR = os.path.expanduser("~/study_notes")

# 兼容【软链接目录】的安全创建逻辑
def safe_mkdir(target_path):
    # 如果是软链接，直接跳过创建
    if os.path.islink(target_path):
        return
    try:
        os.makedirs(target_path, exist_ok=True)
    except FileExistsError:
        pass

safe_mkdir(WATCH_FOLDER)
safe_mkdir(NOTE_SAVE_DIR)

# ===================== DeepSeek LLM 大模型配置（对话保持不变） =====================
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_API_URL = "https://api.deepseek.com/v1/chat/completions"
LLM_BASE_URL = "https://api.deepseek.com/v1"
LLM_MODEL = "deepseek-chat"
MAX_LLM_CONTEXT = 24000   # LLM文本截断上限

# ===================== 硅基流动 Embedding向量接口 =====================
EMB_API_KEY = os.getenv("EMB_API_KEY", "")
EMB_BASE_URL = "https://api.siliconflow.cn/v1"
EMBEDDING_MODEL = "BAAI/bge-m3"

# ===================== Telegram机器人（预留通道） =====================
TG_BOT_TOKEN = "你的TG机器人token"
TG_USER_ID = 123456789

# ===================== FSRS间隔重复复习系统 =====================
REVIEW_PUSH_HOUR = int(os.getenv("REVIEW_PUSH_HOUR", "8"))  # 每日早上8点推送复习计划
MEMORY_DB_PATH = os.getenv("MEMORY_DB_PATH", "memory_spaced_review.db")

# ===================== 飞书机器人主交互渠道【已修复变量名】 =====================
FEISHU_APP_ID = os.getenv("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
ALLOW_OPEN_ID = "ou_0a29eb410979eb1eeb6504db33b7987c"
FLASK_PORT = 8080
# 定时任务推送复习计划webhook
FEISHU_WEBHOOK = "在此处填入你的飞书自定义机器人webhook链接"

# ===================== 全局缓存参数 =====================
CACHE_EXPIRE_SECONDS = 30 * 60    # 试题缓存30分钟过期
CLEAN_INTERVAL = 10 * 60          # 每10分钟清理一次缓存
MAX_WORKERS = 4                   # 线程池最大并发

# ===================== PDF归档存储配置（支持 Docker 环境变量覆盖） =====================
BASE_DOC_DIR = os.getenv("STUDY_DOCS_DIR", "./study_docs")
ARCHIVE_DB_PATH = os.getenv("ARCHIVE_DB_PATH", "archive.db")
os.makedirs(BASE_DOC_DIR, exist_ok=True)

# ===================== RAG本地知识库 + 联网搜索 =====================
# Chroma向量库路径（已经软链接指向/data，无需改动）
CHROMA_PATH = "./chroma_study_kb"
COLLECTION_NAME = "study_docs"
# RAG相似度阈值：低于阈值自动联网搜索
RAG_SIM_THRESHOLD = 0.65
ENABLE_AUTO_WEB_SEARCH = True
# Tavily联网搜索密钥
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
