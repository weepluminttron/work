# 学习助手（AI Study Agent）

一个自托管的 AI 智能学习伴学系统：归档资料、自测题、背诵卡片、学习计划、长期目标、错题本、AI 讲题、答题模式、技能市场、多用户、语音朗读、B站视频推荐，支持网页版 / PWA / Android APK / 飞书机器人。

---

## 功能特性

- 📂 **资料归档**：上传 PDF / Word / PPT / 图片 / TXT，支持拖拽上传、批量导入文本、图片 OCR、扫描版 PDF OCR
- 🧠 **RAG 知识库问答**：本地多语言向量模型，回答基于你自己的归档资料
- 📝 **自测题**：自动出题（20 题）、答题模式（答题卡/标记/保存/断点续做）、判断题/多选题/简答题 AI 批改
- 🤖 **AI 讲题**：点击答题卡题号即可讲解，支持多轮追问，基于 RAG 检索资料
- 📇 **背诵卡片 / 学习计划 / 每日任务 / 打卡 / 学情报告**
- 🎯 **长期学习目标**：`/goal 3年 德语 B2`，AI 生成阶段里程碑并跟踪进度
- 📕 **错题本**：自评/自动记录错题，支持复习与清除
- 🛍 **技能市场**：内置技能包 + AI 生成自定义技能包（前置概念/易错点/示例题）
- 📺 **B站视频推荐**：学习方案自动附视频链接，支持 `/视频 关键词`
- 🔐 **多用户系统**：注册/登录、用户数据隔离、管理员权限、自动登录/记住密码
- 🔊 **语音朗读**：输入栏 🔊 按钮、`/读 单词`、双击选中朗读，自动识别中/英/德语
- 📦 **多端部署**：网页版、可安装 PWA、Android APK（Capacitor 打包）、Docker
- 🔄 **自动更新检测**：服务器发布新版本后，网页/App 自动提示下载

---

## 技术栈

- 后端：Python + Flask + Gunicorn
- 数据库：SQLite（按用户隔离）
- 向量库：ChromaDB + fastembed（本地多语言模型，无需付费 API）
- OCR：RapidOCR（ONNX，稳定） / PaddleOCR（兜底）
- 前端：原生 HTML/CSS/JS（移动优先、支持多主题、PWA）
- App 打包：Capacitor（Android APK）
- 部署：Docker / 服务器脚本

---

## 目录结构

```
.
├── web_app.py           # 网页版后端（Flask）
├── feishu_bot.py        # 飞书机器人
├── feishu_commands.py   # 飞书指令注册表
├── study_service.py     # 网页版/飞书共用指令逻辑
├── llm_summary.py       # 大模型调用（DeepSeek）
├── vector_kb.py         # RAG 向量知识库
├── file_parser.py       # 文件解析 / OCR
├── archive_db.py        # 归档数据库
├── review_scheduler.py  # 学习计划/打卡/定时推送
├── conversation_store.py # 多会话存储
├── chat_memory.py       # 多轮对话记忆
├── wrong_book.py        # 错题本
├── quiz_logic.py        # 自测题批改逻辑
├── skill_market_store.py# 自定义技能存储
├── goal_store.py        # 长期目标存储
├── user_auth.py         # 用户注册/登录
├── user_context.py      # 用户数据隔离
├── video_search.py      # B站视频搜索
├── llm_cache.py         # LLM 结果缓存
├── web/                 # 网页前端
├── tests/               # 单元测试
├── tools/selfcheck.py   # 自检脚本
├── docker-compose.yml   # Docker 部署
├── capacitor.config.json# Android APK 配置
└── docs/APK.md          # APK 打包说明
```

---

## 快速开始

### 方式一：服务器脚本部署（推荐，现有环境）

1. 把部署包 `study_agent_latest.zip` 传到服务器并解压：

```bash
cd /data && unzip -o study_agent_latest.zip -d study_agent && cd study_agent
```

2. 创建 `.env` 配置文件（见下方配置说明）
3. 启动：

```bash
./start.sh
```

停止：

```bash
./stop.sh
```

日志：`logs/bot.log`（飞书）、`logs/web.log`（网页版）、`logs/scheduler.log`（定时任务）。

### 方式二：Docker 部署

```bash
cd D:\work
docker compose up -d --build
```

数据（数据库、归档、向量库、用户等）保存在 Docker 卷 `study-data` 和 `model-cache` 中。

### 方式三：本地开发

```bash
pip install -r requirements.txt
python web_app.py        # 网页版（8090）
python feishu_bot.py     # 飞书机器人（8080）
```

---

## 配置说明（.env）

| 变量 | 说明 | 必填 |
|---|---|---|
| `LLM_API_KEY` | DeepSeek API Key | ✅ |
| `EMB_API_KEY` | 硅基流动 API Key（向量/视觉接口） | ✅ |
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | 飞书机器人凭证 | 用飞书时必填 |
| `WEB_PASSWORD` | 网页登录密码 | ✅ |
| `ADMIN_PASSWORD` | 管理员二级密码（不设则用 WEB_PASSWORD） | 可选 |
| `ADMIN_USERNAME` | 管理员用户名（默认 `admin`） | 可选 |
| `TAVILY_API_KEY` | 联网搜索（可选） | 可选 |
| `BILI_SESSDATA` | B站登录 Cookie（中文关键词视频搜索更准） | 可选 |
| `WEB_SECRET_KEY` | 会话加密密钥 | 可选 |
| `APP_VERSION` / `APP_UPDATE_NOTE` | 自动更新版本号 | 可选 |

> 管理员密码以 `.env` 为准：每次启动自动同步，改 `.env` 重启即可重置。

---

## 常用指令

```
/help                指令清单
/list                归档清单
/today               今日任务
/report              学情诊断报告
/test id 3           自测题（可选 /test id 3 仅题目）
/submit id 3 B,A,C,D 提交答案自动批改
/explain id 3 1      AI 讲题
/cards id 3          背诵卡片
/plan id 3           学习计划
/daily id 3          每日任务
/done id 3 day 1     打卡
/goal 3年 德语 B2    长期目标
/wrong               错题本
/视频 德语语法        B站视频
/market 主题          AI 生成技能包
/读 单词             语音朗读
/password 旧密码 新密码
/restore 对话ID      恢复误删对话
```

---

## 移动端

- **PWA**：部署后 Chrome/Edge 打开网页 → 右上角 📲 安装（需 HTTPS 或 localhost）
- **APK**：见 [docs/APK.md](docs/APK.md)（GitHub Actions 自动打包）
- **自动更新**：服务器 `apk/` 目录放 APK，`.env` 设置 `APP_VERSION`，客户端自动提示

---

## 测试与自检

```bash
python -m unittest discover -s tests -v
python tools/selfcheck.py   # 编译 + 测试 + 未使用导入检查
```

---

## 常见问题

- **502 / 上传崩溃**：确认已部署最新代码，扫描版 PDF 走 RapidOCR；若仍出现，把 `logs/web.log` 发我
- **中文关键词视频搜不到**：在 `.env` 配置 `BILI_SESSDATA`
- **管理员登录不上**：在 `.env` 设置 `ADMIN_PASSWORD` 后重启
- **PWA 无法安装**：需要 HTTPS（或 localhost）

---

## 说明

- 本项目的学习数据默认保存在本地/服务器，请定期备份 `.env`、数据库与 `study_docs/`
- 学习资料版权归原作者所有，请仅用于个人学习
