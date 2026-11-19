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
import { FullscreenPhoto } from './components/FullscreenPhoto'
import { ShortcutsDialog } from './components/Shortcuts'
import { TabStrip, ViewSwitch } from './components/Tabs'
import { actionFor, typingIn, useShortcutsVersion } from './shortcuts'

function TopBar() {
  const s = useStore()
  const online = s.agents.filter(a => a.online)
  const [keysOpen, setKeysOpen] = useState(false)
  return (
    <>
      <div className="tabbar">
        <span className="brand">LightPhotoLibrary</span>
        {s.isLocal && <span className="pill-ok" title="The library on this PC — works without the NAS" style={{ background: 'rgba(140,184,230,.14)', color: '#8cb8e6' }}><i style={{ background: '#8cb8e6' }} />this PC</span>}
        <TabStrip />
        <button className="btn ghost" onClick={() => setKeysOpen(true)} title="Keyboard shortcuts: see and change them" aria-label="Keyboard shortcuts">
          <svg width="18" height="14" viewBox="0 0 24 18" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true" style={{ verticalAlign: '-2px' }}><rect x="1" y="1" width="22" height="16" rx="3" /><path d="M5 6h1M9 6h1M13 6h1M17 6h2M5 10h1M9 10h1M13 10h1M17 10h2M7 13.5h10" strokeLinecap="round" /></svg>
        </button>
        {keysOpen && <ShortcutsDialog onClose={() => setKeysOpen(false)} />}
        {s.isLocal ? null : online.length ? <span className="pill-ok" title={online.map(a => a.name).join(', ')}><i />{online.length === 1 ? online[0].name : `${online.length} PCs`} ready</span>
          : <span className="pill-off" title="Start the LightPhotoLibrary agent on your PC"><i />no PC agent</span>}
      </div>
      <div className="topbar">
        <ViewSwitch />
        <label className="search">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></svg>
          <input aria-label="Search" placeholder="Search filename, folder, tag, lens, camera…" value={s.filters.q || ''} onChange={e => s.patchFilters({ q: e.target.value })} />
          {s.filters.q && <button className="btn ghost tiny" aria-label="Clear search" onClick={() => s.patchFilters({ q: '' })}>×</button>}
        </label>
      </div>
    </>
  )
}

function useShortcuts() {
  const s = useStore()
  useShortcutsVersion()
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (typingIn(e.target)) return
      const ta = s.fullscreen ? null : actionFor(e, ['tabs'])
      if (ta) {
        e.preventDefault()
        const i = s.tabs.findIndex(t => t.id === s.activeTab)
        if (ta === 'newTab') s.newTab()
        else if (ta === 'closeTab') s.closeTab(s.activeTab)
        else if (ta === 'nextTab') s.switchTab(s.tabs[(i + 1) % s.tabs.length].id)
        else if (ta === 'prevTab') s.switchTab(s.tabs[(i - 1 + s.tabs.length) % s.tabs.length].id)
        else if (ta.startsWith('tab')) { const t = s.tabs[+ta.slice(3) - 1]; if (t) s.switchTab(t.id) }
        return
      }
      if (!['grid', 'list', 'board'].includes(s.view) && s.viewer == null) return
      const a = actionFor(e, ['photos'])
      if (!a) return
      if (s.viewer != null && (a === 'view' || a === 'deselect' || a === 'selectAll')) return  // the large view has its own keys
      if (a === 'selectAll') { if (s.view === 'board') return; e.preventDefault(); s.selectAll(); return }  // the board selects its own things
      if (a === 'fullscreen') {
        const id = s.viewer ?? s.sel[0]
        if (id == null || (s.view === 'board' && s.viewer == null)) return  // the board opens it in board order itself
        e.preventDefault()
        const k = s.items.findIndex(p => p.id === id)
        if (k >= 0) s.setFullscreen({ list: s.items, index: k })
        else { const sp = s.selPhotos; const j = sp.findIndex(p => p.id === id); if (j >= 0) s.setFullscreen({ list: sp, index: j }) }
        return
      }
      if (!s.sel.length) return
      e.preventDefault()
      if (a.startsWith('rate')) s.meta({ rating: +a.slice(4) })
      else if (a === 'reject') s.meta({ rating: -1 })
      else if (a.startsWith('label')) s.meta({ toggle_label: +a.slice(5) })
      else if (a === 'edit') s.editInDarktable(s.sel)
      else if (a === 'hide') { const ps = s.selPhotos; s.hidePhotos(s.sel, !(ps.length === s.sel.length && ps.every(p => p.hidden))) }
      else if (a === 'deselect') s.setSel([])
      else if (a === 'view') s.setViewer(s.sel[0])
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
      <div className="main" key={s.activeTab}>
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
      {s.fullscreen && <FullscreenPhoto key={s.fullscreen.list[s.fullscreen.index]?.id} />}
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
