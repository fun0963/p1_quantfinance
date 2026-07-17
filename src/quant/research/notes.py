"""Research knowledge base — one Markdown note per idea (M4.6).

Failed ideas are worth more than successful ones: without a record, the same
dead end gets re-explored every quarter. Each note is one idea — hypothesis,
approach, result, verdict — with a tiny frontmatter header, stored as versioned
Markdown under research_notes/: greppable, reviewable in PRs, and ready-made
context for an AI assistant.

Notes link to experiment ids from the experiment store (M4.5), so a claim like
"momentum beats ma_cross OOS" points at reproducible evidence, not memory.
Frontmatter is deliberately minimal `key: value` lines — no YAML dependency.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from config.settings import ROOT_DIR

DEFAULT_NOTES_DIR = ROOT_DIR / "research_notes"
STATUSES = ("idea", "testing", "adopted", "rejected")

_TEMPLATE = """## 假設

(要驗證什麼?為什麼覺得會有效?)

## 做法

(資料、參數、驗證方式 - sweep / walk-forward / 成本假設)

## 結果

(關鍵數字;對應的實驗 id 記在上方 frontmatter 的 experiments)

## 結論(採用 / 失敗原因)

(採用 -> 下一步;失敗 -> 為什麼,寫清楚讓下次不必重蹈)
"""


def _slugify(title: str) -> str:
    """Filesystem-safe slug: keep word characters, join the rest with '-'."""
    slug = re.sub(r"[^\w]+", "-", title.lower(), flags=re.UNICODE).strip("-")
    return slug or "note"


@dataclass
class Note:
    """Parsed view of one knowledge-base note (frontmatter + body)."""
    path: Path
    title: str
    status: str
    strategy: str = ""
    symbols: list[str] = field(default_factory=list)
    experiments: list[int] = field(default_factory=list)
    created: str = ""
    updated: str = ""
    body: str = ""


def create_note(
    title: str,
    *,
    status: str = "idea",
    strategy: str = "",
    symbols: tuple[str, ...] | list[str] = (),
    experiments: tuple[int, ...] | list[int] = (),
    notes_dir: str | Path | None = None,
) -> Path:
    """Create a dated, templated note file and return its path.

    Filenames are `YYYY-MM-DD-<slug>.md`; a same-day title collision gets a
    numeric suffix rather than overwriting an existing note.
    """
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, got {status!r}")
    base = Path(notes_dir) if notes_dir else DEFAULT_NOTES_DIR
    base.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    stem = f"{today}-{_slugify(title)}"
    path = base / f"{stem}.md"
    n = 2
    while path.exists():
        path = base / f"{stem}-{n}.md"
        n += 1

    front = [
        "---",
        f"title: {title}",
        f"status: {status}",
        f"strategy: {strategy}",
        f"symbols: {', '.join(symbols)}",
        f"experiments: {', '.join(str(e) for e in experiments)}",
        f"created: {today}",
        f"updated: {today}",
        "---",
        "",
    ]
    path.write_text("\n".join(front) + _TEMPLATE, encoding="utf-8")
    return path


def parse_note(path: str | Path) -> Note:
    """Parse one note's frontmatter (+ body). Unknown keys are ignored so notes
    stay hand-editable; a file without frontmatter raises a clear error."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n?", text, flags=re.DOTALL)
    if not m:
        raise ValueError(f"{p.name}: missing frontmatter (--- block) at the top")

    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip()

    symbols = [s.strip() for s in meta.get("symbols", "").split(",") if s.strip()]
    experiments = [int(e) for e in meta.get("experiments", "").split(",") if e.strip()]
    return Note(
        path=p,
        title=meta.get("title", p.stem),
        status=meta.get("status", "idea"),
        strategy=meta.get("strategy", ""),
        symbols=symbols,
        experiments=experiments,
        created=meta.get("created", ""),
        updated=meta.get("updated", ""),
        body=text[m.end():],
    )


def list_notes(notes_dir: str | Path | None = None,
               status: str | None = None) -> list[Note]:
    """All parseable notes, newest first (by filename date prefix); optional
    status filter. A missing directory is just an empty knowledge base."""
    base = Path(notes_dir) if notes_dir else DEFAULT_NOTES_DIR
    if not base.exists():
        return []
    notes = [parse_note(p) for p in sorted(base.glob("*.md"), reverse=True)
             if p.name.lower() != "readme.md"]
    if status:
        notes = [n for n in notes if n.status == status]
    return notes
