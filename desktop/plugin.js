/**
 * Workboard: your Jira board (+ Asana) as a Hermes Desktop page.
 *
 * Two views over the same data:
 *  - Board: the Jira board's own columns, your tickets in its open sprints, recent Done.
 *  - Focus: what's in flight (In Progress / Code Review style columns), what just moved
 *    (Jira history + notes the agent left), and a compact rest-of-board.
 * Asana tasks sit under both. All data comes from the gateway via ctx.rest.
 */
import { Button, Codicon, Input, PALETTE_AREA, ROUTES_AREA, SIDEBAR_NAV_AREA, host } from '@hermes/plugin-sdk'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'workboard'
const PAGE = '/workboard'
const VERSION = '0.1.0'
const POLL_MS = 2 * 60 * 1000
const FRESH_MS = 24 * 60 * 60 * 1000 // notes/moves younger than this glow
const FOCUS_MORE = 8 // cards per compact column in Focus before "+N more"

let api = null

const qs = o => Object.entries(o).filter(([, v]) => v !== undefined && v !== null).map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join('&')
const rest = {
  get: (path, params = {}) => api.rest(Object.keys(params).length ? `${path}?${qs(params)}` : path),
  del: path => api.rest(path, { method: 'DELETE' })
}
const errText = e => {
  const s = String(e?.message || e)
  const m = s.match(/"detail"\s*:\s*"((?:[^"\\]|\\.)*)"/)
  return m ? m[1].replace(/\\"/g, '"') : s
}
const openUrl = url => (api?.os?.openExternal ? api.os.openExternal(url) : Promise.resolve(false)).then(ok => {
  if (!ok) window.open(url, '_blank', 'noopener')
})
const ago = iso => {
  const t = Date.parse(iso)
  if (!t) return ''
  const m = Math.round((Date.now() - t) / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.round(m / 60)
  return h < 24 ? `${h}h ago` : `${Math.round(h / 24)}d ago`
}
const fresh = iso => Date.now() - (Date.parse(iso) || 0) < FRESH_MS
const shortEpic = e => (e || '').split(' — ')[0].split(' (')[0]

/** Columns whose cards count as "in flight" for Focus: everything between the first and last column. */
// 5+ columns (To Do, In Progress, Code Review, QA, Done): the middle ones minus the last-but-one (QA waits on others).
const flightColumns = cols => (cols.length >= 5 ? cols.slice(1, -2) : cols.length >= 3 ? cols.slice(1, -1) : cols)

// ------------------------------------------------------------------ styles

const S = {
  muted: { color: 'var(--ui-text-tertiary)' },
  faint: { color: 'var(--ui-text-quaternary)' },
  border: '1px solid var(--ui-stroke-secondary)',
  section: { display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, fontWeight: 700, letterSpacing: '.07em', textTransform: 'uppercase', color: 'var(--ui-text-tertiary)' },
  count: { background: 'var(--ui-bg-secondary)', borderRadius: 4, padding: '0 6px', fontWeight: 600, color: 'var(--ui-text-secondary)', fontSize: 11 },
  column: { flex: 1, minWidth: 220, display: 'flex', flexDirection: 'column', background: 'color-mix(in srgb, var(--ui-bg-secondary) 45%, transparent)', borderRadius: 10, padding: 8, minHeight: 0 }
}
const JIRA = '#4c7ef3'
const ASANA = '#e8628a'
const GLOW = '#2fe39a'

function Dot({ color }) {
  return jsx('span', { style: { width: 8, height: 8, borderRadius: '50%', background: color, display: 'inline-block' } })
}

function Tabs({ value, onChange }) {
  const tab = (id, label, icon) => jsxs('button', {
    onClick: () => onChange(id),
    'data-tab': id,
    style: {
      display: 'flex', alignItems: 'center', gap: 6, padding: '5px 12px', fontSize: 12, borderRadius: 6, cursor: 'pointer',
      border: 'none', background: value === id ? 'var(--ui-bg-elevated)' : 'transparent',
      color: value === id ? 'var(--ui-text-primary)' : 'var(--ui-text-tertiary)', fontWeight: value === id ? 600 : 400,
      boxShadow: value === id ? '0 1px 3px rgba(0,0,0,.25)' : 'none'
    },
    children: [jsx(Codicon, { name: icon, size: 13 }), label]
  })
  return jsxs('div', {
    style: { display: 'flex', gap: 2, padding: 2, borderRadius: 8, background: 'var(--ui-bg-secondary)' },
    children: [tab('board', 'Board', 'layout'), tab('focus', 'Focus', 'target')]
  })
}

// ------------------------------------------------------------------ cards

function TicketCard({ c, onOpen, compact, recent }) {
  const glow = (c.note && fresh(c.note.at)) || recent
  const bug = /bug/i.test(c.type)
  const high = /highest|high|critical|major|blocker/i.test(c.priority || '')
  return jsxs('div', {
    'data-card': c.key,
    onClick: () => onOpen({ kind: 'jira', key: c.key }),
    title: c.title,
    style: {
      background: 'var(--ui-bg-elevated)', border: S.border, borderLeft: `3px solid ${bug ? 'var(--ui-red, #e5534b)' : JIRA}`,
      borderRadius: 8, padding: compact ? '6px 9px' : '9px 10px', cursor: 'pointer', fontSize: 12.5,
      boxShadow: glow ? `0 0 0 1px ${GLOW}66, 0 0 16px ${GLOW}22` : 'none'
    },
    children: [
      jsx('div', {
        style: { color: 'var(--ui-text-primary)', lineHeight: 1.35, display: '-webkit-box', WebkitLineClamp: compact ? 1 : 3, WebkitBoxOrient: 'vertical', overflow: 'hidden' },
        children: c.title
      }),
      !compact && c.epic && jsx('span', {
        style: { display: 'inline-block', marginTop: 6, fontSize: 10.5, padding: '1px 6px', borderRadius: 4, background: `color-mix(in srgb, ${JIRA} 18%, transparent)`, color: 'var(--ui-text-secondary)', maxWidth: '100%', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
        children: shortEpic(c.epic)
      }),
      jsxs('div', {
        style: { display: 'flex', alignItems: 'center', gap: 6, marginTop: compact ? 3 : 7, fontSize: 11 },
        children: [
          jsx('span', { style: { color: JIRA, fontWeight: 600, textDecoration: c.done ? 'line-through' : 'none' }, children: c.key }),
          high && jsx('span', { title: c.priority, style: { color: '#f0883e' }, children: '⇈' }),
          jsx('span', { style: { flex: 1 } }),
          c.points != null && jsx('span', { style: S.count, children: String(c.points) })
        ]
      }),
      !compact && c.note && jsx('div', { 'data-note': c.key, style: { marginTop: 6, fontSize: 11, color: GLOW }, children: `● ${c.note.text} · ${ago(c.note.at)}` })
    ]
  })
}

function AsanaCard({ t, onOpen }) {
  return jsxs('div', {
    'data-asana': t.gid,
    onClick: () => onOpen({ kind: 'asana', gid: t.gid }),
    style: { background: 'var(--ui-bg-elevated)', border: S.border, borderLeft: `3px solid ${ASANA}`, borderRadius: 8, padding: '8px 10px', cursor: 'pointer', fontSize: 12.5 },
    children: [
      jsx('div', { style: { color: 'var(--ui-text-primary)', lineHeight: 1.35, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }, children: t.title }),
      jsxs('div', {
        style: { display: 'flex', gap: 6, marginTop: 6, fontSize: 11, alignItems: 'center' },
        children: [
          jsx('span', { style: { color: ASANA, fontWeight: 600 }, children: 'ASANA' }),
          jsx('span', { style: { ...S.faint, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: t.project }),
          jsx('span', { style: { flex: 1 } }),
          t.due && jsx('span', { style: { color: t.overdue ? 'var(--ui-red, #e5534b)' : 'var(--ui-text-tertiary)' }, children: t.due })
        ]
      })
    ]
  })
}

// ------------------------------------------------------------------ views

function Column({ name, cards, onOpen, recentKeys, compact, limit, sub }) {
  const shown = limit ? cards.slice(0, limit) : cards
  return jsxs('div', {
    'data-column': name,
    style: S.column,
    children: [
      jsxs('div', { style: { ...S.section, padding: '2px 2px 8px' }, children: [name, jsx('span', { style: S.count, children: cards.length }), sub && jsx('span', { style: { ...S.faint, fontWeight: 400, textTransform: 'none', letterSpacing: 0 }, children: sub })] }),
      jsxs('div', {
        style: { display: 'flex', flexDirection: 'column', gap: compact ? 5 : 8, overflowY: 'auto', minHeight: 0 },
        children: [
          ...shown.map(c => jsx(TicketCard, { c, onOpen, compact, recent: recentKeys.has(c.key) }, c.key)),
          cards.length === 0 && jsx('div', { style: { ...S.faint, fontSize: 12, padding: 6 }, children: 'Nothing here' }),
          limit && cards.length > limit && jsx('div', { style: { ...S.faint, fontSize: 11, padding: 4 }, children: `+${cards.length - limit} more` })
        ]
      })
    ]
  })
}

function BoardView({ data, cards, onOpen, recentKeys }) {
  const last = data.columns[data.columns.length - 1]
  return jsx('div', {
    style: { display: 'flex', gap: 10, minHeight: 0, height: '100%', overflowX: 'auto' },
    children: data.columns.map(col => jsx(Column, {
      name: col, cards: cards.filter(c => c.column === col), onOpen, recentKeys,
      sub: col === last && data.done_days ? `last ${data.done_days} days` : null
    }, col))
  })
}

function ActivityFeed({ events, onOpen }) {
  return jsxs('div', {
    'data-activity': true,
    style: { background: 'color-mix(in srgb, var(--ui-bg-secondary) 45%, transparent)', borderRadius: 10, padding: '8px 12px', overflowY: 'auto', minHeight: 0 },
    children: [
      jsxs('div', { style: { ...S.section, paddingBottom: 6 }, children: [jsx(Dot, { color: GLOW }), 'Activity', jsx('span', { style: { ...S.faint, fontWeight: 400, textTransform: 'none', letterSpacing: 0 }, children: 'last 3 days' })] }),
      events.length === 0 && jsx('div', { style: { ...S.faint, fontSize: 12 }, children: 'Nothing moved lately.' }),
      ...events.map((e, i) => jsxs('div', {
        onClick: () => onOpen({ kind: 'jira', key: e.key }),
        style: { padding: '7px 0', borderBottom: S.border, cursor: 'pointer', fontSize: 12, lineHeight: 1.4 },
        children: [
          jsxs('div', { children: [
            jsx('span', { style: { color: JIRA, fontWeight: 600 }, children: e.key }), ' ',
            e.kind === 'note'
              ? jsx('span', { style: { color: GLOW }, children: `${e.by}: ${e.text}` })
              : jsx('span', { style: S.muted, children: `→ ${e.column || e.to}${e.by ? ` · ${e.by}` : ''}` })
          ] }),
          jsx('div', { style: { ...S.faint, fontSize: 11, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }, children: `${ago(e.at)} · ${e.title}` })
        ]
      }, `${e.key}-${e.at}-${i}`))
    ]
  })
}

function FocusView({ data, cards, events, onOpen, recentKeys }) {
  const flight = flightColumns(data.columns)
  const rest = data.columns.filter(c => !flight.includes(c))
  const now = flight.flatMap(col => cards.filter(c => c.column === col))
  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', gap: 14, minHeight: 0 },
    children: [
      jsxs('div', {
        style: { display: 'flex', gap: 14, minHeight: 0 },
        children: [
          jsxs('div', {
            'data-now': true,
            style: { flex: 2, minWidth: 0 },
            children: [
              jsxs('div', { style: { ...S.section, paddingBottom: 8 }, children: ['▶ Now', jsx('span', { style: S.count, children: now.length }), jsx('span', { style: { ...S.faint, fontWeight: 400, textTransform: 'none', letterSpacing: 0 }, children: flight.join(' + ') })] }),
              jsxs('div', {
                style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 8 },
                children: [
                  ...now.map(c => jsxs('div', { children: [jsx(TicketCard, { c, onOpen, recent: recentKeys.has(c.key) }), jsx('div', { style: { ...S.faint, fontSize: 10.5, padding: '3px 2px 0' }, children: c.column })] }, c.key)),
                  now.length === 0 && jsx('div', { style: { ...S.faint, fontSize: 12 }, children: 'Nothing in flight.' })
                ]
              })
            ]
          }),
          jsx('div', { style: { flex: 1, minWidth: 260, maxHeight: 420, display: 'flex' }, children: jsx(ActivityFeed, { events, onOpen }) })
        ]
      }),
      jsx('div', {
        style: { display: 'flex', gap: 10 },
        children: rest.map(col => jsx(Column, { name: col, cards: cards.filter(c => c.column === col), onOpen, recentKeys, compact: true, limit: FOCUS_MORE }, col))
      })
    ]
  })
}

function AsanaSection({ asana, filter, onOpen }) {
  const [open, setOpen] = useState(() => api.storage.get('asanaOpen', true))
  if (!asana?.configured) return null
  const tasks = asana.tasks.filter(t => !filter || `${t.title} ${t.project}`.toLowerCase().includes(filter))
  const toggle = () => { api.storage.set('asanaOpen', !open); setOpen(!open) }
  return jsxs('div', {
    'data-asana-section': true,
    style: { marginTop: 16 },
    children: [
      jsxs('div', {
        onClick: toggle,
        style: { ...S.section, cursor: 'pointer', paddingBottom: 8 },
        children: [jsx(Codicon, { name: open ? 'chevron-down' : 'chevron-right', size: 12 }), jsx(Dot, { color: ASANA }), 'Asana', jsx('span', { style: S.count, children: tasks.length })]
      }),
      open && jsx('div', {
        style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 8 },
        children: tasks.map(t => jsx(AsanaCard, { t, onOpen }, t.gid))
      })
    ]
  })
}

// ------------------------------------------------------------------ drawer

function Drawer({ item, onClose, onNoteCleared }) {
  const [d, setD] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => {
    setD(null)
    setErr(null)
    const path = item.kind === 'jira' ? `/ticket/${encodeURIComponent(item.key)}` : `/asana/${encodeURIComponent(item.gid)}`
    rest.get(path).then(setD, e => setErr(errText(e)))
  }, [item])
  useEffect(() => {
    const onKey = e => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  const row = (k, v) => v ? jsxs('div', { style: { display: 'flex', gap: 10, padding: '5px 0', borderBottom: S.border, fontSize: 12 }, children: [jsx('span', { style: { ...S.muted, width: 80, flexShrink: 0 }, children: k }), jsx('span', { children: v })] }) : null
  const clearNote = async () => {
    try {
      await rest.del(`/note/${encodeURIComponent(d.key)}`)
      setD({ ...d, note: null })
      onNoteCleared()
    } catch (e) {
      host.notify({ kind: 'error', message: errText(e) })
    }
  }
  return jsxs('div', {
    'data-drawer': true,
    style: { position: 'absolute', top: 0, right: 0, bottom: 0, width: 'min(460px, 92%)', background: 'var(--ui-bg-elevated)', borderLeft: S.border, boxShadow: '-12px 0 30px rgba(0,0,0,.35)', padding: 16, overflowY: 'auto', zIndex: 20, fontSize: 12.5 },
    children: [
      jsxs('div', {
        style: { display: 'flex', alignItems: 'center', gap: 8 },
        children: [
          jsx('span', { style: { color: item.kind === 'jira' ? JIRA : ASANA, fontWeight: 700 }, children: item.kind === 'jira' ? item.key : 'ASANA' }),
          d?.status && jsx('span', { style: S.count, children: d.status }),
          jsx('span', { style: { flex: 1 } }),
          jsx('button', { title: 'Close', onClick: onClose, style: { background: 'none', border: 'none', color: 'inherit', cursor: 'pointer' }, children: jsx(Codicon, { name: 'close', size: 14 }) })
        ]
      }),
      err && jsx('div', { style: { color: 'var(--ui-red, #e5534b)', marginTop: 12 }, children: err }),
      !d && !err && jsx('div', { style: { ...S.faint, marginTop: 12 }, children: 'Loading…' }),
      d && jsxs('div', {
        children: [
          jsx('div', { style: { fontSize: 15, fontWeight: 600, color: 'var(--ui-text-primary)', margin: '10px 0 12px', lineHeight: 1.35 }, children: d.title }),
          d.note && jsxs('div', {
            'data-drawer-note': true,
            style: { color: GLOW, marginBottom: 10, display: 'flex', gap: 8, alignItems: 'center' },
            children: [`● ${d.note.by}: ${d.note.text} · ${ago(d.note.at)}`, jsx('button', { onClick: clearNote, style: { background: 'none', border: 'none', color: 'var(--ui-text-quaternary)', cursor: 'pointer', fontSize: 11 }, children: 'clear' })]
          }),
          row('Type', d.type), row('Priority', d.priority), row('Epic', d.epic), row('Assignee', d.assignee), row('Project', d.project), row('Due', d.due),
          d.description && jsxs('div', { children: [jsx('div', { style: { ...S.section, margin: '14px 0 6px' }, children: 'Description' }), jsx('div', { style: { whiteSpace: 'pre-wrap', lineHeight: 1.5, color: 'var(--ui-text-secondary)' }, children: d.description })] }),
          d.subtasks?.length > 0 && jsxs('div', { children: [jsx('div', { style: { ...S.section, margin: '14px 0 6px' }, children: 'Subtasks' }), ...d.subtasks.map(s => jsx('div', { style: { padding: '3px 0' }, children: `${s.done ? '☑' : '☐'} ${s.key} ${s.title}` }, s.key))] }),
          d.comments?.length > 0 && jsxs('div', { children: [jsx('div', { style: { ...S.section, margin: '14px 0 6px' }, children: 'Comments' }), ...d.comments.map((c, i) => jsxs('div', { style: { padding: '6px 0', borderBottom: S.border }, children: [jsx('div', { style: { ...S.muted, fontSize: 11 }, children: `${c.author} · ${ago(c.at)}` }), jsx('div', { style: { whiteSpace: 'pre-wrap', lineHeight: 1.45, marginTop: 2 }, children: c.body })] }, i))] }),
          d.url && jsx('div', { style: { marginTop: 14 }, children: jsx(Button, { size: 'sm', variant: 'ghost', onClick: () => openUrl(d.url), children: item.kind === 'jira' ? 'Open in Jira ↗' : 'Open in Asana ↗' }) })
        ]
      })
    ]
  })
}

// ------------------------------------------------------------------ page

function Setup({ status, onRecheck }) {
  return jsxs('div', {
    style: { maxWidth: 520, margin: '60px auto', display: 'flex', flexDirection: 'column', gap: 10, fontSize: 13 },
    children: [
      jsx('div', { style: { fontSize: 16, fontWeight: 600 }, children: 'Connect Jira' }),
      jsx('div', { style: S.muted, children: 'Add your Jira site, email and an API token (id.atlassian.com → Security → API tokens) in the plugin settings. Optionally pick a board and add an Asana token.' }),
      status?.jira_error && jsx('div', { style: { color: 'var(--ui-red, #e5534b)' }, children: status.jira_error }),
      jsxs('div', { style: { display: 'flex', gap: 8 }, children: [
        jsx(Button, { size: 'sm', onClick: () => host.navigate(`/settings?tab=plugins&plugin=${ID}`), children: 'Open plugin settings' }),
        jsx(Button, { size: 'sm', variant: 'ghost', onClick: onRecheck, children: 'Check again' })
      ] })
    ]
  })
}

function Workboard() {
  const [status, setStatus] = useState(null)
  const [data, setData] = useState(null)
  const [events, setEvents] = useState([])
  const [asana, setAsana] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)
  const [view, setView] = useState(() => api.storage.get('view', 'board'))
  const [filter, setFilter] = useState('')
  const [open, setOpen] = useState(null)

  const loadStatus = useCallback(() => rest.get('/status').then(setStatus, e => setStatus({ jira: false, jira_error: errText(e) })), [])
  const load = useCallback(async (refresh = false) => {
    setLoading(true)
    try {
      const [b, a, s] = await Promise.all([
        rest.get('/board', refresh ? { refresh: 1 } : {}),
        rest.get('/activity').catch(() => ({ events: [] })),
        rest.get('/asana').catch(() => null)
      ])
      setData(b)
      setEvents(a.events || [])
      setAsana(s)
      setError(null)
    } catch (e) {
      setError(errText(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadStatus()
    const off = host.onEvent?.(`plugin.${ID}.settings.changed`, () => { loadStatus(); load(true) })
    return () => off?.()
  }, [loadStatus, load])
  useEffect(() => {
    if (!status?.jira) return
    load()
    const t = setInterval(() => load(), POLL_MS)
    const onFocus = () => load()
    window.addEventListener('focus', onFocus)
    return () => { clearInterval(t); window.removeEventListener('focus', onFocus) }
  }, [status?.jira, load])

  const changeView = v => { setView(v); api.storage.set('view', v) }
  const q = filter.trim().toLowerCase()
  const cards = useMemo(() => (data?.cards || []).filter(c => !q || `${c.key} ${c.title} ${c.epic || ''}`.toLowerCase().includes(q)), [data, q])
  const recentKeys = useMemo(() => new Set(events.filter(e => e.kind === 'move' && Date.now() - Date.parse(e.at) < 2 * 3600 * 1000).map(e => e.key)), [events])

  if (!status) return jsx('div', { style: { padding: 20, ...S.faint }, children: 'Loading…' })
  if (!status.jira) return jsx(Setup, { status, onRecheck: loadStatus })

  return jsxs('div', {
    style: { position: 'relative', display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, color: 'var(--ui-text-secondary)' },
    children: [
      jsxs('div', {
        style: { display: 'flex', alignItems: 'center', gap: 12, padding: '10px 16px', borderBottom: S.border, flexWrap: 'wrap' },
        children: [
          jsx('div', { style: { fontSize: 15, fontWeight: 600, color: 'var(--ui-text-primary)' }, children: 'Workboard' }),
          data && jsx('span', { style: { ...S.muted, fontSize: 12 }, children: `${data.board.name} · ${data.cards.length} tickets · updated ${ago(data.fetched_at)}` }),
          jsx('span', { style: { flex: 1 } }),
          jsx(Tabs, { value: view, onChange: changeView }),
          jsx('div', { style: { width: 220 }, children: jsx(Input, { placeholder: 'Search tickets…', value: filter, onChange: e => setFilter(e.target.value) }) }),
          jsx(Button, { size: 'sm', variant: 'ghost', onClick: () => load(true), children: jsxs('span', { style: { display: 'flex', alignItems: 'center', gap: 5 }, children: [jsx(Codicon, { name: loading ? 'loading' : 'refresh', size: 13 }), 'Refresh'] }) })
        ]
      }),
      error && jsx('div', { style: { padding: '8px 16px', color: 'var(--ui-red, #e5534b)', fontSize: 12 }, children: error }),
      jsx('div', {
        style: { flex: 1, minHeight: 0, overflowY: 'auto', padding: '12px 16px 18px' },
        children: data && jsxs('div', {
          children: [
            jsxs('div', { style: { ...S.section, paddingBottom: 10 }, children: [jsx(Dot, { color: JIRA }), `Jira · ${data.board.name}`, jsx('span', { style: { ...S.faint, fontWeight: 400, textTransform: 'none', letterSpacing: 0 }, children: data.only_mine ? 'my tickets in open sprints' : 'open sprints' })] }),
            view === 'focus'
              ? jsx(FocusView, { data, cards, events, onOpen: setOpen, recentKeys })
              : jsx('div', { style: { height: '62vh', minHeight: 360 }, children: jsx(BoardView, { data, cards, onOpen: setOpen, recentKeys }) }),
            jsx(AsanaSection, { asana, filter: q, onOpen: setOpen })
          ]
        })
      }),
      open && jsx(Drawer, { item: open, onClose: () => setOpen(null), onNoteCleared: () => load(true) })
    ]
  })
}

export default {
  id: ID,
  name: 'Workboard',
  register(ctx) {
    api = ctx
    ctx.registerMany([
      { id: 'page', area: ROUTES_AREA, title: 'Workboard', data: { path: PAGE }, render: () => jsx(Workboard, {}) },
      { id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: PAGE, label: 'Workboard', codicon: 'project' } },
      { id: 'open', area: PALETTE_AREA, data: { id: `${ID}.open`, label: 'Open Workboard', keywords: ['jira', 'board', 'sprint', 'tickets', 'asana'], run: () => host.navigate(PAGE) } }
    ])
  }
}

