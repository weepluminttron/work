def ai_simplify_filename(raw_name: str, subject: str) -> str:
    """
    AI自动精简文档名称，去除冗余、去水印标记、重复文本
    raw_name：原始PDF文件名
    subject：AI识别出来的科目（德语A1/操作系统等）
    返回：精简后的短名称
    """
    prompt = f"""
任务：精简学习资料文件名，只输出最终名称，不要任何解释、多余标点、换行、思考文字。
【原始文件名】：{raw_name}
【科目分类】：{subject}

规则：
1. 删除 .pdf、(去水印)、（去水印）、【副本】、扫描版、修订版、高清版等无关备注
2. 删除重复文字，长教材名称缩写（例：新求精德语强化教程初级A1 → 新求精A1）
3. 保留核心：教材名称、等级、资料类型（词汇练习/课件/习题/讲义）
4. 文字控制在20字以内
5. 只输出一行精简标题，不要任何额外内容

示例：
输入：新求精德语强化教程 词汇练习册 初级A1（去水印）.pdf
输出：新求精A1词汇练习册
"""
    try:
        short_name = llm_chat(prompt).strip()
        # 清理换行、多余符号
        short_name = short_name.replace("\n", "").replace("\r", "")
        short_name = re.sub(r"\s+", " ", short_name).strip()

        # 兜底防护：防止AI返回超长文本
        if len(short_name) > 30:
            raise Exception("AI返回名称过长，启用兜底")
        print(f"✅AI名称精简成功：{raw_name} → {short_name}")
        return short_name
    except Exception as e:
        # LLM调用失败时使用正则降级方案，保证功能不中断
        import re
        fallback = raw_name.replace(".pdf", "")
        # 删除所有中英文括号及内部内容
        fallback = re.sub(r"[（(].*?[）)]", "", fallback)
        fallback = re.sub(r"\s+", " ", fallback).strip()
        print(f"⚠️AI精简名称失败，使用兜底命名：{str(e)} | 原始名称：{raw_name} | 兜底名称：{fallback}")
        return fallback
