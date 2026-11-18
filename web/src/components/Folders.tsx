import { useEffect, useMemo, useState } from 'react'
import { api, coverUrl, Folder } from '../api'
import { useStore } from '../store'
import { fmtMonth } from '../fields'

const parentOf = (p: string) => (p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : p ? '' : null)

export function FoldersView() {
  const s = useStore()
  const [all, setAll] = useState<Folder[]>([])
  const [cur, setCur] = useState<string>(() => localStorage.getItem('lpl.folder') || '')
  const [mode, setMode] = useState<string>(() => localStorage.getItem('lpl.folderSort') || 'custom')
  const [drag, setDrag] = useState<string | null>(null)
  const [over, setOver] = useState<string | null>(null)
  const load = () => api.folders().then(r => setAll(r.folders)).catch(e => s.toast(e.message))
  useEffect(() => { load() }, [])
  useEffect(() => { localStorage.setItem('lpl.folder', cur) }, [cur])
  useEffect(() => { localStorage.setItem('lpl.folderSort', mode) }, [mode])

  const here = all.find(f => f.path === cur)
  const children = useMemo(() => {
    const kids = all.filter(f => f.path !== '' && parentOf(f.path) === cur)
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
  }, [all, cur, mode])

  const crumbs = cur ? cur.split('/') : []
  const open = (path: string) => { s.patchFilters({ folder: path || null, month: null }); s.setView('grid') }
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
        <button className="btn ghost" onClick={() => setCur('')}>NAS</button>
        {crumbs.map((c, i) => (
          <span key={i} className="row" style={{ gap: 4 }}>
            <span className="muted">›</span>
            <button className="btn ghost" onClick={() => setCur(crumbs.slice(0, i + 1).join('/'))}>{c}</button>
          </span>
        ))}
        <div className="grow" />
        {here && here.direct > 0 && <button className="btn" onClick={() => open(cur)}>Show {here.direct} photos here</button>}
        {cur && <button className="btn" onClick={() => open(cur)}>Show all {here?.count ?? ''} incl. subfolders</button>}
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
      <div className="ftiles">
        {children.map(f => {
          const hasKids = all.some(x => parentOf(x.path) === f.path && x.path !== f.path)
          return (
            <div key={f.path} className={'ftile' + (over === f.path ? ' over' : '')} draggable
              onDragStart={e => { setDrag(f.path); e.dataTransfer.setData('text/plain', f.path) }}
              onDragOver={e => { e.preventDefault(); setOver(f.path) }} onDragLeave={() => setOver(null)}
              onDrop={e => { e.preventDefault(); setOver(null); drop(f.path); setDrag(null) }}
              onClick={() => (hasKids ? setCur(f.path) : open(f.path))}>
              <div className="mosaic">
                {[0, 1, 2, 3].map(i => <span key={i} style={{ backgroundImage: f.covers[i] ? `url(${coverUrl(f.covers[i])})` : undefined }} />)}
              </div>
              <div className="col" style={{ gap: 2 }}>
                <b className="ellipsis">{f.path.split('/').pop()}</b>
                <span className="small muted">{f.count} photos · {f.edited} edited{hasKids ? ' · has subfolders' : ''}</span>
                <span className="tiny muted">{range(f)}</span>
              </div>
            </div>
          )
        })}
      </div>
      {!children.length && <div className="muted" style={{ padding: 30 }}>No subfolders here.</div>}
      {!s.isMobile && <div className="small muted" style={{ marginTop: 14 }}>Click a folder to open it. Drag tiles to put them in your own order; it's saved for every device.</div>}
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
          <div className="strip">{m.covers.map(c => <img key={c.id} src={coverUrl(c)} alt="" loading="lazy" />)}</div>
          <button className="btn" onClick={() => open(m.month)}>Show</button>
        </div>
      ))}
      {!months.length && <div className="muted" style={{ padding: 30 }}>Nothing yet. Photos without a capture date don't appear here.</div>}
    </div>
  )
}
