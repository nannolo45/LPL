import { useEffect, useMemo, useRef, useState } from 'react'
import { api, coverUrl, Folder, FoldersResult } from '../api'
import { useStore } from '../store'
import { fmtMonth } from '../fields'
import { RetryImg } from './RetryImg'

// The last folder list is kept (in memory and in the browser) so the page shows at once; it's refreshed in the background.
let lastFolders: FoldersResult | null = null
export const readCached = (): FoldersResult | null => {
  if (lastFolders) return lastFolders
  try { const t = localStorage.getItem('lpl.foldersCache'); return t ? JSON.parse(t) : null } catch { return null }
}
export const writeCached = (r: FoldersResult) => {
  lastFolders = r
  try { localStorage.setItem('lpl.foldersCache', JSON.stringify(r)) } catch { /* full or blocked: memory copy is enough */ }
}

const parentOf = (p: string) => (p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : p ? '' : null)

export function FoldersView() {
  const s = useStore()
  const [all, setAll] = useState<Folder[]>(() => readCached()?.folders || [])
  const [excluded, setExcluded] = useState<string[]>(() => readCached()?.excluded || [])
  const [pendingPreviews, setPendingPreviews] = useState(0)
  const [menu, setMenu] = useState<string | null>(null)
  const [showExcluded, setShowExcluded] = useState(false)
  const showHidden = !!s.filters.show_hidden
  const polls = useRef(0)
  const [picked, setPicked] = useState<string[]>([])
  const [picking, setPicking] = useState(false)   // "Select" mode (for touch screens)
  const lastPick = useRef<string | null>(null)
  const fkey = 'lpl.folder.' + s.activeTab  // each tab remembers where it is in the folders
  const [cur, setCur] = useState<string>(() => localStorage.getItem(fkey) ?? localStorage.getItem('lpl.folder') ?? '')
  const [mode, setMode] = useState<string>(() => localStorage.getItem('lpl.folderSort') || 'custom')
  const [drag, setDrag] = useState<string | null>(null)
  const [over, setOver] = useState<string | null>(null)
  const load = () => api.folders().then(r => { writeCached(r); setAll(r.folders); setExcluded(r.excluded || []); setPendingPreviews(r.previews_pending || 0) }).catch(e => s.toast(e.message))
  useEffect(() => { load() }, [])
  // covers whose preview is still being made: refresh the list a few times so they fill in
  const coversPending = all.some(f => f.covers.some(c => c.pending))
  useEffect(() => {
    if (!coversPending || polls.current > 20) return
    const t = window.setTimeout(() => { polls.current++; load() }, 5000)
    return () => window.clearTimeout(t)
  }, [all])
  useEffect(() => {
    if (!menu) return
    const close = () => setMenu(null)
    window.addEventListener('click', close)
    return () => window.removeEventListener('click', close)
  }, [menu])
  const hide = (f: Folder, hidden: boolean) => {
    setAll(prev => prev.map(x => (x.path === f.path ? { ...x, hidden } : x)))
    api.folderHide(f.path, hidden).then(() => { s.toast(hidden ? `“${f.path.split('/').pop()}” is hidden. Turn on “Show hidden” to see it.` : 'Shown again'); s.reload(); load() }).catch(e => s.toast(e.message))
  }
  const exclude = (path: string, ex: boolean) => {
    const name = path.split('/').pop()
    if (ex && !window.confirm(`Leave “${name}” out of LightPhotoLibrary?\n\nIts photos disappear from LPL (ratings and tags stay in the XMP files) and it is no longer scanned. Nothing is deleted from the NAS. You can take it back in later.`)) return
    api.folderExclude(path, ex).then(r => { s.toast(ex ? `Left out “${name}” (${r.removed} photos)` : `Taking “${name}” back in; it's being scanned`); setExcluded(r.excluded); if (!r.excluded.length) setShowExcluded(false); s.reload(); load() }).catch(e => s.toast(e.message))
  }
  useEffect(() => { localStorage.setItem(fkey, cur) }, [cur])
  useEffect(() => { localStorage.setItem('lpl.folderSort', mode) }, [mode])

  const here = all.find(f => f.path === cur)
  const children = useMemo(() => {
    const kids = all.filter(f => f.path !== '' && parentOf(f.path) === cur && (showHidden || !f.hidden))
    const name = (f: Folder) => f.path.split('/').pop() || ''
    return kids.sort((a, b) => {
      switch (mode) {
        case 'new': return (b.last || '').localeCompare(a.last || '')
        case 'old': return (a.first || '').localeCompare(b.first || '')
        case 'name': return name(a).localeCompare(name(b), undefined, { numeric: true })
        case 'count': return b.count - a.count
        case 'edited': return b.edited - a.edited
      }
      const pa = a.pos ?? 1e9, pb = b.pos ?? 1e9
      return pa - pb || name(a).localeCompare(name(b), undefined, { numeric: true })
    })
  }, [all, cur, mode, showHidden])
  const hiddenHere = all.filter(f => f.hidden && parentOf(f.path) === cur).length

  const crumbs = cur ? cur.split('/') : []
  const open = (path: string) => { s.patchFilters({ folder: path || null, folders: [], month: null }); s.setView('grid') }
  const openPicked = () => { s.patchFilters({ folder: null, folders: picked, month: null }); setPicked([]); setPicking(false); s.setView('grid') }
  // Desktop: a click selects (Ctrl adds, Shift picks a range), a double-click opens.
  // Phone: a tap opens, unless “Select” is on.
  const openTile = (f: Folder, hasKids: boolean) => { setPicked([]); hasKids ? setCur(f.path) : open(f.path) }
  const clickTile = (e: React.MouseEvent, f: Folder, hasKids: boolean) => {
    if (!s.isMobile && !picking && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
      lastPick.current = f.path
      setPicked([f.path])
      return
    }
    if (picking || e.ctrlKey || e.metaKey || e.shiftKey) {
      if (e.shiftKey && lastPick.current) {
        const ps = children.map(c => c.path)
        const a = ps.indexOf(lastPick.current), b = ps.indexOf(f.path)
        if (a >= 0 && b >= 0) {
          const range = ps.slice(Math.min(a, b), Math.max(a, b) + 1)
          setPicked(prev => Array.from(new Set([...prev, ...range])))
          return
        }
      }
      lastPick.current = f.path
      setPicked(prev => (prev.includes(f.path) ? prev.filter(x => x !== f.path) : [...prev, f.path]))
      return
    }
    openTile(f, hasKids)
  }
  const pickedCount = all.filter(f => picked.includes(f.path)).reduce((n, f) => n + f.count, 0)
  const drop = (target: string) => {
    if (!drag || drag === target) return
    const order = children.map(c => c.path).filter(p => p !== drag)
    order.splice(order.indexOf(target), 0, drag)
    setMode('custom')
    setAll(prev => prev.map(f => (order.includes(f.path) ? { ...f, pos: order.indexOf(f.path) } : f)))
    api.folderOrder(order).catch(e => s.toast(e.message))
  }
  const range = (f: Folder) => {
    if (!f.first) return ''
    const a = fmtMonth(f.first), b = f.last ? fmtMonth(f.last) : a
    return a === b ? a : `${a} – ${b}`
  }

  return (
    <div className="scroll">
      <div className="row wrap" style={{ marginBottom: 16, padding: s.isMobile ? '0 14px' : 0 }}>
        <button className="btn ghost" onClick={() => setCur('')}>{s.isLocal ? 'This PC' : 'NAS'}</button>
        {crumbs.map((c, i) => (
          <span key={i} className="row" style={{ gap: 4 }}>
            <span className="muted">›</span>
            <button className="btn ghost" onClick={() => setCur(crumbs.slice(0, i + 1).join('/'))}>{c}</button>
          </span>
        ))}
        <div className="grow" />
        {here && here.direct > 0 && <button className="btn" onClick={() => open(cur)}>Show {here.direct} photos here</button>}
        {cur && <button className="btn" onClick={() => open(cur)}>Show all {here?.count ?? ''} incl. subfolders</button>}
        {(hiddenHere > 0 || showHidden) && (
          <label className="row small soft" style={{ cursor: 'pointer' }}>
            <input type="checkbox" checked={showHidden} onChange={() => s.patchFilters({ show_hidden: !showHidden })} style={{ accentColor: '#E8A33D' }} />
            Show hidden{hiddenHere ? ` (${hiddenHere})` : ''}
          </label>
        )}
        <button className={'btn' + (picking ? ' primary' : '')} onClick={() => { setPicking(!picking); if (picking) setPicked([]) }}>{picking ? 'Done' : 'Select'}</button>
        {excluded.length > 0 && <button className="btn ghost small" onClick={() => setShowExcluded(true)}>Left out ({excluded.length})</button>}
        <label className="row small soft">Arrange
          <select className="input" value={mode} onChange={e => setMode(e.target.value)}>
            <option value="custom">my own order (drag)</option>
            <option value="new">newest first</option>
            <option value="old">oldest first</option>
            <option value="name">name</option>
            <option value="count">number of photos</option>
            <option value="edited">most edited</option>
          </select>
        </label>
      </div>
      {picked.length > 0 && (
        <div className="row wrap pickbar">
          <b>{picked.length === 1 ? picked[0].split('/').pop() : `${picked.length} folders`}</b><span className="small soft">{pickedCount.toLocaleString()} photos</span>
          <div className="grow" />
          <button className="btn" onClick={() => setPicked([])}>Clear</button>
          {picked.length === 1 && all.some(x => parentOf(x.path) === picked[0] && x.path !== picked[0]) && <button className="btn" onClick={() => { const p = picked[0]; setPicked([]); setCur(p) }}>Open folder</button>}
          <button className="btn primary" onClick={openPicked}>Show {picked.length === 1 ? 'its' : 'their'} photos</button>
        </div>
      )}
      <div className="ftiles" onClick={e => { if (e.target === e.currentTarget) setPicked([]) }}>
        {children.map(f => {
          const hasKids = all.some(x => parentOf(x.path) === f.path && x.path !== f.path)
          return (
            <div key={f.path} className={'ftile' + (over === f.path ? ' over' : '') + (f.hidden ? ' hidden' : '') + (picked.includes(f.path) ? ' picked' : '')} draggable
              onDragStart={e => { setDrag(f.path); e.dataTransfer.setData('text/plain', f.path) }}
              onDragOver={e => { e.preventDefault(); setOver(f.path) }} onDragLeave={() => setOver(null)}
              onDrop={e => { e.preventDefault(); setOver(null); drop(f.path); setDrag(null) }}
              onClick={e => { e.stopPropagation(); clickTile(e, f, hasKids) }} onDoubleClick={() => { if (!picking) openTile(f, hasKids) }}>
              <div className="mosaic">
                {[0, 1, 2, 3].map(i => <span key={i}>{f.covers[i] && !f.covers[i].pending && <RetryImg src={coverUrl(f.covers[i])} />}</span>)}
              </div>
              <div className="col" style={{ gap: 2, position: 'relative' }}>
                <div className="row" style={{ gap: 4 }}>
                  <b className="ellipsis grow">{f.path.split('/').pop()}{f.hidden ? <span className="small muted"> · hidden</span> : null}</b>
                  <button className="btn ghost tilemenu" aria-label={`Options for ${f.path.split('/').pop()}`} onClick={e => { e.stopPropagation(); setMenu(menu === f.path ? null : f.path) }}>⋯</button>
                </div>
                {menu === f.path && (
                  <div className="tmenu" onClick={e => e.stopPropagation()}>
                    <button onClick={() => { setMenu(null); open(f.path) }}>Show all photos</button>
                    {f.hidden ? <button onClick={() => { setMenu(null); hide(f, false) }}>Show again in all views</button>
                      : <button onClick={() => { setMenu(null); hide(f, true) }}>Hide from views</button>}
                    <button onClick={() => { setMenu(null); exclude(f.path, true) }}>Leave out of the library…</button>
                  </div>
                )}
                <span className="small muted">{f.count} photos · {f.edited} edited{hasKids ? ' · has subfolders' : ''}</span>
                <span className="tiny muted">{range(f)}</span>
              </div>
            </div>
          )
        })}
      </div>
      {!children.length && <div className="muted" style={{ padding: 30 }}>No subfolders here.</div>}
      {!s.isMobile && <div className="small muted" style={{ marginTop: 14 }}>Click a folder to select it, double-click to open it. Ctrl-click adds folders, Shift-click picks a range; then “Show their photos” shows them together. Drag tiles to put them in your own order. ⋯ on a tile hides it or leaves it out of the library.{pendingPreviews > 0 ? ` Making previews: ${pendingPreviews.toLocaleString()} to go (what you look at goes first).` : ''}</div>}
      {showExcluded && (
        <>
          <div className="backdrop" onClick={() => setShowExcluded(false)} />
          <div className="dialog" role="dialog" aria-label="Folders left out">
            <b>Folders left out of the library</b>
            <span className="small soft">They aren't scanned or shown. The files are still on the NAS.</span>
            {excluded.map(p => (
              <div key={p} className="row"><span className="grow ellipsis">{p}</span><button className="btn" onClick={() => exclude(p, false)}>Take back in</button></div>
            ))}
            {!excluded.length && <span className="small muted">None.</span>}
            <div className="row"><div className="grow" /><button className="btn" onClick={() => setShowExcluded(false)}>Close</button></div>
          </div>
        </>
      )}
    </div>
  )
}

export function TimelineView() {
  const s = useStore()
  const [months, setMonths] = useState<{ month: string; count: number; folders: string[]; covers: { id: number; v: number }[] }[]>([])
  useEffect(() => { api.timeline(s.filters).then(r => setMonths(r.months)).catch(e => s.toast(e.message)) }, [s.filters])
  const open = (m: string) => { s.patchFilters({ month: m }); s.setView('grid') }
  return (
    <div className="scroll">
      {months.map(m => (
        <div className="month" key={m.month}>
          <div className="col" style={{ flex: '0 0 170px', gap: 2 }}>
            <span className="display" style={{ fontSize: 17, fontWeight: 500 }}>{fmtMonth(m.month + '-01')}</span>
            <span className="small muted ellipsis">{m.count} photos · {m.folders.join(', ')}</span>
          </div>
          <div className="strip">{m.covers.map(c => <RetryImg key={c.id} src={coverUrl(c)} />)}</div>
          <button className="btn" onClick={() => open(m.month)}>Show</button>
        </div>
      ))}
      {!months.length && <div className="muted" style={{ padding: 30 }}>Nothing yet. Photos without a capture date don't appear here.</div>}
    </div>
  )
}
