# -*- coding: utf-8 -*-
"""项目自检：Python 语法 + 单元测试 + 未使用导入 + 前端 JS/CSS/HTML + 关键文件 + 配置安全 + Git 状态。

用法：python tools/selfcheck.py
"""
import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {
    ".git", "__pycache__", "venv", "bot_env", "node_modules", "android",
    "dist", "build", ".idea", ".vscode", "study_docs", "chroma_study_kb",
    "logs", "apk", "snap", "Downloads",
}
REQUIRED_WEB_FILES = [
    "web/chat.html", "web/sw.js", "web/manifest.json", "web/mascot.png",
    "web/icon.svg", "web/icon-192.png", "web/icon-512.png",
]
ENV_KEYS = [
    "WEB_PASSWORD", "ADMIN_PASSWORD", "LLM_API_KEY", "EMB_API_KEY",
    "FEISHU_APP_ID", "FEISHU_APP_SECRET", "TAVILY_API_KEY",
]

warnings = []
failures = []


def warn(msg):
    warnings.append(msg)
    print("  [警告] " + msg)


def fail(msg):
    failures.append(msg)
    print("  [失败] " + msg)


def iter_py_files():
    """遍历项目下所有 Python 文件（跳过虚拟环境、构建产物等目录）。"""
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fn in sorted(filenames):
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def check_python_syntax():
    print("== 1. Python 语法 ==")
    files = list(iter_py_files())
    for path in files:
        rel = os.path.relpath(path, ROOT)
        try:
            with open(path, "r", encoding="utf-8") as f:
                ast.parse(f.read(), filename=path)
        except SyntaxError as e:
            fail(f"{rel}:{e.lineno} 语法错误：{e.msg}")
        except UnicodeDecodeError:
            fail(f"{rel} 不是有效的 UTF-8 编码")
        except Exception as e:
            fail(f"{rel} 解析异常：{e}")
    print(f"  已检查 {len(files)} 个 Python 文件")
    return files


def check_unit_tests():
    print("== 2. 单元测试 ==")
    r = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", os.path.join(ROOT, "tests")]
    )
    if r.returncode != 0:
        fail("单元测试未通过")


def check_unused_imports(path):
    """用 AST 粗查顶层未使用导入，返回 [(行号, 名称)]。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
    except (SyntaxError, UnicodeDecodeError):
        return []
    used = set()
    imported = {}
    exported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                imported.setdefault(name, node.lineno)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                name = alias.asname or alias.name
                imported.setdefault(name, node.lineno)
        elif isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name):
                used.add(node.value.id)
        elif isinstance(node, ast.Assign):
            t = node.targets[0]
            if isinstance(t, ast.Name) and t.id == "__all__" and isinstance(node.value, (ast.List, ast.Tuple)):
                for elt in node.value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        exported.add(elt.value)
    return [
        (lineno, name)
        for name, lineno in imported.items()
        if name not in used and name not in exported
    ]


def check_imports(files):
    print("== 3. 未使用导入 ==")
    count = 0
    for path in files:
        rel = os.path.relpath(path, ROOT)
        for lineno, name in check_unused_imports(path):
            count += 1
            warn(f"{rel}:{lineno} 可能未使用：{name}")
    if count == 0:
        print("  未发现未使用导入")


def check_frontend():
    print("== 4. 前端检查 ==")
    for rel in REQUIRED_WEB_FILES:
        if not os.path.exists(os.path.join(ROOT, rel)):
            fail(f"缺少关键文件：{rel}")

    html_path = os.path.join(ROOT, "web", "chat.html")
    if not os.path.exists(html_path):
        return
    try:
        html = open(html_path, "r", encoding="utf-8").read()
    except UnicodeDecodeError:
        fail("web/chat.html 不是有效的 UTF-8 编码")
        return

    style_m = re.search(r"<style>(.*?)</style>", html, re.S)
    style = style_m.group(1) if style_m else ""
    if style.count("{") != style.count("}"):
        warn(f"CSS 花括号不匹配：{{ 有 {style.count('{')} 个，}} 有 {style.count('}')} 个")

    js = "".join(re.findall(r"<script(?![^>]*src=)[^>]*>(.*?)</script>", html, re.S))
    node = find_node()
    if node:
        fd, tmp = tempfile.mkstemp(suffix=".js", prefix="selfcheck_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(js)
            r = subprocess.run([node, "--check", tmp], capture_output=True, text=True)
            if r.returncode != 0:
                err = (r.stderr or r.stdout).strip().splitlines()
                fail("chat.html 内联 JS 语法错误：" + (err[-1] if err else "未知错误"))
            else:
                print("  chat.html 内联 JS 语法正常")
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    else:
        warn("未找到 Node.js，已跳过前端 JS 语法检查（可安装 Node 或设置 NODE_BIN 环境变量）")

    ids = re.findall(r'id="([^"]+)"', html)
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        warn("HTML 中存在重复 id：" + "、".join(dup[:8]))

    refs = (
        set(re.findall(r"\$\('#([^']+)'\)", js))
        | set(re.findall(r"getElementById\('([^']+)'\)", js))
        | set(re.findall(r'getElementById\("([^"]+)"\)', js))
    )
    missing = sorted(refs - set(ids))
    if missing:
        warn("JS 引用了 HTML 中不存在的 id：" + "、".join(missing[:10]))

    m = re.search(r"const APP_VERSION\s*=\s*'([^']+)'", html)
    web_ver = m.group(1) if m else None
    env_ver = None
    env_path = os.path.join(ROOT, ".env")
    if os.path.exists(env_path):
        for line in open(env_path, "r", encoding="utf-8"):
            if line.startswith("APP_VERSION="):
                env_ver = line.split("=", 1)[1].strip().strip('"').strip("'")
    if web_ver and env_ver and web_ver != env_ver:
        warn(f"版本不一致：chat.html={web_ver}，.env={env_ver}")


def find_node():
    """按环境变量、PATH、常见安装位置查找 Node.js。"""
    candidates = [
        os.environ.get("NODE_BIN"),
        os.environ.get("NODE"),
        shutil.which("node"),
        shutil.which("node.exe"),
        r"C:\Program Files\nodejs\node.exe",
        r"C:\Program Files (x86)\nodejs\node.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "nodejs", "node.exe"),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def check_env():
    print("== 5. 配置与密钥 ==")
    env_path = os.path.join(ROOT, ".env")
    if not os.path.exists(env_path):
        warn(".env 文件不存在（服务器必需，本地开发可忽略）")
    else:
        values = {}
        for line in open(env_path, "r", encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                values[k.strip()] = v.strip().strip('"').strip("'")
        for key in ENV_KEYS:
            if not (values.get(key) or os.environ.get(key)):
                warn(f".env 未配置 {key}")
    r = subprocess.run(
        ["git", "-C", ROOT, "ls-files", "--error-unmatch", ".env"],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        fail(".env 已被 Git 跟踪，存在密钥泄露风险，请从仓库移除")


def check_git():
    print("== 6. Git 状态 ==")
    r = subprocess.run(
        ["git", "-C", ROOT, "status", "--porcelain"],
        capture_output=True, text=True,
    )
    dirty = [ln for ln in r.stdout.splitlines() if ln.strip()]
    if dirty:
        warn(f"有 {len(dirty)} 个未提交改动（打包前请确认）")
    else:
        print("  工作区干净")


def main():
    start = time.time()
    files = check_python_syntax()
    check_unit_tests()
    check_imports(files)
    check_frontend()
    check_env()
    check_git()
    elapsed = time.time() - start
    print(f"== 自检完成：失败 {len(failures)} 项，警告 {len(warnings)} 项，耗时 {elapsed:.1f}s ==")
    if failures:
        print("请修复上述失败项后再打包/部署。")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
