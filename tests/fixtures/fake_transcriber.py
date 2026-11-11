"""Stand-in for the Whisper worker in tests: `fake_transcriber.py <in.json> <out.json>`.

Env RVS_FAKE_WORDS names a JSON file {"<duration in whole seconds>": [[word, start, end], ...]}. A file is
recognised by its length (the end of its last slice), and each slice gets the words that start inside it.
"""
import json
import os
import sys

jobs = json.load(open(sys.argv[1]))
canned = json.load(open(os.environ["RVS_FAKE_WORDS"]))
words = canned.get(str(round(max(j["end"] for j in jobs))), [])
out = []
for job in jobs:
    ws = [{"w": w, "start": round(s - job["start"], 3), "end": round(e - job["start"], 3), "p": 0.9}
          for w, s, e in words if job["start"] <= s < job["end"]]
    segs = [{"text": " ".join(w["w"] for w in ws), "start": ws[0]["start"], "end": ws[-1]["end"]}] if ws else []
    out.append({"index": job["index"], "language": "en", "words": ws, "segments": segs})
json.dump(out, open(sys.argv[2], "w"))
