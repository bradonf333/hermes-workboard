# Workboard for Hermes

Your Jira board inside [Hermes](https://github.com/NousResearch/hermes-agent)
Desktop, plus your Asana tasks, so you can see what you're working on and where
it stands without opening Jira.

- **Board view.** Your Jira board's own columns (To Do, In Progress, Code
  Review, QA, Done, or whatever your board uses) with the same status mapping,
  showing your tickets in the board's open sprints. Done only keeps the last 7
  days (configurable), so it doesn't grow forever when sprints aren't closed.
- **Focus view.** A second tab: what's in flight right now (the middle columns),
  an **activity feed** of what moved in the last 3 days (from Jira's history)
  and what the agent says it did, and a compact rest-of-board.
- **Agent notes.** When Hermes finishes work on a ticket it can leave a note
  ("built, PR #212 open"). The card glows and shows the note, and it lands in
  the activity feed. Nothing is written to Jira.
- **Asana** tasks assigned to you, sorted by due date, under both views
  (optional, collapsible).
- **Ticket drawer.** Click a card for the description, subtasks and comments,
  with a link to open it in Jira or Asana.
- **Agent tools:** `workboard_board` ("what's in QA?", "what am I working on?")
  and `workboard_note`.

Read-only against Jira and Asana.

## Install

In Hermes Desktop: **Capabilities → Plugins → Install from Git** →
`bradonf333/hermes-workboard`, then switch it on. A **Workboard** link appears
in the sidebar (or ⌘K → *Open Workboard*).

From a terminal on the gateway machine: `hermes plugins install
bradonf333/hermes-workboard` and `hermes plugins enable workboard`; the Desktop
panel is installed by the Desktop app.

If Desktop runs on a different computer than the gateway, install it from
Desktop (that installs both halves). Hermes's Update refreshes only the
gateway's copy in that setup; use Install from Git with **Force reinstall** to
update the page.

## Setup

**Settings → Plugins → Workboard:**

| Setting | Default | |
|---|---|---|
| Jira site | `JIRA_BASE_URL` from `.env` | `https://yourcompany.atlassian.net` |
| Jira email | `JIRA_EMAIL` from `.env` | The account the token belongs to |
| Jira API token | `JIRA_API_TOKEN` | [id.atlassian.com → Security → API tokens](https://id.atlassian.com/manage-profile/security/api-tokens); read access is enough |
| Jira board | your first scrum board | Board name (e.g. `Team Board`) or id |
| Only my tickets | on | Off = everyone's tickets on the board |
| Show Done for (days) | 7 | 0–60 |
| Asana personal access token | `ASANA_ACCESS_TOKEN` | Optional. Asana → Settings → Apps → Developer apps |

Tokens are stored by Hermes as secrets (in `.env`), never shown to the page or
the model.

## How it decides what to show

- **Tickets:** the board's saved filter, AND assigned to you (unless turned
  off), AND in an open sprint, AND either not done or moved to Done within the
  last N days.
- **Columns:** read from the board's configuration, so a status lands in the
  same column as in Jira. Empty columns are skipped; a status not mapped on the
  board goes by its category (to-do, in progress, done).
- **Focus "Now":** the columns between the first and the last two (for To Do /
  In Progress / Code Review / QA / Done: In Progress and Code Review). With
  fewer columns, everything between the first and last.
- **Activity:** status changes on your board tickets from Jira's history in
  the last 3 days, plus agent notes (kept 14 days).
- The page refreshes every 2 minutes, when the window regains focus, and on
  **Refresh**. The backend reuses results for 60 seconds.

## Privacy

- Talks only to your Jira site and `app.asana.com`, with your own tokens.
- Stores only the agent's notes, in
  `~/.hermes/plugin-data/workboard/notes.json` (owner-only).

## Development

```bash
python -m pytest -q                          # backend + tools + routes, no network
node tests/ui_smoke.mjs <node_modules>       # page in jsdom (needs react, react-dom, jsdom)
python tests/mutate.py <node_modules>        # every key behaviour has a failing test when broken
hermes plugins validate .
```

## License

MIT
