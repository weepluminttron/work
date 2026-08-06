# -*- coding: utf-8 -*-
"""项目自检脚本：语法编译 + 单元测试 + 未使用导入检查
用法：python tools/selfcheck.py
"""
import ast
import os
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY_FILES = [
    "archive_db.py", "chat_memory.py", "config.py", "feishu_bot.py",
    "feishu_commands.py", "file_parser.py", "folder_watcher.py",
    "llm_summary.py", "quiz_logic.py", "quiz_store.py", "review_scheduler.py",
    "study_service.py", "vector_kb.py", "web_app.py", "wrong_book.py",
    "tools/selfcheck.py",
]


def check_unused_imports(path):
    """用 AST 粗查未使用的顶层导入，返回 [(行号, 名称)]"""
    with open(path, "r", encoding="utf-8") as f:
        try:
            tree = ast.parse(f.read(), filename=path)
        except SyntaxError:
            return []
    used = set()
    imported = {}
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
            # a.b 中 a 属于使用
            if isinstance(node.value, ast.Name):
                used.add(node.value.id)
    # 模块级 __all__ / 直接引用自身不算
    unused = []
    for name, lineno in imported.items():
        if name not in used and name != "__name__":
            unused.append((lineno, name))
    return unused


def main():
    ok = True
    print("== 1. 语法编译 ==")
    r = subprocess.run([sys.executable, "-m", "compileall", "-q"] + [os.path.join(ROOT, p) for p in PY_FILES])
    if r.returncode != 0:
        ok = False
    print("== 2. 单元测试 ==")
    r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", os.path.join(ROOT, "tests")])
    if r.returncode != 0:
        ok = False
    print("== 3. 未使用导入检查 ==")
    for p in PY_FILES:
        full = os.path.join(ROOT, p)
        if not os.path.exists(full):
            continue
        unused = check_unused_imports(full)
        for lineno, name in unused:
            print(f"  [WARN] {p}:{lineno} 可能未使用：{name}")
    print("== 自检完成 ==")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
