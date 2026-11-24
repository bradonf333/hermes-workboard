"""workboard plugin backend: mounted at /api/plugins/workboard.

Thin HTTP wrapper over workboard_core (one level up), which the agent tools share.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import workboard_core as core  # noqa: E402

router = APIRouter()


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except core.WorkboardError as e:
        raise HTTPException(e.status, str(e))


@router.get("/status")
def status():
    return core.status()


@router.get("/board")
def board(refresh: int = 0):
    return _run(core.board, refresh=bool(refresh))


@router.get("/activity")
def activity():
    return {"events": _run(core.activity)}


@router.get("/ticket/{key}")
def ticket(key: str):
    return _run(core.ticket, key)


@router.get("/boards")
def boards():
    return {"boards": _run(core.list_boards)}


@router.get("/asana")
def asana():
    return _run(core.asana_tasks)


@router.get("/asana/{gid}")
def asana_task(gid: str):
    return _run(core.asana_task, gid)


@router.delete("/note/{key}")
def clear_note(key: str):
    return {"cleared": _run(core.clear_note, key)}
