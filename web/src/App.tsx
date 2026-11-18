import { useEffect, useState } from 'react'
import { api } from './api'
import { StoreProvider, useStore, View } from './store'
import { LeftPanel } from './components/Left'
import { ContextBar, GridView, ListView } from './components/Library'
import { FoldersView, TimelineView } from './components/Folders'
import { Inspector } from './components/Inspector'
import { BoardView } from './components/Board'
import { WorkView } from './components/Work'
import { SendView } from './components/Send'
import { Viewer } from './components/Viewer'
import { MobileApp } from './components/Mobile'

const BASE: [View, string][] = [['grid', 'Grid'], ['list', 'List'], ['folders', 'Folders'], ['timeline', 'Timeline'], ['board', 'Board']]

function TopBar() {
  const s = useStore()
  const online = s.agents.filter(a => a.online)
  const ws = s.agents.reduce((n, a) => n + (a.state?.working_set?.length || 0), 0)
  const VIEWS: [View, string][] = [...BASE, s.isLocal ? ['send', 'Send to NAS'] : ['work', 'Working set']]
  return (
    <div className="topbar">
      <span className="brand">LightPhotoLibrary</span>
      {s.isLocal && <span className="pill-ok" title="The library on this PC — works without the NAS" style={{ background: 'rgba(140,184,230,.14)', color: '#8cb8e6' }}><i style={{ background: '#8cb8e6' }} />this PC</span>}
      <nav className="tabs" aria-label="Views">
        {VIEWS.map(([v, l]) => (
          <button key={v} className={s.view === v ? 'on' : ''} onClick={() => s.setView(v)}>
            {l}{v === 'work' && ws ? <span style={{ color: '#E8A33D' }}> {ws}</span> : null}
          </button>
        ))}
      </nav>
      <label className="search">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></svg>
        <input aria-label="Search" placeholder="Search filename, folder, tag, lens, camera…" value={s.filters.q || ''} onChange={e => s.patchFilters({ q: e.target.value })} />
      </label>
      <div className="grow" />
      {s.isLocal ? null : online.length ? <span className="pill-ok" title={online.map(a => a.name).join(', ')}><i />{online.length === 1 ? online[0].name : `${online.length} PCs`} ready</span>
        : <span className="pill-off" title="Start the LightPhotoLibrary agent on your PC"><i />no PC agent</span>}
    </div>
  )
}

function useShortcuts() {
  const s = useStore()
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT')) return
      if (!['grid', 'list', 'board'].includes(s.view) && s.viewer == null) return
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a') { e.preventDefault(); s.selectAll(); return }
      if (e.ctrlKey || e.metaKey || e.altKey) return
      if (!s.sel.length) return
      if (/^[0-5]$/.test(e.key)) { s.meta({ rating: +e.key }); return }
      if (e.key.toLowerCase() === 'r') { s.meta({ rating: -1 }); return }
      const fk = ['F1', 'F2', 'F3', 'F4', 'F5'].indexOf(e.key)
      if (fk >= 0) { e.preventDefault(); s.meta({ toggle_label: fk }); return }
      if (e.key.toLowerCase() === 'e') { s.editInDarktable(s.sel); return }
      if (e.key === 'Escape' && s.viewer == null) s.setSel([])
      if (e.key === 'Enter' && s.viewer == null) s.setViewer(s.sel[0])
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  })
}

function Desktop() {
  const s = useStore()
  useShortcuts()
  const libView = s.view === 'grid' || s.view === 'list'
  return (
    <div className="app">
      <TopBar />
      <div className="main">
        {libView && <LeftPanel />}
        {s.view === 'board' ? <BoardView /> : (
          <div className="center">
            {libView && <ContextBar />}
            {s.view === 'grid' && <GridView />}
            {s.view === 'list' && <ListView />}
            {s.view === 'folders' && <FoldersView />}
            {s.view === 'timeline' && <TimelineView />}
            {s.view === 'work' && <WorkView />}
            {s.view === 'send' && <SendView />}
          </div>
        )}
        {(libView || s.view === 'board') && <Inspector />}
      </div>
      <Viewer />
    </div>
  )
}

function Shell() {
  const s = useStore()
  return (
    <>
      {s.isMobile ? <MobileApp /> : <Desktop />}
      {s.toastMsg && <div className="toast" role="status">{s.toastMsg}</div>}
    </>
  )
}

function Login({ onOk }: { onOk: () => void }) {
  const [pw, setPw] = useState('')
  const [err, setErr] = useState('')
  return (
    <div className="login">
      <form className="card" style={{ width: 320 }} onSubmit={e => { e.preventDefault(); api.login(pw).then(onOk).catch(() => setErr('Wrong password')) }}>
        <span className="brand">LightPhotoLibrary</span>
        <input className="input" type="password" autoFocus placeholder="Password" value={pw} onChange={e => setPw(e.target.value)} aria-label="Password" style={{ height: 40 }} />
        {err && <span className="small" style={{ color: '#ff8b8e' }}>{err}</span>}
        <button className="btn primary" type="submit" style={{ height: 40 }}>Open library</button>
      </form>
    </div>
  )
}

export default function App() {
  const [auth, setAuth] = useState<'checking' | 'ok' | 'login'>('checking')
  useEffect(() => { api.auth().then(r => setAuth(r.ok ? 'ok' : 'login')).catch(() => setAuth('ok')) }, [])
  if (auth === 'checking') return null
  if (auth === 'login') return <Login onOk={() => setAuth('ok')} />
  return <StoreProvider><Shell /></StoreProvider>
}
