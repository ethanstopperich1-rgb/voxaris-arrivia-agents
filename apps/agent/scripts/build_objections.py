"""Re-parse the source TOP 100 OBJECTIONS .docx into objections.json.

Run this whenever the source playbook is updated. The JSON ships in the
wheel and is what the agent's `lookup_objection` tool reads.

Usage:
    python -m scripts.build_objections \\
        --src "/path/to/TOP 100 OBJECTIONS.docx" \\
        --out voxaris_agent/data/objections.json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from docx import Document

CAT_RE = re.compile(r"^[^\w]+CATEGORY\s+\d+:\s*(.+?)\s*\(\d+[–-]\d+\)\s*$")


def parse(src: Path) -> list[dict]:
    doc = Document(str(src))
    paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    out: list[dict] = []
    current_cat = "GENERAL"
    i = 0
    while i < len(paras):
        line = paras[i]
        cm = CAT_RE.match(line)
        if cm:
            current_cat = cm.group(1).strip()
            i += 1
            continue
        is_quote_open = line.startswith("“") or line.startswith('"')
        if is_quote_open and i + 1 < len(paras):
            nxt = paras[i + 1]
            if nxt.startswith("→") or nxt.startswith("->"):
                q = line.strip("“”\"").strip()
                a = nxt.lstrip("→-> ").strip().strip("“”\"")
                out.append({"category": current_cat, "objection": q, "rebuttal": a})
                i += 2
                continue
        i += 1
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--src", type=Path, required=True)
    p.add_argument(
        "--out",
        type=Path,
        default=Path("voxaris_agent/data/objections.json"),
    )
    args = p.parse_args()
    if not args.src.is_file():
        raise SystemExit(f"source not found: {args.src}")
    entries = parse(args.src)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(entries, indent=2, ensure_ascii=False))
    print(f"wrote {args.out} ({len(entries)} entries)")


if __name__ == "__main__":
    main()
