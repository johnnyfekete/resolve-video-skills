"""Parse a storyboard or plain script into scenes."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

HEADING = re.compile(r"^##\s+(S?\d+[A-Za-z]?)\b")
FIELD = re.compile(r"^(setup|voiceover|b-roll)\s*:\s*(.*)$", re.IGNORECASE)
# Clause boundaries: . , ; : em dash ( ). Separators inside numbers (3.5, 1,000) do not split.
BOUNDARY = re.compile(r"(?<!\d)[.,;:—()]|[.,;:—()](?!\d)")


@dataclass
class Scene:
    id: str
    text: str
    setup: str | None = None
    voiceover: bool = False
    broll: str | None = None
    clauses: list[str] = field(default_factory=list)


def split_clauses(text: str) -> list[str]:
    parts = [p.strip() for p in BOUNDARY.split(text)]
    parts = [p for p in parts if p]
    merged: list[str] = []
    pending = ""
    for part in parts:
        part = f"{pending} {part}".strip() if pending else part
        pending = ""
        if len(part.split()) < 2:
            if merged:
                merged[-1] = f"{merged[-1]} {part}"
            else:
                pending = part
            continue
        merged.append(part)
    if pending:
        if merged:
            merged[-1] = f"{merged[-1]} {pending}"
        else:
            merged.append(pending)
    return merged


def tokens(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(c for c in folded if not unicodedata.combining(c)).lower()
    folded = re.sub(r"['’‘`]", "", folded)
    return re.findall(r"[a-z0-9]+", folded)


def _scene(id_: str, lines: list[str]) -> Scene:
    setup = broll = None
    voiceover = False
    body: list[str] = []
    for line in lines:
        m = FIELD.match(line.strip())
        if not m:
            body.append(line.strip())
            continue
        key, value = m.group(1).lower(), m.group(2).strip()
        if key == "setup":
            setup = value or None
        elif key == "voiceover":
            voiceover = value.lower() in ("yes", "true", "1")
        else:
            broll = value or None
    text = " ".join(l for l in body if l)
    return Scene(id_, text, setup, voiceover, broll, split_clauses(text))


def load(path: str | None) -> tuple[list[Scene], str]:
    if not path:
        return [], "none"
    content = Path(path).read_text(encoding="utf-8")
    lines = content.splitlines()
    if any(HEADING.match(l) for l in lines):
        scenes: list[Scene] = []
        current: str | None = None
        buf: list[str] = []
        for line in lines:
            m = HEADING.match(line)
            if m:
                if current is not None:
                    scenes.append(_scene(current, buf))
                current, buf = m.group(1), []
            elif current is not None:
                buf.append(line)
        if current is not None:
            scenes.append(_scene(current, buf))
        return scenes, "storyboard"
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", content) if p.strip()]
    scenes = [
        _scene(f"S{i:02d}", [" ".join(p.split())]) for i, p in enumerate(paragraphs, 1)
    ]
    return scenes, "script"
