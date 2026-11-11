"""Whisper worker, run under `uv run --with mlx-whisper`: transcribe each slice's WAV.

Usage: whisper_worker.py <in.json> <out.json>
in.json:  [{"index", "wav", "start", "end"}, ...]
out.json: [{"index", "language", "segments": [{"text", "start", "end"}],
            "words": [{"w", "start", "end", "p"}]}, ...]   (times relative to the slice)
Language is auto-detected per slice.
"""
import json
import sys

import mlx_whisper

MODEL = "mlx-community/whisper-large-v3-turbo"


def main(src: str, dst: str) -> None:
    slices = json.load(open(src))
    out = []
    for s in slices:
        r = mlx_whisper.transcribe(s["wav"], path_or_hf_repo=MODEL, word_timestamps=True)
        segs, words = [], []
        for seg in r.get("segments", []):
            segs.append({"text": seg["text"].strip(), "start": seg["start"], "end": seg["end"]})
            for w in seg.get("words", []):
                words.append({"w": w["word"].strip(), "start": w["start"], "end": w["end"],
                              "p": w.get("probability", 0.0)})
        out.append({"index": s["index"], "language": r.get("language"), "segments": segs, "words": words})
    json.dump(out, open(dst, "w"))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
