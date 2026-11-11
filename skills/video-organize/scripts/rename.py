"""Approved rename of organized footage, with Resolve relink and undo.

`propose` turns the takes store into film-style names (`S02A_T03_IMG_5419.MOV`), `validate` checks the batch,
`apply` renames on disk and keeps everything that stores a footage path in step, `undo` reverses the last batch.

Order of writes, so a crash midway can be recovered with `undo` (which tolerates half-done batches):
renames.json (the undo record), disk, Resolve media pool, then the JSON stores (takes.json, broll.json, the
transcript cache, the bridge's current plan; older plans are history and stay as they were).

Clips already used on a timeline in the open Resolve project are never renamed (RENAME_USED_CLIPS): relinking a
used clip may lose its in/out points or grade. The API cannot list the sources inside compound, multicam or Fusion clips, so when the media pool holds any,
every clip imported in the project counts as used. The Resolve project is passed in as an object with the scripting
API's methods, so tests use fakes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

RENAME_USED_CLIPS = False
SCHEMA = "rvs.renames/1"
FILENAME = "renames.json"
SKIP_USED = "used on a timeline in Resolve"
SKIP_NESTED = "project has compound, multicam or Fusion clips; their use cannot be checked"
NESTED_TYPES = ("compound", "multicam", "fusion")

_ID = re.compile(r"^([A-Za-z]+\d+[A-Z])-T(\d+)$")
_PREFIX = re.compile(r"^[SG]\d+[A-Z](?:_T\d+(?:-T\d+)?|(?:_[SG]\d+[A-Z])+)_")


def _norm(path: str) -> str:
    return os.path.realpath(path)


def _org(workdir: Path) -> Path:
    return Path(workdir) / ".edit" / "organize"


# --- Resolve ---------------------------------------------------------------------

def _walk(folder):
    yield folder
    for sub in folder.GetSubFolderList() or []:
        yield from _walk(sub)


def _pool(project):
    for folder in _walk(project.GetMediaPool().GetRootFolder()):
        yield from folder.GetClipList() or []


def has_nested(project) -> bool:
    """True when the media pool holds a compound, multicam or Fusion clip."""
    return any(any(n in (c.GetClipProperty("Type") or "").lower() for n in NESTED_TYPES) for c in _pool(project))


def used_paths(project) -> set[str]:
    """File paths of every media pool item placed on a timeline of the project.

    With compound, multicam or Fusion clips in the pool this is every imported clip (see `has_nested`)."""
    used: set[str] = set()
    for i in range(1, project.GetTimelineCount() + 1):
        tl = project.GetTimelineByIndex(i)
        if tl is None:
            continue
        for kind in ("video", "audio"):
            for track in range(1, tl.GetTrackCount(kind) + 1):
                for item in tl.GetItemListInTrack(kind, track) or []:
                    mpi = item.GetMediaPoolItem()
                    path = mpi.GetClipProperty("File Path") if mpi else None
                    if path:
                        used.add(path)
    if has_nested(project):
        used |= {c.GetClipProperty("File Path") for c in _pool(project) if c.GetClipProperty("File Path")}
    return used


def _named(project, rows: list[dict]) -> set[str]:
    """Old paths of rows with a media pool item whose Clip Name is still the old file name."""
    named = set()
    for clip in _pool(project):
        path = clip.GetClipProperty("File Path")
        for r in rows:
            if path and _norm(path) == _norm(r["old"]) and clip.GetClipProperty("Clip Name") == os.path.basename(r["old"]):
                named.add(r["old"])
    return named


def _relink(project, mapping: dict[str, str], name_only: set[str] | None = None) -> None:
    """Point every media pool item whose file is a key of `mapping` at its value.

    A clip is also renamed after the file only when its Clip Name is the old file name and, if `name_only` is
    given, its path is in it."""
    wanted = {_norm(old): (old, new) for old, new in mapping.items()}
    for clip in _pool(project):
        path = clip.GetClipProperty("File Path")
        hit = wanted.get(_norm(path)) if path else None
        if hit is None:
            continue
        old, new = hit
        rename_clip = clip.GetClipProperty("Clip Name") == os.path.basename(old) and \
            (name_only is None or old in name_only)
        if not clip.ReplaceClip(new):
            raise RuntimeError(f"Resolve could not relink {path} to {new}")
        if rename_clip:
            clip.SetClipProperty("Clip Name", os.path.basename(new))


def _check_project(project, workdir: Path) -> None:
    state = Path(workdir) / ".edit" / "state.json"
    if not state.exists():
        return
    expected = json.loads(state.read_text()).get("resolve_project")
    if expected is not None and project.GetName() != expected:
        raise RuntimeError(f"Resolve has project '{project.GetName()}' open, but this folder belongs to "
                           f"'{expected}'. Open that project first.")


def _check_unused(project, paths: list[str]) -> None:
    used = {_norm(p) for p in used_paths(project)}
    clash = [p for p in paths if _norm(p) in used]
    if clash:
        why = SKIP_NESTED if has_nested(project) else SKIP_USED
        raise RuntimeError(f"Not changed ({why}): {', '.join(clash)}")


# --- proposal --------------------------------------------------------------------

def _prefix(takes: list[dict]) -> str:
    """Name prefix for one file's matched takes."""
    keys: dict[str, list[str]] = {}
    for t in sorted(takes, key=lambda t: t["start"]):
        m = _ID.match(t["id"])
        keys.setdefault(m.group(1), []).append(m.group(2))
    if len(keys) > 1:
        return "_".join(keys)
    (key, numbers), = keys.items()
    numbers = sorted(numbers, key=int)
    span = f"T{numbers[0]}" if len(numbers) == 1 else f"T{numbers[0]}-T{numbers[-1]}"
    return f"{key}_{span}"


def propose(data: dict, scenes, used_paths: frozenset | set = frozenset(), nested: bool = False) -> list[dict]:
    """Rows {"old", "new", "reason"} for files with matched takes, in filming order of the store.

    B-roll, unmatched files, already correctly named files and the no-storyboard grouping (`mode` none) get no
    row. A row for a file used in Resolve has "skip" set and is never applied; `nested` says `used_paths` is
    every imported clip because the project has compound, multicam or Fusion clips.
    """
    if data.get("mode") == "none":
        return []
    by_file: dict[str, list[dict]] = {}
    for t in data.get("takes", []):
        if t.get("id") and _ID.match(t["id"]):
            by_file.setdefault(t["file"], []).append(t)
    used = {_norm(p) for p in used_paths}
    rows = []
    for file, takes in by_file.items():
        prefix = _prefix(takes)
        p = Path(file)
        new = str(p.with_name(f"{prefix}_{_PREFIX.sub('', p.name, count=1)}"))
        if new == file:
            continue
        row = {"old": file, "new": new,
               "reason": f"{len(takes)} take{'s' if len(takes) != 1 else ''} in "
                         f"{', '.join(sorted({t['scene'] for t in takes}))}"}
        if not RENAME_USED_CLIPS and _norm(file) in used:
            row["skip"] = SKIP_NESTED if nested else SKIP_USED
        rows.append(row)
    return rows


def active(rows: list[dict]) -> list[dict]:
    return [r for r in rows if not r.get("skip")]


def validate(rows: list[dict]) -> list[str]:
    """Errors that block the whole batch; skipped rows are ignored."""
    errors, sources, targets = [], set(), {}
    for r in active(rows):
        old, new = r["old"], r["new"]
        if _norm(old) in sources:
            errors.append(f"{old} appears twice as a source")
        sources.add(_norm(old))
        if not Path(old).exists():
            errors.append(f"source missing: {old}")
        if Path(new).exists():
            errors.append(f"target exists: {new}")
        key = new.casefold()
        if key in targets:
            errors.append(f"{old} and {targets[key]} would both become {new}")
        targets.setdefault(key, old)
    return errors


def _safe_rename(src: str, dst: str) -> None:
    """Rename without ever replacing something: refuse if any entry of the folder equals `dst` ignoring case,
    except `src` itself (a case-only rename)."""
    want, parent = os.path.basename(dst).casefold(), os.path.dirname(dst) or "."
    for entry in os.listdir(parent):
        if entry.casefold() == want and entry != os.path.basename(src):
            raise FileExistsError(f"{os.path.join(parent, entry)} exists; not overwriting it with {src}")
    os.rename(src, dst)


# --- stores that hold footage paths ---------------------------------------------

def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def _remap(obj, mapping: dict[str, str]):
    """Copy of a JSON value with every string (and dict key) equal to a mapped path replaced."""
    if isinstance(obj, str):
        return mapping.get(obj, obj)
    if isinstance(obj, list):
        return [_remap(x, mapping) for x in obj]
    if isinstance(obj, dict):
        return {mapping.get(k, k): _remap(v, mapping) for k, v in obj.items()}
    return obj


def _rewrite_json(path: Path, mapping: dict[str, str]) -> None:
    """Rewrite a JSON file atomically in the style it already has (indent, ensure_ascii, final newline)."""
    if not path.exists():
        return
    text = path.read_text()
    data = json.loads(text)
    new = _remap(data, mapping)
    if new == data:
        return
    lines = text.split("\n")
    indent = len(lines[1]) - len(lines[1].lstrip()) if len(lines) > 2 and lines[1].startswith(" ") else None
    out = json.dumps(new, indent=indent, ensure_ascii="\\u" in text, sort_keys=path.name == "broll.json")
    _write_atomic(path, out + ("\n" if text.endswith("\n") else ""))


def _move_transcripts(cache_dir: Path, mapping: dict[str, str]) -> None:
    for old, new in mapping.items():
        src = cache_dir / (hashlib.sha1(old.encode()).hexdigest() + ".json")
        dst = cache_dir / (hashlib.sha1(new.encode()).hexdigest() + ".json")
        if not src.exists() or dst.exists():
            continue
        cached = json.loads(src.read_text())
        cached["file"] = new
        _write_atomic(dst, json.dumps(cached))
        src.unlink()


def _rewrite_stores(workdir: Path, mapping: dict[str, str]) -> None:
    org = _org(workdir)
    _rewrite_json(org / "takes.json", mapping)
    _rewrite_json(org / "broll.json", mapping)
    _move_transcripts(org / "transcripts", mapping)
    state = Path(workdir) / ".edit" / "state.json"
    if state.exists():
        rel = json.loads(state.read_text()).get("current", {}).get("plan")
        if rel:
            _rewrite_json(Path(workdir) / ".edit" / rel, mapping)


# --- record, apply, undo --------------------------------------------------------

def _load_record(workdir: Path) -> dict:
    path = _org(workdir) / FILENAME
    return json.loads(path.read_text()) if path.exists() else {"schema": SCHEMA, "batches": []}


def _save_record(workdir: Path, record: dict) -> Path:
    path = _org(workdir) / FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(path, json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    return path


def _reverse(workdir: Path, pairs: list[tuple[str, str]], project, named: set[str] | None = None) -> int:
    """Move files from `src` back to `dst` and fix Resolve and the stores. Tolerates half-done work.

    `named` holds the `src` paths whose media pool item was named after the file."""
    moved = 0
    for src, dst in pairs:
        if Path(src).exists():
            _safe_rename(src, dst)
            moved += 1
    mapping = {src: dst for src, dst in pairs}
    if project is not None:
        _relink(project, mapping, named)
    _rewrite_stores(workdir, mapping)
    return moved


def apply(rows: list[dict], workdir, resolve_project=None, never_imported: bool = False) -> Path:
    """Rename the non-skipped rows. Returns the path of renames.json.

    Needs the open Resolve project, or `never_imported` when the footage was never imported into Resolve."""
    workdir = Path(workdir)
    rows = [{"old": r["old"], "new": r["new"], "reason": r.get("reason", "")} for r in active(rows)]
    if not rows:
        raise ValueError("nothing to rename")
    errors = validate(rows)
    if errors:
        raise ValueError("; ".join(errors))
    if resolve_project is None and not never_imported:
        raise RuntimeError("Resolve is not open with this project. Open it, or pass never_imported if the "
                           "footage was never imported into Resolve.")
    named: set[str] = set()
    if resolve_project is not None:
        _check_project(resolve_project, workdir)
        if not RENAME_USED_CLIPS:
            _check_unused(resolve_project, [r["old"] for r in rows])
        named = _named(resolve_project, rows)
        for r in rows:
            r["named"] = r["old"] in named

    record = _load_record(workdir)
    batch = {"date": datetime.now(timezone.utc).isoformat(timespec="seconds"), "rows": rows,
             "resolve_project": resolve_project.GetName() if resolve_project is not None else None,
             "undone": False}
    record["batches"].append(batch)
    path = _save_record(workdir, record)  # first: this is the undo record
    done: list[tuple[str, str]] = []
    try:
        for r in rows:
            _safe_rename(r["old"], r["new"])
            done.append((r["new"], r["old"]))
        mapping = {r["old"]: r["new"] for r in rows}
        if resolve_project is not None:
            _relink(resolve_project, mapping, named)
        _rewrite_stores(workdir, mapping)
    except BaseException as error:
        try:
            _reverse(workdir, done, resolve_project, {r["new"] for r in rows if r.get("named")})
        except Exception as again:
            error.add_note(f"Rolling back also failed ({again!r}); the batch stays in renames.json, "
                           "run rename --undo")
            raise
        record["batches"].pop()
        _save_record(workdir, record)
        raise
    return path


def undo(workdir, resolve_project=None, never_imported: bool = False) -> int:
    """Reverse the last batch in renames.json that is not undone; returns the number of files restored.

    Like `apply` it needs the matching open Resolve project (clips used on a timeline since are refused),
    or `never_imported`."""
    workdir = Path(workdir)
    record = _load_record(workdir)
    batch = next((b for b in reversed(record["batches"]) if not b["undone"]), None)
    if batch is None:
        raise RuntimeError("Nothing to undo.")
    if resolve_project is None and not never_imported:
        raise RuntimeError("Resolve is not open with this project. Open it, or pass never_imported if the "
                           "footage was never imported into Resolve.")
    for r in batch["rows"]:
        old, new = r["old"], r["new"]
        if Path(new).exists() and Path(old).exists():
            raise ValueError(f"cannot restore {old}: a file with that name exists again")
        if not Path(new).exists() and not Path(old).exists():
            raise ValueError(f"cannot restore {old}: {new} is missing")
    if resolve_project is not None:
        _check_project(resolve_project, workdir)
        _check_unused(resolve_project, [r["new"] for r in batch["rows"]])
    moved = _reverse(workdir, [(r["new"], r["old"]) for r in batch["rows"]], resolve_project,
                     {r["new"] for r in batch["rows"] if r.get("named")})
    batch["undone"] = True
    _save_record(workdir, record)
    return moved
