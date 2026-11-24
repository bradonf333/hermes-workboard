"""Workboard core: your Jira board (+ Asana) for Hermes.

Used by both halves of the package: the panel backend (dashboard/plugin_api.py)
and the agent tools (__init__.py). Stdlib only. Read-only against Jira and
Asana; the only thing written is the plugin's own notes file (what the agent
says it did on a ticket).
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

PLUGIN_ID = "workboard"
ASANA_API = "https://app.asana.com/api/1.0"
CACHE_TTL = 60  # seconds the board is reused between panel polls
ACTIVITY_DAYS = 3
NOTE_TTL_DAYS = 14
DEFAULT_COLUMNS = [
    ("To Do", "new"), ("In Progress", "indeterminate"), ("Done", "done"),
]


class WorkboardError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- settings + secrets

def _plugin_settings() -> dict:
    try:
        from hermes_cli.config import load_config_readonly

        cfg = load_config_readonly() or {}
        return ((cfg.get("plugins") or {}).get("entries") or {}).get(PLUGIN_ID, {}).get("settings") or {}
    except Exception:
        return {}


def _secret(name: str) -> str:
    try:
        from agent.secret_scope import get_secret

        v = get_secret(name)
        if v:
            return v
    except Exception:
        pass
    return os.environ.get(name, "")


def _setting(key: str, default=None):
    v = _plugin_settings().get(key)
    return default if v in (None, "") else v


def jira_conf() -> dict | None:
    base = (_setting("jira_base_url") or _secret("JIRA_BASE_URL") or "").strip().rstrip("/")
    email = (_setting("jira_email") or _secret("JIRA_EMAIL") or "").strip()
    token = (_secret("JIRA_API_TOKEN") or "").strip()
    if not (base and email and token):
        return None
    if not base.startswith("https://"):
        raise WorkboardError("Jira site URL must start with https://")
    return {"base": base, "auth": "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()}


def asana_token() -> str:
    return (_secret("ASANA_ACCESS_TOKEN") or "").strip()


def done_days() -> int:
    try:
        return max(0, min(60, int(_setting("done_days", 7))))
    except (TypeError, ValueError):
        return 7


def only_mine() -> bool:
    v = _setting("only_mine", True)
    return v.strip().lower() in ("1", "true", "yes", "on") if isinstance(v, str) else bool(v)


def data_dir() -> Path:
    override = os.environ.get("WORKBOARD_DATA_DIR")
    if override:
        d = Path(override)
    else:
        try:
            from plugins.plugin_storage import plugin_data_dir

            d = Path(plugin_data_dir(PLUGIN_ID))
        except Exception:
            d = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "plugin-data" / PLUGIN_ID
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


_lock = threading.RLock()


@contextlib.contextmanager
def _file_lock():
    with _lock:
        fh = open(data_dir() / ".lock", "a+")
        try:
            try:
                import fcntl

                fcntl.flock(fh, fcntl.LOCK_EX)
            except ImportError:
                pass
            yield
        finally:
            fh.close()


# ---------------------------------------------------------------- HTTP

def _http(method: str, url: str, *, headers: dict | None = None, params: dict | None = None, timeout: int = 25):
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method=method, headers={"Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        if e.code in (401, 403):
            raise WorkboardError(f"Not allowed ({e.code}): check the API token and its access", status=e.code)
        raise WorkboardError(f"Request failed ({e.code}): {detail}", status=502)
    except (urllib.error.URLError, TimeoutError) as e:
        raise WorkboardError(f"Could not reach {urllib.parse.urlparse(url).netloc}: {e}", status=502)


def _jira(conf: dict, path: str, params: dict | None = None):
    return _http("GET", conf["base"] + path, headers={"Authorization": conf["auth"]}, params=params)


# ---------------------------------------------------------------- Jira board

def list_boards(conf: dict | None = None) -> list[dict]:
    conf = conf or _need_jira()
    return _cached(f"boards:{conf['base']}", lambda: _list_boards(conf))


def _list_boards(conf: dict) -> list[dict]:
    out, start = [], 0
    while True:
        page = _jira(conf, "/rest/agile/1.0/board", {"startAt": start, "maxResults": 50})
        out += [{"id": b["id"], "name": b["name"], "type": b.get("type")} for b in page.get("values", [])]
        if page.get("isLast", True) or not page.get("values"):
            return out
        start += len(page["values"])


def _need_jira() -> dict:
    conf = jira_conf()
    if not conf:
        raise WorkboardError("Jira isn't set up: add your Jira site, email and API token in the plugin settings", 409)
    return conf


def board_id(conf: dict) -> int:
    raw = _setting("jira_board")
    if raw not in (None, ""):
        if str(raw).strip().isdigit():
            return int(raw)
        name = str(raw).strip().lower()
        for b in list_boards(conf):
            if b["name"].lower() == name:
                return b["id"]
        raise WorkboardError(f"No Jira board named '{raw}'", 404)
    boards = [b for b in list_boards(conf) if b["type"] == "scrum"] or list_boards(conf)
    if not boards:
        raise WorkboardError("Your Jira account can't see any boards", 404)
    return boards[0]["id"]


_meta: dict = {}
META_TTL = 10 * 60


def _cached(name: str, fn):
    hit = _meta.get(name)
    if hit and time.time() - hit[0] < META_TTL:
        return hit[1]
    val = fn()
    _meta[name] = (time.time(), val)
    return val


def board_config(conf: dict, bid: int) -> dict:
    """The board's own columns (and which status ids land in each), points field and saved filter."""
    def load():
        cfg = _jira(conf, f"/rest/agile/1.0/board/{bid}/configuration")
        cols = [{"name": c["name"], "statuses": [str(s["id"]) for s in c.get("statuses", [])]}
                for c in (cfg.get("columnConfig") or {}).get("columns", []) if c.get("statuses")]
        fid = (cfg.get("filter") or {}).get("id")
        if not fid:
            raise WorkboardError("That board has no saved filter", 502)
        return {"name": cfg.get("name") or str(bid), "columns": cols, "filter": str(fid),
                "points": ((cfg.get("estimation") or {}).get("field") or {}).get("fieldId") or ""}
    return _cached(f"cfg:{conf['base']}:{bid}", load)


def _sprint_field(conf: dict) -> str:
    def load():
        for f in _jira(conf, "/rest/api/3/field"):
            if (f.get("schema") or {}).get("custom") == "com.pyxis.greenhopper.jira:gh-sprint":
                return f["id"]
        return ""
    return _cached(f"sprintfield:{conf['base']}", load)


def _column_for(status: dict, cols: list[dict]) -> str:
    sid = str(status.get("id") or "")
    for c in cols:
        if sid in c["statuses"]:
            return c["name"]
    cat = (status.get("statusCategory") or {}).get("key")
    return {"new": cols[0]["name"], "done": cols[-1]["name"]}.get(cat, cols[min(1, len(cols) - 1)]["name"]) if cols else ""


def _card(issue: dict, cols, points_field: str, sprint_field: str, base: str) -> dict:
    f = issue.get("fields") or {}
    status = f.get("status") or {}
    sprints = [s for s in (f.get(sprint_field) or []) if isinstance(s, dict)] if sprint_field else []
    open_sprints = [s.get("name") for s in sprints if s.get("state") in ("active", "future")]
    parent = f.get("parent") or {}
    pts = f.get(points_field) if points_field else None
    return {
        "key": issue["key"],
        "title": f.get("summary") or "",
        "column": _column_for(status, cols),
        "status": status.get("name") or "",
        "done": (status.get("statusCategory") or {}).get("key") == "done",
        "type": (f.get("issuetype") or {}).get("name") or "",
        "priority": (f.get("priority") or {}).get("name"),
        "points": pts if isinstance(pts, (int, float)) else None,
        "epic": ((parent.get("fields") or {}).get("summary")) or None,
        "epic_key": parent.get("key"),
        "sprint": open_sprints[-1] if open_sprints else None,
        "assignee": (f.get("assignee") or {}).get("displayName"),
        "updated": f.get("updated") or "",
        "url": f"{base}/browse/{issue['key']}",
    }


def _board_jql() -> str:
    mine = "assignee = currentUser() AND " if only_mine() else ""
    return (f"{mine}sprint in openSprints() AND "
            f"(statusCategory != Done OR statusCategoryChangedDate >= -{done_days()}d) ORDER BY Rank ASC")


def _search(conf: dict, jql: str, fields: str, expand: str | None = None, limit: int = 1000) -> list[dict]:
    out, token = [], None
    while len(out) < limit:
        params = {"jql": jql, "fields": fields, "maxResults": 100}
        if expand:
            params["expand"] = expand
        if token:
            params["nextPageToken"] = token
        page = _jira(conf, "/rest/api/3/search/jql", params)
        out += page.get("issues", [])
        if page.get("isLast", True) or not page.get("nextPageToken") or not page.get("issues"):
            return out
        token = page["nextPageToken"]
    return out


_cache: dict = {}


def board(refresh: bool = False) -> dict:
    """Columns + cards for the configured board, Done limited to the last N days."""
    conf = _need_jira()
    key = (conf["base"], _setting("jira_board"), done_days(), only_mine())
    hit = _cache.get("board")
    if hit and not refresh and hit["key"] == key and time.time() - hit["at"] < CACHE_TTL:
        return hit["data"]
    bid = board_id(conf)
    bc = board_config(conf, bid)
    points_field, cols = bc["points"], bc["columns"]
    if not cols:
        cols = [{"name": n, "statuses": []} for n, _ in DEFAULT_COLUMNS]
    sprint_field = _sprint_field(conf)
    fields = ",".join(x for x in ["summary", "status", "issuetype", "priority", "parent", "assignee", "updated",
                                  points_field, sprint_field] if x)
    issues = _search(conf, f"filter = {bc['filter']} AND {_board_jql()}", fields)
    cards = [_card(i, cols, points_field, sprint_field, conf["base"]) for i in issues]
    notes = _active_notes()
    for c in cards:
        if c["key"] in notes:
            c["note"] = notes[c["key"]]
    data = {"board": {"id": bid, "name": bc["name"]}, "columns": [c["name"] for c in cols], "cards": cards,
            "done_days": done_days(), "only_mine": only_mine(), "fetched_at": _iso_now()}
    _cache["board"] = {"key": key, "at": time.time(), "data": data}
    return data


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- activity (Focus view)

def activity(days: int = ACTIVITY_DAYS) -> list[dict]:
    """Recent column moves on your board tickets (from Jira's history) + notes the agent left, newest first."""
    conf = _need_jira()
    bc = board_config(conf, board_id(conf))
    cols = bc["columns"]
    mine = "assignee = currentUser() AND " if only_mine() else ""
    issues = _search(conf, f"filter = {bc['filter']} AND {mine}updated >= -{days}d ORDER BY updated DESC",
                     "summary,status", expand="changelog", limit=200)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    for i in issues:
        for h in (i.get("changelog") or {}).get("histories", []):
            at = _parse(h.get("created"))
            if not at or at < since:
                continue
            for it in h.get("items", []):
                if it.get("field") != "status":
                    continue
                to_col = _column_for({"id": it.get("to")}, cols) if cols else it.get("toString")
                out.append({"key": i["key"], "title": (i.get("fields") or {}).get("summary") or "",
                            "kind": "move", "by": (h.get("author") or {}).get("displayName") or "",
                            "from": it.get("fromString"), "to": it.get("toString"), "column": to_col,
                            "at": at.isoformat(timespec="seconds")})
    for key, n in _active_notes().items():
        at = _parse(n["at"])
        if at and at >= since:
            out.append({"key": key, "title": n.get("title", ""), "kind": "note", "by": n.get("by", "Hermes"),
                        "text": n["text"], "at": n["at"]})
    out.sort(key=lambda e: e["at"], reverse=True)
    return out[:60]


def _parse(v):
    if not v:
        return None
    try:
        v = re.sub(r"([+-]\d\d)(\d\d)$", r"\1:\2", v.replace("Z", "+00:00"))
        return datetime.fromisoformat(v).astimezone(timezone.utc)
    except ValueError:
        return None


# ---------------------------------------------------------------- ticket detail

def _adf_text(node) -> str:
    """Atlassian Document Format -> plain text with paragraphs and bullets."""
    if not node:
        return ""
    if isinstance(node, str):
        return node
    t = node.get("type")
    kids = "".join(_adf_text(c) for c in node.get("content", []) or [])
    if t == "text":
        return node.get("text", "")
    if t in ("paragraph", "heading"):
        return kids + "\n\n"
    if t == "listItem":
        return "• " + kids.strip() + "\n"
    if t in ("bulletList", "orderedList"):
        return kids + "\n"
    if t == "hardBreak":
        return "\n"
    if t == "codeBlock":
        return kids + "\n\n"
    if t in ("mention", "emoji"):
        return (node.get("attrs") or {}).get("text", "")
    if t == "inlineCard":
        return (node.get("attrs") or {}).get("url", "")
    return kids


def ticket(key: str) -> dict:
    if not re.fullmatch(r"[A-Z][A-Z0-9_]+-\d+", key or ""):
        raise WorkboardError("Not a Jira key", 400)
    conf = _need_jira()
    it = _jira(conf, f"/rest/api/3/issue/{key}",
               {"fields": "summary,status,issuetype,priority,parent,assignee,reporter,labels,description,comment,subtasks,updated,created"})
    f = it.get("fields") or {}
    comments = [{"author": (c.get("author") or {}).get("displayName") or "", "at": c.get("created") or "",
                 "body": _adf_text(c.get("body")).strip()} for c in (f.get("comment") or {}).get("comments", [])[-10:]]
    note = _active_notes().get(key)
    return {
        "key": key, "title": f.get("summary") or "", "status": (f.get("status") or {}).get("name"),
        "type": (f.get("issuetype") or {}).get("name"), "priority": (f.get("priority") or {}).get("name"),
        "epic": ((f.get("parent") or {}).get("fields") or {}).get("summary"),
        "assignee": (f.get("assignee") or {}).get("displayName"), "reporter": (f.get("reporter") or {}).get("displayName"),
        "labels": f.get("labels") or [], "created": f.get("created"), "updated": f.get("updated"),
        "description": _adf_text(f.get("description")).strip(), "comments": comments,
        "subtasks": [{"key": s.get("key"), "title": (s.get("fields") or {}).get("summary") or "",
                      "done": (((s.get("fields") or {}).get("status") or {}).get("statusCategory") or {}).get("key") == "done"}
                     for s in f.get("subtasks") or []],
        "note": note, "url": f"{conf['base']}/browse/{key}",
    }


# ---------------------------------------------------------------- agent notes

def _notes_path() -> Path:
    return data_dir() / "notes.json"


def _load_notes() -> dict:
    try:
        return json.loads(_notes_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _active_notes() -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=NOTE_TTL_DAYS)
    return {k: v for k, v in _load_notes().items() if (_parse(v.get("at")) or cutoff) > cutoff}


def add_note(key: str, text: str, title: str = "", by: str = "Hermes") -> dict:
    """What the agent did on a ticket ("built, PR #212 open"). Shown on the card and in Focus."""
    key = (key or "").strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]+-\d+", key):
        raise WorkboardError("key must be a Jira key like SJT-2020", 400)
    text = (text or "").strip()
    if not text:
        raise WorkboardError("text is required", 400)
    with _file_lock():
        notes = _active_notes()
        notes[key] = {"text": text[:280], "title": title[:200], "by": by[:40], "at": _iso_now()}
        fd, tmp = tempfile.mkstemp(dir=data_dir(), prefix=".tmp.", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(notes, fh)
        os.chmod(tmp, 0o600)
        os.replace(tmp, _notes_path())
    _cache.pop("board", None)
    return {"key": key, **notes[key]}


def clear_note(key: str) -> bool:
    with _file_lock():
        notes = _active_notes()
        gone = notes.pop((key or "").upper(), None) is not None
        if gone:
            fd, tmp = tempfile.mkstemp(dir=data_dir(), prefix=".tmp.", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(notes, fh)
            os.chmod(tmp, 0o600)
            os.replace(tmp, _notes_path())
    _cache.pop("board", None)
    return gone


# ---------------------------------------------------------------- Asana

def asana_tasks() -> dict:
    tok = asana_token()
    if not tok:
        return {"configured": False, "tasks": []}
    h = {"Authorization": f"Bearer {tok}"}
    fields = "name,due_on,completed,permalink_url,projects.name,parent.name,modified_at"
    out = []
    for ws in _http("GET", f"{ASANA_API}/workspaces", headers=h).get("data", []):
        page = _http("GET", f"{ASANA_API}/tasks", headers=h,
                     params={"assignee": "me", "workspace": ws["gid"], "completed_since": "now",
                             "opt_fields": fields, "limit": 100})
        for t in page.get("data", []):
            projects = [p.get("name") for p in t.get("projects", []) if p.get("name")]
            out.append({"gid": t["gid"], "title": t.get("name") or "", "due": t.get("due_on"),
                        "project": projects[0] if projects else ws.get("name", ""),
                        "parent": (t.get("parent") or {}).get("name"), "url": t.get("permalink_url") or ""})
    today = datetime.now().date().isoformat()
    out.sort(key=lambda t: (t["due"] is None, t["due"] or "", t["title"].lower()))
    for t in out:
        t["overdue"] = bool(t["due"] and t["due"] < today)
    return {"configured": True, "tasks": out}


def asana_task(gid: str) -> dict:
    if not re.fullmatch(r"\d+", gid or ""):
        raise WorkboardError("Not an Asana task id", 400)
    tok = asana_token()
    if not tok:
        raise WorkboardError("Asana isn't set up", 409)
    h = {"Authorization": f"Bearer {tok}"}
    t = _http("GET", f"{ASANA_API}/tasks/{gid}", headers=h,
              params={"opt_fields": "name,notes,due_on,permalink_url,projects.name,assignee.name"}).get("data", {})
    stories = _http("GET", f"{ASANA_API}/tasks/{gid}/stories", headers=h,
                    params={"opt_fields": "text,created_by.name,created_at,type"}).get("data", [])
    return {"gid": gid, "title": t.get("name") or "", "due": t.get("due_on"), "description": t.get("notes") or "",
            "project": ", ".join(p.get("name", "") for p in t.get("projects", [])), "url": t.get("permalink_url") or "",
            "comments": [{"author": (s.get("created_by") or {}).get("name") or "", "at": s.get("created_at") or "",
                          "body": s.get("text") or ""} for s in stories if s.get("type") == "comment"][-10:]}


# ---------------------------------------------------------------- status

def status() -> dict:
    try:
        jira = bool(jira_conf())
        err = None
    except WorkboardError as e:
        jira, err = False, str(e)
    return {"jira": jira, "jira_error": err, "asana": bool(asana_token()), "version": plugin_version()}


def plugin_version() -> str:
    try:
        for line in (Path(__file__).resolve().parent / "plugin.yaml").read_text(encoding="utf-8").splitlines():
            if line.startswith("version:"):
                return line.split(":", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return ""
