"""受保护文件完整性校验。

用法：
    python tools/verify_integrity.py          # 校验，任何不一致返回非零
    python tools/verify_integrity.py --write  # 仅设计方使用：重新生成清单

哈希在把 CRLF 统一成 LF 之后计算，因此不受 Windows 换行设置影响。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "PROTECTED.sha256"
PROTECTED_GLOBS = (
    "AGENTS.md",
    "docs/*.md",
    "perq/**/*.py",
    "tests/**/*.py",
    "tools/**/*.py",
    "scripts/**/*.py",
    "tasks/*.md",
    "requirements.txt",
    "pytest.ini",
    ".gitattributes",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def protected_files() -> list[Path]:
    found: set[Path] = set()
    for pattern in PROTECTED_GLOBS:
        found.update(p for p in ROOT.glob(pattern) if p.is_file() and "__pycache__" not in p.parts)
    return sorted(found)


def write_manifest() -> int:
    lines = [f"{digest(p)}  {p.relative_to(ROOT).as_posix()}" for p in protected_files()]
    MANIFEST.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"已写入 {len(lines)} 条记录到 {MANIFEST.name}")
    return 0


def verify() -> int:
    if not MANIFEST.exists():
        print("失败：缺少 PROTECTED.sha256")
        return 2
    expected: dict[str, str] = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if line.strip():
            sha, rel = line.split("  ", 1)
            expected[rel] = sha
    actual = {p.relative_to(ROOT).as_posix(): digest(p) for p in protected_files()}
    modified = sorted(rel for rel in expected if rel in actual and actual[rel] != expected[rel])
    missing = sorted(rel for rel in expected if rel not in actual)
    added = sorted(rel for rel in actual if rel not in expected)
    for label, items in (("被修改", modified), ("被删除", missing), ("未登记的新增", added)):
        for rel in items:
            print(f"{label}: {rel}")
    if modified or missing or added:
        print("失败：受保护文件与清单不一致。请停止工作并在任务报告中说明。")
        return 1
    print(f"通过：{len(expected)} 个受保护文件与清单一致。")
    return 0


if __name__ == "__main__":
    sys.exit(write_manifest() if "--write" in sys.argv[1:] else verify())
