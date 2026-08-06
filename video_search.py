# -*- coding: utf-8 -*-
"""B站视频搜索：wbi 签名接口 + 中文关键词翻译 + 兜底搜索链接"""
import hashlib
import json
import re
import time
import uuid
import urllib.parse
import urllib.request

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def _mixin_key(orig: str) -> str:
    return "".join(orig[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def _get_mixin_key() -> str:
    """从 nav 接口获取 wbi 图片密钥，算出 mixin key"""
    req = urllib.request.Request(
        "https://api.bilibili.com/x/web-interface/nav",
        headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    wbi = data["data"]["wbi_img"]
    img_key = wbi["img_url"].rsplit("/", 1)[-1].split(".")[0]
    sub_key = wbi["sub_url"].rsplit("/", 1)[-1].split(".")[0]
    return _mixin_key(img_key + sub_key)


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _api_search(keyword: str, limit: int = 5) -> list:
    """调用B站 wbi 搜索接口（纯中文关键词在游客环境下可能被忽略，返回空）"""
    keyword = (keyword or "").strip()
    try:
        mixin = _get_mixin_key()
        params = {
            "search_type": "video",
            "keyword": keyword,
            "page": "1",
            "page_size": str(min(max(limit, 1), 20)),
            "platform": "pc",
            "web_location": "333.934",
        }
        params["wts"] = str(int(time.time()))
        # wbi 签名：过滤特殊字符后按 key 排序拼接，追加 mixin key 再 md5
        filtered = {
            k: v for k, v in params.items()
            if str(v).strip() not in ("", "'", "!", "(", ")", "*")
        }
        query = urllib.parse.urlencode(sorted(filtered.items()))
        params["w_rid"] = hashlib.md5((query + mixin).encode("utf-8")).hexdigest()
        url = "https://api.bilibili.com/x/web-interface/wbi/search/type?" + urllib.parse.urlencode(params)
        headers = {"User-Agent": UA, "Referer": "https://search.bilibili.com/"}
        try:
            import config as _cfg
            sess = getattr(_cfg, "BILI_SESSDATA", "") or ""
            if sess:
                buvid3 = str(uuid.uuid4()) + "infoc"
                headers["Cookie"] = f"buvid3={buvid3}; SESSDATA={sess}"
        except Exception:
            pass
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        if data.get("code") != 0:
            raise RuntimeError(f"bilibili code {data.get('code')}")
        results = (data.get("data") or {}).get("result") or []
        videos = []
        for r in results[:limit]:
            bvid = r.get("bvid")
            if not bvid:
                continue
            videos.append({
                "title": _strip_tags(r.get("title", "")),
                "link": f"https://www.bilibili.com/video/{bvid}",
                "author": r.get("author", ""),
                "duration": r.get("duration", ""),
                "play": r.get("play", 0),
            })
        if videos:
            return videos
    except Exception as e:
        print(f"⚠️B站搜索失败：{e}")
    return []


def _translate_keyword(keyword: str) -> str:
    """把中文主题翻译成英文，作为B站可识别的搜索词（ASCII关键词直接返回）"""
    if re.search(r"[A-Za-z]", keyword):
        return keyword
    try:
        from llm_summary import llm_request
        resp = llm_request(
            f"把「{keyword}」翻译成英文学习主题关键词，只输出英文，不要解释。",
            timeout=30,
        )
        en = resp.strip().strip('"').strip("'")
        if en and not en.startswith("❌") and re.search(r"[A-Za-z]", en):
            return en[:50]
    except Exception as e:
        print(f"⚠️关键词翻译失败：{e}")
    return ""


def _search_links(keyword: str) -> list:
    """兜底：返回可直接点击的B站搜索页链接（保证一定有可跳转链接）"""
    enc = urllib.parse.quote(keyword)
    return [
        {
            "title": f"🔍 在B站搜索「{keyword}」（综合）",
            "link": f"https://search.bilibili.com/all?keyword={enc}",
            "author": "",
            "duration": "",
            "play": 0,
        },
        {
            "title": f"🔍 在B站搜索「{keyword}」（最新发布）",
            "link": f"https://search.bilibili.com/all?keyword={enc}&order=pubdate",
            "author": "",
            "duration": "",
            "play": 0,
        },
        {
            "title": f"🔍 在B站搜索「{keyword}」（最多播放）",
            "link": f"https://search.bilibili.com/all?keyword={enc}&order=click",
            "author": "",
            "duration": "",
            "play": 0,
        },
    ]


def search_bilibili(keyword: str, limit: int = 5) -> list:
    """搜索B站视频，返回 [{title, link, author, duration, play}]；保证至少返回可点击链接"""
    keyword = (keyword or "").strip()
    videos = []
    # 含ASCII的关键词直接搜
    if re.search(r"[A-Za-z]", keyword):
        videos = _api_search(keyword, limit)
    # 纯中文：先翻译成英文再搜（游客接口会忽略纯中文关键词）
    if not videos:
        en = _translate_keyword(keyword)
        if en and en.lower() != keyword.lower():
            videos = _api_search(en, limit)
    if not videos:
        return _search_links(keyword)
    # 末尾始终附上搜索页兜底，保证有更多可点击结果
    videos = videos[:limit]
    videos.append(_search_links(keyword)[0])
    return videos
    return [{
        "title": f"在B站搜索「{keyword}」",
        "link": "https://search.bilibili.com/all?keyword=" + urllib.parse.quote(keyword),
        "author": "",
        "duration": "",
        "play": 0,
    }]
