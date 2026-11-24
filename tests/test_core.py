"""Tests for workboard_core, the agent tools and the panel routes. Jira/Asana are faked at core._http."""
import importlib
import json
import os
import stat
import sys
import types
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

NOW = datetime.now(timezone.utc)


def jtime(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000-0000")


def issue(key, summary, status_id, cat="indeterminate", **f):
    fields = {"summary": summary, "status": {"id": status_id, "name": f.pop("status_name", status_id), "statusCategory": {"key": cat}},
              "issuetype": {"name": f.pop("type", "Story")}, "priority": {"name": f.pop("priority", "Major")},
              "updated": jtime(NOW), "customfield_10004": f.pop("points", 3), "customfield_10020": f.pop("sprints", []), **f}
    return {"key": key, "fields": fields}


class FakeAPI:
    def __init__(self):
        self.calls = []
        self.issues = [
            issue("SJT-1", "Build the thing", "1", "new", status_name="Open"),
            issue("SJT-2", "Review me", "10434", status_name="Code Review",
                  parent={"key": "SJT-100", "fields": {"summary": "Platform — core work"}},
                  sprints=[{"name": "S23", "state": "closed"}, {"name": "S24", "state": "active"}]),
            issue("SJT-3", "QA me", "10104", status_name="Ready for Alpha Testing", type="Bug"),
            issue("SJT-4", "Shipped", "6", "done", status_name="Closed"),
            issue("SJT-5", "Odd status", "999", "indeterminate", status_name="Mystery"),
        ]
        self.history = {"SJT-2": [{"created": jtime(NOW - timedelta(hours=1)), "author": {"displayName": "Bradon"},
                                   "items": [{"field": "status", "fromString": "In Progress", "toString": "Code Review", "to": "10434"},
                                             {"field": "assignee", "toString": "x"}]},
                                  {"created": jtime(NOW - timedelta(days=9)), "author": {"displayName": "Old"},
                                   "items": [{"field": "status", "fromString": "Open", "toString": "In Progress", "to": "3"}]}]}
        self.asana = [{"gid": "11", "name": "Later task", "due_on": "2999-01-01", "projects": [{"name": "Proj"}]},
                      {"gid": "12", "name": "Late task", "due_on": "2000-01-01", "projects": []},
                      {"gid": "13", "name": "No date", "due_on": None, "projects": [{"name": "Proj"}]}]
        self.pages = 1

    def __call__(self, method, url, *, headers=None, params=None, timeout=25):
        self.calls.append({"url": url, "params": params, "headers": headers})
        u = urllib.parse.urlparse(url)
        p = u.path
        if p == "/rest/agile/1.0/board":
            return {"values": [{"id": 7, "name": "Kanban thing", "type": "kanban"}, {"id": 38, "name": "Team Board", "type": "scrum"}], "isLast": True}
        if p.endswith("/configuration"):
            return {"name": "Team Board", "filter": {"id": "11224"},
                    "estimation": {"field": {"fieldId": "customfield_10004"}},
                    "columnConfig": {"columns": [
                        {"name": "To Do", "statuses": [{"id": "1"}, {"id": "10004"}]},
                        {"name": "In Progress", "statuses": [{"id": "3"}]},
                        {"name": "Code Review", "statuses": [{"id": "10434"}]},
                        {"name": "QA", "statuses": [{"id": "10104"}]},
                        {"name": "Done", "statuses": [{"id": "6"}]},
                        {"name": "Empty", "statuses": []}]}}
        if p == "/rest/api/3/field":
            return [{"id": "summary", "schema": {}}, {"id": "customfield_10020", "schema": {"custom": "com.pyxis.greenhopper.jira:gh-sprint"}}]
        if p == "/rest/api/3/search/jql":
            if params.get("expand") == "changelog":
                return {"issues": [{**i, "changelog": {"histories": self.history.get(i["key"], [])}} for i in self.issues], "isLast": True}
            if self.pages == 2 and not params.get("nextPageToken"):
                return {"issues": self.issues[:2], "isLast": False, "nextPageToken": "p2"}
            if params.get("nextPageToken") == "p2":
                return {"issues": self.issues[2:], "isLast": True}
            return {"issues": self.issues, "isLast": True}
        if p.startswith("/rest/api/3/issue/"):
            return {"key": p.rsplit("/", 1)[1], "fields": {
                "summary": "Build the thing", "status": {"name": "Open"}, "issuetype": {"name": "Story"},
                "description": {"type": "doc", "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "Hello "}, {"type": "mention", "attrs": {"text": "@Dom"}}]},
                    {"type": "bulletList", "content": [{"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "one"}]}]}]}]},
                "comment": {"comments": [{"author": {"displayName": "Dom"}, "created": "2026-11-20", "body": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "lgtm"}]}]}}]},
                "subtasks": [{"key": "SJT-9", "fields": {"summary": "sub", "status": {"statusCategory": {"key": "done"}}}}]}}
        if u.netloc == "app.asana.com":
            if p.endswith("/workspaces"):
                return {"data": [{"gid": "ws", "name": "Work"}]}
            if p.endswith("/tasks"):
                return {"data": self.asana}
            if p.endswith("/stories"):
                return {"data": [{"type": "comment", "text": "hi", "created_by": {"name": "T"}}, {"type": "system", "text": "moved"}]}
            return {"data": {"name": "Late task", "notes": "do it", "projects": [{"name": "Proj"}]}}
        raise AssertionError(f"unexpected {url}")


@pytest.fixture
def core(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBOARD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("JIRA_BASE_URL", "https://acme.atlassian.net/")
    monkeypatch.setenv("JIRA_EMAIL", "me@acme.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "tok")
    monkeypatch.setenv("ASANA_ACCESS_TOKEN", "atok")
    import workboard_core as c
    importlib.reload(c)
    settings = {}
    monkeypatch.setattr(c, "_plugin_settings", lambda: settings)
    c._test_settings = settings
    return c


@pytest.fixture
def fake(core, monkeypatch):
    f = FakeAPI()
    monkeypatch.setattr(core, "_http", f)
    return f


def jql_calls(fake):
    return [c["params"]["jql"] for c in fake.calls if c["params"] and "jql" in c["params"]]


# ---------------------------------------------------------------- config


def test_jira_conf_from_env_and_settings(core):
    conf = core.jira_conf()
    assert conf["base"] == "https://acme.atlassian.net" and conf["auth"].startswith("Basic ")
    core._test_settings["jira_base_url"] = "http://insecure"
    with pytest.raises(core.WorkboardError):
        core.jira_conf()


def test_missing_jira_is_a_clear_409(core, monkeypatch):
    monkeypatch.delenv("JIRA_API_TOKEN")
    assert core.status()["jira"] is False
    with pytest.raises(core.WorkboardError) as e:
        core.board()
    assert e.value.status == 409


def test_done_days_clamped(core):
    core._test_settings["done_days"] = "500"
    assert core.done_days() == 60
    core._test_settings["done_days"] = "x"
    assert core.done_days() == 7


# ---------------------------------------------------------------- board


def test_board_uses_board_columns_and_filter(core, fake):
    b = core.board()
    assert b["board"] == {"id": 38, "name": "Team Board"}  # first scrum board when none chosen
    assert b["columns"] == ["To Do", "In Progress", "Code Review", "QA", "Done"]  # empty column dropped
    col = {c["key"]: c["column"] for c in b["cards"]}
    assert col == {"SJT-1": "To Do", "SJT-2": "Code Review", "SJT-3": "QA", "SJT-4": "Done", "SJT-5": "In Progress"}
    jql = jql_calls(fake)[0]
    assert jql.startswith("filter = 11224 AND assignee = currentUser() AND sprint in openSprints()")
    assert "statusCategoryChangedDate >= -7d" in jql


def test_card_fields(core, fake):
    c = {x["key"]: x for x in core.board()["cards"]}
    assert c["SJT-2"]["epic"] == "Platform — core work" and c["SJT-2"]["sprint"] == "S24"
    assert c["SJT-2"]["points"] == 3 and c["SJT-3"]["type"] == "Bug" and c["SJT-4"]["done"] is True
    assert c["SJT-1"]["url"] == "https://acme.atlassian.net/browse/SJT-1"


def test_board_by_name_and_everyone(core, fake):
    core._test_settings.update({"jira_board": "team board", "only_mine": False, "done_days": 3})
    b = core.board()
    jql = jql_calls(fake)[0]
    assert b["board"]["id"] == 38 and "assignee" not in jql and "-3d" in jql
    core._test_settings["jira_board"] = "nope"
    with pytest.raises(core.WorkboardError):
        core.board()


def test_board_cache_and_refresh(core, fake):
    core.board()
    n = len(jql_calls(fake))
    core.board()
    assert len(jql_calls(fake)) == n
    core.board(refresh=True)
    assert len(jql_calls(fake)) == n + 1


def test_search_follows_pages(core, fake):
    fake.pages = 2
    assert len(core.board()["cards"]) == 5


# ---------------------------------------------------------------- activity + notes


def test_activity_status_moves_within_window(core, fake):
    ev = core.activity()
    assert [(e["key"], e["column"], e["by"]) for e in ev] == [("SJT-2", "Code Review", "Bradon")]


def test_notes_show_on_cards_and_in_activity(core, fake):
    core.add_note("sjt-1", "built, PR #212 open", title="Build the thing")
    card = {c["key"]: c for c in core.board()["cards"]}["SJT-1"]
    assert card["note"]["text"] == "built, PR #212 open"
    ev = core.activity()
    assert ev[0]["kind"] == "note" and ev[0]["key"] == "SJT-1"  # newest first
    assert core.clear_note("SJT-1") and "note" not in {c["key"]: c for c in core.board()["cards"]}["SJT-1"]


def test_notes_validate_and_store_owner_only(core):
    with pytest.raises(core.WorkboardError):
        core.add_note("not a key", "x")
    with pytest.raises(core.WorkboardError):
        core.add_note("SJT-1", "  ")
    core.add_note("SJT-1", "x" * 500)
    p = core.data_dir() / "notes.json"
    assert stat.S_IMODE(core.data_dir().stat().st_mode) == 0o700
    assert stat.S_IMODE(p.stat().st_mode) == 0o600 and len(json.loads(p.read_text())["SJT-1"]["text"]) == 280


def test_old_notes_expire(core):
    core.add_note("SJT-1", "x")
    p = core.data_dir() / "notes.json"
    d = json.loads(p.read_text())
    d["SJT-1"]["at"] = (NOW - timedelta(days=20)).isoformat()
    p.write_text(json.dumps(d))
    assert core._active_notes() == {}


def test_note_invalidates_board_cache(core, fake):
    core.board()
    n = len(jql_calls(fake))
    core.add_note("SJT-1", "x")
    core.board()
    assert len(jql_calls(fake)) == n + 1


# ---------------------------------------------------------------- detail


def test_ticket_detail_flattens_adf(core, fake):
    t = core.ticket("SJT-1")
    assert "Hello @Dom" in t["description"] and "• one" in t["description"]
    assert t["comments"][0]["body"] == "lgtm" and t["subtasks"][0]["done"] is True
    with pytest.raises(core.WorkboardError):
        core.ticket("../../etc")


# ---------------------------------------------------------------- asana


def test_asana_sorted_with_overdue(core, fake):
    a = core.asana_tasks()
    assert [t["title"] for t in a["tasks"]] == ["Late task", "Later task", "No date"]
    assert a["tasks"][0]["overdue"] is True and a["tasks"][1]["overdue"] is False
    assert a["tasks"][0]["project"] == "Work"  # falls back to workspace


def test_asana_optional(core, fake, monkeypatch):
    monkeypatch.delenv("ASANA_ACCESS_TOKEN")
    assert core.asana_tasks() == {"configured": False, "tasks": []}


def test_asana_detail_only_comments(core, fake):
    d = core.asana_task("12")
    assert d["description"] == "do it" and [c["body"] for c in d["comments"]] == ["hi"]
    with pytest.raises(core.WorkboardError):
        core.asana_task("abc")


def test_http_errors_are_readable(core, monkeypatch):
    import urllib.error

    def boom(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 401, "no", {}, None)
    monkeypatch.setattr(core.urllib.request, "urlopen", boom)
    with pytest.raises(core.WorkboardError) as e:
        core._http("GET", "https://acme.atlassian.net/x")
    assert e.value.status == 401 and "token" in str(e.value)


# ---------------------------------------------------------------- tools + routes


@pytest.fixture
def tools(core):
    pkg = types.ModuleType("wb_pkg")
    pkg.__path__ = [str(ROOT)]
    sys.modules["wb_pkg"] = pkg
    sys.modules["wb_pkg.workboard_core"] = core
    spec = importlib.util.spec_from_file_location("wb_pkg.tools", ROOT / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "wb_pkg"
    spec.loader.exec_module(mod)
    return mod


def test_tools_register_with_check(tools, core, monkeypatch):
    seen = []

    class Ctx:
        def register_tool(self, **kw):
            seen.append(kw)
    tools.register(Ctx())
    assert [s["name"] for s in seen] == ["workboard_board", "workboard_note"]
    assert seen[0]["check_fn"]() is True
    monkeypatch.delenv("JIRA_API_TOKEN")
    assert seen[0]["check_fn"]() is False


def test_board_tool_groups_and_filters(tools, fake):
    h = dict((s["name"], fn) for s, fn in tools.TOOLS)
    out = h["workboard_board"]({})
    assert list(out["columns"]) == ["To Do", "In Progress", "Code Review", "QA", "Done"]
    qa = h["workboard_board"]({"column": "qa"})
    assert list(qa["columns"]) == ["QA"] and qa["columns"]["QA"][0]["key"] == "SJT-3"
    assert h["workboard_board"]({"column": "nope"})["ok"] is False


def test_note_tool_errors_are_json(tools, fake):
    handler = tools._wrap(tools._note)
    assert json.loads(handler({"key": "bad", "text": "x"}))["ok"] is False
    assert json.loads(handler({"key": "SJT-1", "text": "built"}))["note"]["by"] == "Hermes"


def test_routes(core, fake):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    sys.modules["workboard_core"] = core
    spec = importlib.util.spec_from_file_location("wb_api", ROOT / "dashboard" / "plugin_api.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    app = fastapi.FastAPI()
    app.include_router(m.router)
    c = TestClient(app)
    assert c.get("/status").json()["jira"] is True
    assert len(c.get("/board").json()["cards"]) == 5
    assert c.get("/activity").json()["events"][0]["key"] == "SJT-2"
    assert c.get("/ticket/SJT-1").json()["title"] == "Build the thing"
    assert c.get("/ticket/bad").status_code == 400
    assert len(c.get("/asana").json()["tasks"]) == 3
    core.add_note("SJT-1", "x")
    assert c.delete("/note/SJT-1").json() == {"cleared": True}
    body = json.dumps(c.get("/board").json()) + json.dumps(c.get("/status").json())
    assert "tok" not in body.replace("token", "")  # no secrets in responses


def test_versions_agree():
    import re
    import yaml
    manifest = yaml.safe_load((ROOT / "plugin.yaml").read_text())["version"]
    js = re.search(r"const VERSION = '([^']+)'", (ROOT / "desktop" / "plugin.js").read_text()).group(1)
    log = re.search(r"^## (\S+)", (ROOT / "CHANGELOG.md").read_text(), re.M).group(1)
    assert manifest == js == log
