"""Approved rename of organized footage, with Resolve relink and undo.

`propose` turns the takes store into film-style names (`S02A_T03_IMG_5419.MOV`), `validate` checks the batch,
`apply` renames on disk and keeps everything that stores a footage path in step, `undo` reverses the last batch.

Order of writes, so a crash midway can be recovered with `undo` (which tolerates half-done batches):
renames.json (the undo record), disk, Resolve media pool, then the JSON stores (takes.json, broll.json, the
transcript cache, the bridge's current plan; older plans are history and stay as they were).

Clips already used on a timeline in the open Resolve project are never renamed (RENAME_USED_CLIPS): relinking a
used clip may lose its in/out points or grade. The Resolve project is passed in as an object with the scripting
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


def used_paths(project) -> set[str]:
    """File paths of every media pool item placed on any timeline of the project."""
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
    return used


def _relink(project, mapping: dict[str, str]) -> None:
    """Point every media pool item whose file is a key of `mapping` at its value and name it after the file."""
    wanted = {_norm(old): new for old, new in mapping.items()}
    for folder in _walk(project.GetMediaPool().GetRootFolder()):
        for clip in folder.GetClipList() or []:
            path = clip.GetClipProperty("File Path")
            new = wanted.get(_norm(path)) if path else None
            if new is None:
                continue
            if not clip.ReplaceClip(new):
                raise RuntimeError(f"Resolve could not relink {path} to {new}")
            clip.SetClipProperty("Clip Name", os.path.basename(new))


def _check_project(project, workdir: Path) -> None:
    state = Path(workdir) / ".edit" / "state.json"
    if not state.exists():
        return
    expected = json.loads(state.read_text()).get("resolve_project")
    if expected is not None and project.GetName() != expected:
        raise RuntimeError(f"Resolve has project '{project.GetName()}' open, but this folder belongs to "
                           f"'{expected}'. Open that project first.")


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


def propose(data: dict, scenes, used_paths: frozenset | set = frozenset()) -> list[dict]:
    """Rows {"old", "new", "reason"} for files with matched takes, in filming order of the store.

    B-roll, unmatched files, already correctly named files and the no-storyboard grouping (`mode` none) get no
    row. A row for a file used on a timeline in Resolve has "skip" set and is never applied.
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
            row["skip"] = SKIP_USED
        rows.append(row)
    return rows


def active(rows: list[dict]) -> list[dict]:
    return [r for r in rows if not r.get("skip")]


def validate(rows: list[dict]) -> list[str]:
    """Errors that block the whole batch; skipped rows are ignored."""
    errors, sources, targets = [], set(), {}
    for r in active(rows):
        old, new = r["old"], r["new"]
        if old in sources:
            errors.append(f"{old} appears twice as a source")
        sources.add(old)
        if not Path(old).exists():
            errors.append(f"source missing: {old}")
        if Path(new).exists():
            errors.append(f"target exists: {new}")
        key = new.casefold()
        if key in targets:
            errors.append(f"{old} and {targets[key]} would both become {new}")
        targets.setdefault(key, old)
    return errors


# --- stores that hold footage paths ---------------------------------------------

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
    if not path.exists():
        return
    data = json.loads(path.read_text())
    new = _remap(data, mapping)
    if new != data:
        path.write_text(json.dumps(new, indent=2, ensure_ascii=False, sort_keys=path.name == "broll.json") + "\n")


def _move_transcripts(cache_dir: Path, mapping: dict[str, str]) -> None:
    for old, new in mapping.items():
        src = cache_dir / (hashlib.sha1(old.encode()).hexdigest() + ".json")
        dst = cache_dir / (hashlib.sha1(new.encode()).hexdigest() + ".json")
        if not src.exists() or dst.exists():
            continue
        cached = json.loads(src.read_text())
        cached["file"] = new
        dst.write_text(json.dumps(cached))
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
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)
    return path


def _reverse(workdir: Path, pairs: list[tuple[str, str]], project) -> int:
    """Move files from `src` back to `dst` and fix Resolve and the stores. Tolerates half-done work."""
    moved = 0
    for src, dst in pairs:
        if Path(src).exists() and not Path(dst).exists():
            os.rename(src, dst)
            moved += 1
    mapping = {src: dst for src, dst in pairs}
    if project is not None:
        _relink(project, mapping)
    _rewrite_stores(workdir, mapping)
    return moved


def apply(rows: list[dict], workdir, resolve_project=None, never_imported: bool = False) -> Path:
    """Rename the non-skipped rows. Returns the path of renames.json."""
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
    if resolve_project is not None:
        _check_project(resolve_project, workdir)
        if not RENAME_USED_CLIPS:
            used = {_norm(p) for p in used_paths(resolve_project)}
            clash = [r["old"] for r in rows if _norm(r["old"]) in used]
            if clash:
                raise RuntimeError(f"Used on a timeline in Resolve, not renamed: {', '.join(clash)}")

    record = _load_record(workdir)
    batch = {"date": datetime.now(timezone.utc).isoformat(timespec="seconds"), "rows": rows,
             "resolve_project": resolve_project.GetName() if resolve_project is not None else None,
             "undone": False}
    record["batches"].append(batch)
    path = _save_record(workdir, record)  # first: this is the undo record
    pairs = [(r["new"], r["old"]) for r in rows]
    try:
        for r in rows:
            os.rename(r["old"], r["new"])
        mapping = {r["old"]: r["new"] for r in rows}
        if resolve_project is not None:
            _relink(resolve_project, mapping)
        _rewrite_stores(workdir, mapping)
    except BaseException:
        _reverse(workdir, pairs, resolve_project)
        record["batches"].pop()
        _save_record(workdir, record)
        raise
    return path


def undo(workdir, resolve_project=None) -> int:
    """Reverse the last batch in renames.json that is not undone; returns the number of files restored."""
    workdir = Path(workdir)
    record = _load_record(workdir)
    batch = next((b for b in reversed(record["batches"]) if not b["undone"]), None)
    if batch is None:
        raise RuntimeError("Nothing to undo.")
    if batch["resolve_project"] is not None and resolve_project is None:
        raise RuntimeError(f"Resolve must be open with project '{batch['resolve_project']}' to undo this rename.")
    for r in batch["rows"]:
        old, new = r["old"], r["new"]
        if Path(new).exists() and Path(old).exists():
            raise ValueError(f"cannot restore {old}: a file with that name exists again")
        if not Path(new).exists() and not Path(old).exists():
            raise ValueError(f"cannot restore {old}: {new} is missing")
    if resolve_project is not None:
        _check_project(resolve_project, workdir)
        used = {_norm(p) for p in used_paths(resolve_project)}
        clash = [r["new"] for r in batch["rows"] if _norm(r["new"]) in used]
        if clash:
            raise RuntimeError(f"Now used on a timeline in Resolve, not restored: {', '.join(clash)}")
    moved = _reverse(workdir, [(r["new"], r["old"]) for r in batch["rows"]],
                     resolve_project if batch["resolve_project"] is not None else None)
    batch["undone"] = True
    _save_record(workdir, record)
    return moved
