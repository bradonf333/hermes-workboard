"""Workboard: your Jira board (+ Asana) as a Hermes Desktop page, plus agent tools.

The tools let the agent read the board and leave a short note on a ticket after
it works on it ("built, PR #212 open"), which shows on the card and in the
Focus view's activity feed.
"""
import json

try:
    from . import workboard_core as core
except ImportError:  # loaded standalone (tests, tooling)
    import workboard_core as core

BOARD = {
    "name": "workboard_board",
    "description": (
        "Read the user's Jira board as shown on their Workboard page: every ticket in the board's open sprints "
        "(assigned to them by default), grouped by the board's own columns (e.g. To Do, In Progress, Code Review, "
        "QA, Done; Done covers only recent days). Use it to answer 'what am I working on', 'what's in QA', or to "
        "find a ticket's key before acting on it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "column": {"type": "string", "description": "Only this column (case-insensitive), e.g. 'Code Review'."},
            "refresh": {"type": "boolean", "description": "Bypass the 60 s cache.", "default": False},
        },
        "required": [],
    },
}

NOTE = {
    "name": "workboard_note",
    "description": (
        "Leave a one-line note on a Jira ticket's Workboard card saying what you just did, e.g. 'built, PR #212 "
        "open' or 'tests pass, moved to Code Review'. Call this after you finish real work on a ticket (build, fix, "
        "review, deploy) so the user sees it on their board and in the Focus view's activity feed without opening "
        "Jira. Doesn't change anything in Jira. A new note replaces the ticket's previous one; notes fade after 14 days."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Jira key, e.g. SJT-2020."},
            "text": {"type": "string", "description": "What you did, one short line (max 280 chars)."},
            "title": {"type": "string", "description": "Optional ticket title, for the activity feed."},
        },
        "required": ["key", "text"],
    },
}


def _board(args):
    b = core.board(refresh=bool(args.get("refresh")))
    col = (args.get("column") or "").strip().lower()
    out = {}
    for name in b["columns"]:
        if col and name.lower() != col:
            continue
        out[name] = [{k: c.get(k) for k in ("key", "title", "status", "points", "epic", "sprint", "note") if c.get(k)}
                     for c in b["cards"] if c["column"] == name]
    if col and not out:
        return {"ok": False, "error": f"No column '{args.get('column')}'. Columns: {', '.join(b['columns'])}"}
    return {"ok": True, "board": b["board"]["name"], "done_days": b["done_days"], "columns": out}


def _note(args):
    return {"ok": True, "note": core.add_note(args.get("key", ""), args.get("text", ""), args.get("title", ""), by="Hermes")}


def _wrap(fn):
    def handler(args, **_kw):
        try:
            return json.dumps(fn(args or {}))
        except core.WorkboardError as e:
            return json.dumps({"ok": False, "error": str(e)})
        except Exception as e:  # never raise into the agent loop
            return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"})
    return handler


TOOLS = [(BOARD, _board), (NOTE, _note)]


def _available() -> bool:
    try:
        return bool(core.jira_conf())
    except Exception:
        return False


def register(ctx):
    for schema, fn in TOOLS:
        ctx.register_tool(name=schema["name"], toolset="workboard", schema=schema, handler=_wrap(fn),
                          check_fn=_available, description=schema["description"], emoji="🗂️")
