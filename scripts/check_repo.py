"""仓库合规/安全检查：密钥模式、超大文件、禁止提交的目录。

用法：
    python scripts/check_repo.py

不依赖第三方库；CI 中会在单元测试前运行。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

MAX_FILE_BYTES = 2 * 1024 * 1024
TEXT_SUFFIXES = {
    ".py", ".md", ".yml", ".yaml", ".toml", ".txt", ".bat", ".cmd", ".ps1",
    ".json", ".cfg", ".ini", ".cff", ".gitignore", ".gitattributes",
}
FORBIDDEN_PARTS = {
    "models/", "dist/", "portable/", ".venv/", "venv/", ".cache/", ".deps/",
}
SECRET_PATTERNS = [
    (re.compile(r"ghp_[A-Za-z0-9]{20,}"), "GitHub personal access token"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "GitHub fine-grained token"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "OpenAI-style API key"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key id"),
    (re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"), "private key"),
]


def tracked_files() -> list[Path]:
    try:
        output = subprocess.check_output(["git", "ls-files", "-z"], stderr=subprocess.DEVNULL)
        return [Path(item.decode("utf-8")) for item in output.split(b"\0") if item]
    except Exception:
        return [
            path
            for path in Path(".").rglob("*")
            if path.is_file()
            and ".git" not in path.parts
            and not any(part in {".venv", "venv", "models", "dist", "portable", ".cache", ".deps"} for part in path.parts)
        ]


def main() -> int:
    problems: list[str] = []
    files = tracked_files()
    if not files:
        print("[失败] 没有找到任何文件")
        return 1

    for path in files:
        text_path = str(path).replace("\\", "/")
        for forbidden in FORBIDDEN_PARTS:
            if text_path.startswith(forbidden):
                problems.append(f"禁止提交的目录：{path}")
        try:
            size = path.stat().st_size
        except OSError:
            problems.append(f"无法读取：{path}")
            continue
        if size > MAX_FILE_BYTES:
            problems.append(f"文件超过 2MB：{path}（{size} bytes）")
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {".gitignore", ".gitattributes"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pattern, label in SECRET_PATTERNS:
            if pattern.search(text):
                problems.append(f"疑似{label}：{path}")

    print(f"检查文件：{len(files)} 个")
    if problems:
        print(f"[失败] 发现 {len(problems)} 个问题：")
        for item in problems:
            print(f"  - {item}")
        return 1
    print("[OK] 未发现密钥模式、超大文件或禁止提交的目录")
    return 0


if __name__ == "__main__":
    sys.exit(main())
