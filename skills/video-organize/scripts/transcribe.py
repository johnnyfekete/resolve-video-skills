"""Thin shim: the transcriber lives in the bridge (rvs_transcribe). Kept so `transcribe.py <file>` still works."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, os.environ.get("RVS_BRIDGE") or str(HERE.parent.parent / "resolve-bridge" / "scripts"))

from rvs_transcribe import *  # noqa: E402,F401,F403


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: transcribe.py <file> [cache_dir]", file=sys.stderr)
        return 2
    cache = Path(argv[2]) if len(argv) > 2 else Path(tempfile.gettempdir()) / "rvs-transcripts"
    t = transcribe(argv[1], cache)
    print("language:", t["language"])
    if t.get("warning"):
        print("warning:", t["warning"])
    print(" ".join(w["w"] for w in t["words"][:20]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
