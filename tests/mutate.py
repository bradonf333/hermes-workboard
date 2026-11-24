#!/usr/bin/env python3
"""Mutation check: break one behaviour at a time and confirm a test fails.
Usage: python3 tests/mutate.py <node_modules dir for the UI test>"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NM = sys.argv[1]
PY = [sys.executable, "-B", "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"]
UI = ["node", "tests/ui_smoke.mjs", NM]

MUTANTS = [
    ("status ids ignored (all by category)", "workboard_core.py", "        if sid in c[\"statuses\"]:\n            return c[\"name\"]", "        pass", PY),
    ("empty board columns kept", "workboard_core.py", "for c in (cfg.get(\"columnConfig\") or {}).get(\"columns\", []) if c.get(\"statuses\")]", "for c in (cfg.get(\"columnConfig\") or {}).get(\"columns\", [])]", PY),
    ("other people's tickets", "workboard_core.py", "    mine = \"assignee = currentUser() AND \" if only_mine() else \"\"\n    return (", "    mine = \"\"\n    return (", PY),
    ("done never ages out", "workboard_core.py", "(statusCategory != Done OR statusCategoryChangedDate >= -{done_days()}d)", "(statusCategory != Done OR statusCategory = Done)", PY),
    ("board ignores its filter", "workboard_core.py", "f\"filter = {bc['filter']} AND {_board_jql()}\"", "_board_jql()", PY),
    ("only first page", "workboard_core.py", "        token = page[\"nextPageToken\"]\n    return out", "        return out\n    return out", PY),
    ("cache never refreshes", "workboard_core.py", "if hit and not refresh and hit[\"key\"] == key", "if hit and hit[\"key\"] == key", PY),
    ("old moves in activity", "workboard_core.py", "            if not at or at < since:", "            if not at:", PY),
    ("non-status changes in activity", "workboard_core.py", "                if it.get(\"field\") != \"status\":\n                    continue", "                pass", PY),
    ("notes never expire", "workboard_core.py", "if (_parse(v.get(\"at\")) or cutoff) > cutoff}", "}", PY),
    ("data folder world-readable", "workboard_core.py", "        os.chmod(d, 0o700)", "        pass", PY),
    ("note keeps stale board", "workboard_core.py", "    _cache.pop(\"board\", None)\n    return {\"key\": key, **notes[key]}", "    return {\"key\": key, **notes[key]}", PY),
    ("path in ticket key", "workboard_core.py", "    if not re.fullmatch(r\"[A-Z][A-Z0-9_]+-\\d+\", key or \"\"):\n        raise WorkboardError(\"Not a Jira key\", 400)", "    pass", PY),
    ("http jira url accepted", "workboard_core.py", "    if not base.startswith(\"https://\"):", "    if False:", PY),
    ("asana overdue wrong", "workboard_core.py", "t[\"overdue\"] = bool(t[\"due\"] and t[\"due\"] < today)", "t[\"overdue\"] = False", PY),
    ("tools offered without jira", "__init__.py", "        return bool(core.jira_conf())", "        return True", PY),
    ("UI: Focus 'Now' includes QA", "desktop/plugin.js", "cols.length >= 5 ? cols.slice(1, -2)", "cols.length >= 5 ? cols.slice(1, -1)", UI),
    ("UI: view not remembered", "desktop/plugin.js", "const changeView = v => { setView(v); api.storage.set('view', v) }", "const changeView = v => { setView(v) }", UI),
    ("UI: no '+N more' cap in Focus", "desktop/plugin.js", "compact: true, limit: FOCUS_MORE }", "compact: true }", UI),
    ("UI: notes hidden", "desktop/plugin.js", "!compact && c.note && jsx('div', { 'data-note'", "false && jsx('div', { 'data-note'", UI),
    ("UI: search ignores titles", "desktop/plugin.js", "`${c.key} ${c.title} ${c.epic || ''}`", "`${c.key}`", UI),
    ("UI: refresh uses cache", "desktop/plugin.js", "rest.get('/board', refresh ? { refresh: 1 } : {})", "rest.get('/board', {})", UI),
    ("UI: Now not in column order", "desktop/plugin.js", "const now = flight.flatMap(col => cards.filter(c => c.column === col))", "const now = cards.filter(c => flight.includes(c.column))", UI),
    ("version drift", "desktop/plugin.js", "const VERSION = '0.1.0'", "const VERSION = '0.0.9'", PY),
]

killed = 0
for name, rel, old, new, cmd in MUTANTS:
    p = ROOT / rel
    src = p.read_text()
    if src.count(old) != 1:
        print(f"BAD MUTANT ({src.count(old)} matches): {name}")
        continue
    backup = p.with_suffix(p.suffix + ".bak")
    shutil.copy(p, backup)
    try:
        p.write_text(src.replace(old, new))
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300)
        ok = r.returncode != 0
        killed += ok
        print(f"{'KILLED  ' if ok else 'SURVIVED'} {name}")
    finally:
        shutil.move(backup, p)
for cache in ROOT.rglob("__pycache__"):
    shutil.rmtree(cache, ignore_errors=True)
print(f"{killed}/{len(MUTANTS)} killed")
sys.exit(0 if killed == len(MUTANTS) else 1)
