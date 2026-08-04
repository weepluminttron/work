import requests
import json
import time
from config import LLM_API_KEY, LLM_API_URL, LLM_MODEL

def llm_request(prompt: str, timeout: int = 30) -> str:
    """通用LLM请求，增加超时捕获、异常处理"""
    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3
    }
    try:
        resp = requests.post(LLM_API_URL, headers=headers, json=data, timeout=timeout)
        resp.raise_for_status()
        resp_json = resp.json()
        content = resp_json["choices"][0]["message"]["content"]
        return content.strip()
    except requests.exceptions.Timeout:
        return "❌大模型接口请求超时，请稍后重试"
    except Exception as e:
        print(f"LLM调用异常: {str(e)}")
        return f"❌LLM接口调用失败：{str(e)}"

def generate_knowledge_points(text: str, subject: str) -> str:
    prompt = f"""
你是专业课学习助手，处理{subject}课程文档，提取核心知识点，输出标准Markdown笔记：
1. 根据内容逻辑分小节整理，重要概念、公式使用**加粗**
2. 删除无关废话、冗余描述，只保留考点与核心原理
3. 公式采用单行LaTeX $...$ 格式
文档内容：
{text[:6000]}
"""
    return llm_request(prompt)

def generate_test_questions(text: str, subject: str, num: int = 10) -> tuple[str, str]:
    prompt = f"""
任务：基于下方{subject}学习资料生成{num}道自测习题，混合选择题+简答题。
⚠️严格输出规范，禁止额外开场白、总结！
第一部分：全部试题
分割标记：===QUESTION===
第二部分：标准答案+简要解析
分割标记：===ANSWER===

格式要求：
1. 数学公式使用 $表达式$，禁止多层反斜杠
2. 使用Markdown排版，条理清晰
3. 选择题附带A/B/C/D选项
4. 简答题答案附上思路解析

参考资料：
{text[:6000]}
"""
    full_output = llm_request(prompt)

    # 健壮分割逻辑
    if "===QUESTION===" in full_output and "===ANSWER===" in full_output:
        seg_q = full_output.split("===QUESTION===")[-1]
        q_part, a_part = seg_q.split("===ANSWER===")
        question_part = q_part.strip()
        answer_part = a_part.strip()
    else:
        question_part = full_output
        answer_part = "⚠️AI输出未遵循分割标记，无法自动分离答案"
    return question_part, answer_part

def generate_daily_review_plan(memory_records: str) -> str:
    prompt = f"""
现有间隔重复记忆卡片到期清单：
{memory_records}

请生成今日复习方案：
1. 按科目分组
2. 标注复习优先级（优先复习遗忘风险高的内容）
3. 语言简洁清晰，适合直接推送
4. 可以适当给出复习小建议
    """
    return llm_request(prompt)

def polish_text(text: str, lang: str = "") -> str:
    """润色德语/英语文本：让文本流畅自然，并遵循用户附加的修改要求"""
    lang_map = {
        "德语": "德语", "德": "德语", "de": "德语",
        "英语": "英语", "英": "英语", "en": "英语",
    }
    lang_desc = lang_map.get(lang.strip().lower(), "")
    if lang_desc:
        lang_line = f"目标语言：{lang_desc}（保持该语言输出，不要翻译成中文）"
    else:
        lang_line = "目标语言：根据原文自动判断（德语或英语），保持原文语言输出"

    prompt = f"""
你是专业的德语/英语文本润色助手。用户会给出待修改的文本，可能还包含修改要求。
任务：把文本修改得流畅、自然、符合语言习惯，同时满足用户附加的修改要求
（例如：更口语化、更正式、更简洁、更学术、控制字数、保持原意等）。
规则：
1. {lang_line}
2. 保持原意，不添加原文没有的内容，不删减核心信息
3. 修正语法、用词、表达不自然的地方，让整体读起来通顺
4. 如果用户没有明确要求，默认保持原风格，只做语言层面的润色
5. 只输出修改后的文本本身，不要任何解释、前缀、引号或 Markdown 标记

【用户提供的内容】
{text[:8000]}
"""
    return llm_request(prompt)
