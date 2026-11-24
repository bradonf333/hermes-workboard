// UI smoke test: renders desktop/plugin.js in jsdom with React, a stub SDK and a fake backend.
//   node tests/ui_smoke.mjs <path-to-node_modules-with-react-react-dom-jsdom>
import { readFileSync, writeFileSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createRequire } from 'node:module'

const here = dirname(fileURLToPath(import.meta.url))
const nm = resolve(process.argv[2])
const req = createRequire(join(nm, '..', 'x.js'))
const { JSDOM } = req('jsdom')
const dom = new JSDOM('<!doctype html><div id="root" style="height:800px"></div>', { pretendToBeVisual: true })
Object.assign(globalThis, { window: dom.window, document: dom.window.document,
  HTMLElement: dom.window.HTMLElement, Node: dom.window.Node, getComputedStyle: dom.window.getComputedStyle,
  IS_REACT_ACT_ENVIRONMENT: true })
const React = req('react')
const { createRoot } = req('react-dom/client')
const { act } = React

const notes = []
globalThis.__sdk = {
  Button: ({ children, onClick, title }) => React.createElement('button', { onClick, title }, children),
  Input: props => React.createElement('input', props),
  Codicon: ({ name }) => React.createElement('i', { 'data-icon': name }),
  PALETTE_AREA: 'palette', ROUTES_AREA: 'routes', SIDEBAR_NAV_AREA: 'sidebar.nav',
  host: { navigate: p => notes.push(['nav', p]), notify: n => notes.push(['notify', n.kind, n.message]), onEvent: () => () => {} }
}
const tmp = mkdtempSync(join(tmpdir(), 'wb-ui-'))
const src = readFileSync(join(here, '..', 'desktop', 'plugin.js'), 'utf8')
  .replace(/import \{([^}]+)\} from '@hermes\/plugin-sdk'/, 'const {$1} = globalThis.__sdk')
  .replace(/from 'react\/jsx-runtime'/, `from '${pathToFileURL(req.resolve('react/jsx-runtime')).href}'`)
  .replace(/from 'react'/, `from '${pathToFileURL(req.resolve('react')).href}'`)
writeFileSync(join(tmp, 'plugin.mjs'), src)
const plugin = (await import(pathToFileURL(join(tmp, 'plugin.mjs')).href)).default

// ---- fake backend
const iso = (minsAgo) => new Date(Date.now() - minsAgo * 60000).toISOString()
const state = { jira: false, asana: true }
const card = (key, title, column, extra = {}) => ({ key, title, column, status: column, type: 'Story', priority: 'Major', points: 3, epic: 'Platform — core', sprint: 'S24', done: column === 'Done', url: `https://j/browse/${key}`, ...extra })
const board = {
  board: { id: 38, name: 'Team Board' }, columns: ['To Do', 'In Progress', 'Code Review', 'QA', 'Done'], done_days: 7, only_mine: true, fetched_at: iso(0),
  cards: [card('SJT-1', 'Plan the thing', 'To Do'), card('SJT-3', 'Review the thing', 'Code Review'), card('SJT-2', 'Build the thing', 'In Progress', { note: { text: 'built, PR #212 open', by: 'Hermes', at: iso(5) } }), card('SJT-4', 'Bug in QA', 'QA', { type: 'Bug' }), card('SJT-5', 'Shipped thing', 'Done'),
    ...Array.from({ length: 12 }, (_, i) => card(`SJT-${100 + i}`, `QA item ${i}`, 'QA'))]
}
const calls = []
const storage = new Map()
const ctx = {
  storage: { get: (k, d) => (storage.has(k) ? storage.get(k) : d), set: (k, v) => storage.set(k, v) },
  os: { openExternal: async url => (calls.push(['open', url]), true) },
  registerMany: cs => (ctx.contribs = cs),
  rest: async (path, opts = {}) => {
    const method = opts.method || 'GET'
    calls.push([method, path])
    const p = path.split('?')[0]
    if (p === '/status') return { jira: state.jira, asana: state.asana, version: '0.1.0' }
    if (p === '/board') return board
    if (p === '/activity') return { events: [{ key: 'SJT-3', title: 'Review the thing', kind: 'move', by: 'Bradon', column: 'Code Review', to: 'Code Review', at: iso(30) },
      { key: 'SJT-2', title: 'Build the thing', kind: 'note', by: 'Hermes', text: 'built, PR #212 open', at: iso(5) }] }
    if (p === '/asana') return { configured: state.asana, tasks: [{ gid: '11', title: 'Asana task', project: 'Proj', due: '2000-01-01', overdue: true, url: 'https://a/11' }] }
    if (p.startsWith('/ticket/')) return { key: 'SJT-2', title: 'Build the thing', status: 'In Progress', description: 'Desc here', comments: [{ author: 'Dom', at: iso(60), body: 'lgtm' }], subtasks: [], note: { text: 'built, PR #212 open', by: 'Hermes', at: iso(5) }, url: 'https://j/browse/SJT-2' }
    if (p.startsWith('/asana/')) return { gid: '11', title: 'Asana task', description: 'asana desc', comments: [], url: 'https://a/11' }
    if (p.startsWith('/note/') && method === 'DELETE') return { cleared: true }
    throw new Error(`unexpected ${method} ${path}`)
  }
}
plugin.register(ctx)

let failures = 0
const check = (cond, msg) => { console.log(`${cond ? 'PASS' : 'FAIL'} ${msg}`); if (!cond) failures++ }
const text = () => document.body.textContent
const flush = async () => { for (let i = 0; i < 6; i++) await act(async () => { await new Promise(r => setTimeout(r, 5)) }) }
const byText = (sel, t) => [...document.querySelectorAll(sel)].find(el => el.textContent.trim() === t)
const click = async el => { await act(async () => el.dispatchEvent(new window.MouseEvent('click', { bubbles: true }))); await flush() }
const type = async (el, v) => {
  Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(el, v)
  await act(async () => el.dispatchEvent(new window.Event('input', { bubbles: true })))
  await flush()
}
const errors = []
console.error = (...a) => { errors.push(a.join(' ')) }
const cardsIn = col => [...document.querySelectorAll(`[data-column="${col}"] [data-card]`)].map(e => e.getAttribute('data-card'))

const page = ctx.contribs.find(c => c.area === 'routes')
const nav = ctx.contribs.find(c => c.area === 'sidebar.nav')
check(page?.data.path === '/workboard' && nav?.data.label === 'Workboard', 'registers a page + sidebar link')

const root = createRoot(document.getElementById('root'))
await act(async () => root.render(page.render()))
await flush()
check(text().includes('Connect Jira'), 'no Jira -> setup screen')
await click(byText('button', 'Open plugin settings'))
check(notes.some(n => n[0] === 'nav' && n[1].includes('plugin=workboard')), 'setup opens plugin settings')

state.jira = true
await click(byText('button', 'Check again'))
check(text().includes('Team Board') && text().includes('17 tickets'), 'header shows board + count')
check(document.querySelectorAll('[data-column]').length === 5, 'Board view: one column per board column')
check(cardsIn('To Do').join() === 'SJT-1' && cardsIn('Code Review').join() === 'SJT-3' && cardsIn('QA').length === 13, 'cards land in their columns')
check(text().includes('last 7 days'), 'Done column says how far back it goes')
check(document.querySelector('[data-note="SJT-2"]')?.textContent.includes('built, PR #212 open'), 'agent note shows on the card')
check(document.querySelector('[data-asana="11"]') && text().includes('ASANA'), 'Asana tasks shown under the board')
check(storage.get('view') === undefined, 'Board is the default view')

// Focus tab
await click(document.querySelector('[data-tab="focus"]'))
check(storage.get('view') === 'focus', 'Focus choice remembered')
const now = [...document.querySelectorAll('[data-now] [data-card]')].map(e => e.getAttribute('data-card'))
check(now.join() === 'SJT-2,SJT-3', 'Focus "Now" = In Progress + Code Review')
check(document.querySelector('[data-activity]')?.textContent.includes('Hermes: built, PR #212 open') && text().includes('→ Code Review · Bradon'), 'activity feed shows notes + moves')
check(document.querySelectorAll('[data-column]').length === 3 && cardsIn('QA').length === 8 && text().includes('+5 more'), 'Focus: compact To Do / QA / Done with "+N more"')
const glow = document.querySelector('[data-now] [data-card="SJT-3"]').style.boxShadow
check(glow && glow !== 'none', 'recently moved card glows')

// back to Board
await click(document.querySelector('[data-tab="board"]'))
check(document.querySelectorAll('[data-column]').length === 5 && storage.get('view') === 'board', 'back to Board view')

// search
await type(document.querySelector('input'), 'review')
check(cardsIn('Code Review').join() === 'SJT-3' && cardsIn('To Do').length === 0, 'search filters cards')
await type(document.querySelector('input'), '')

// drawer
await click(document.querySelector('[data-card="SJT-2"]'))
check(document.querySelector('[data-drawer]') && text().includes('Desc here') && text().includes('lgtm'), 'drawer loads ticket detail + comments')
await click(byText('button', 'clear'))
check(calls.some(c => c[0] === 'DELETE' && c[1] === '/note/SJT-2') && !document.querySelector('[data-drawer-note]'), 'clearing the note calls the API')
await click(byText('button', 'Open in Jira ↗'))
check(calls.some(c => c[0] === 'open' && c[1] === 'https://j/browse/SJT-2'), 'Open in Jira opens the ticket')
await act(async () => window.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape' })))
await flush()
check(!document.querySelector('[data-drawer]'), 'Esc closes the drawer')

await click(document.querySelector('[data-asana="11"]'))
check(calls.some(c => c[1] === '/asana/11') && text().includes('asana desc'), 'Asana card opens its detail')

// refresh
calls.length = 0
await click(byText('button', 'Refresh'))
check(calls.some(c => c[1] === '/board?refresh=1'), 'Refresh bypasses the cache')

// asana collapse remembered
await click([...document.querySelectorAll('[data-asana-section] div')].find(d => d.textContent.startsWith('Asana')))
check(!document.querySelector('[data-asana="11"]') && storage.get('asanaOpen') === false, 'Asana section collapses (remembered)')

check(errors.filter(e => !/act\(/.test(e)).length === 0, `no React errors (${errors.slice(0, 2).join(' | ')})`)
console.log(failures ? `${failures} FAILED` : 'ALL PASS')
process.exit(failures ? 1 : 0)
