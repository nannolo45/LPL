import { useEffect, useState } from 'react'
import { api, Photo } from '../api'
import { useStore } from '../store'
import { actionFor, keysOf, typingIn } from '../shortcuts'
import { exifLine, fmtDay, leaf } from '../fields'
import { RatingLabels } from './Inspector'
import { Thumb } from './Library'

export function Viewer() {
  const s = useStore()
  const id = s.viewer
  const idx = s.items.findIndex(p => p.id === id)
  const [extra, setExtra] = useState<Photo | null>(null)
  const p: Photo | null = idx >= 0 ? s.items[idx] : extra
  useEffect(() => { if (id != null && idx < 0) api.photo(id).then(setExtra).catch(() => {}) }, [id, idx])
  const go = (d: number) => {
    const n = s.items[idx + d]
    if (n) { s.setViewer(n.id); s.setSel([n.id]) }
    if (idx + d >= s.items.length - 5) s.loadMore()
  }
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (id == null || s.fullscreen || typingIn(e.target)) return  // closed: leave the keys alone
      const a = actionFor(e, ['viewer'])
      if (a === 'close') s.setViewer(null)
      else if (a === 'next') { e.preventDefault(); go(1) }
      else if (a === 'prev') { e.preventDefault(); go(-1) }
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  })
  if (id == null || !p) return null
  return (
    <div className="viewer" role="dialog" aria-label={p.filename}>
      <div className="vtop row wrap">
        <b>{p.filename}</b>
        <span className="small muted">{p.taken ? fmtDay(p.taken) + ' ' + p.taken.slice(11, 16) : ''} · {p.camera} · {p.lens} · {exifLine(p)}</span>
        <div className="grow" />
        <RatingLabels photos={[p]} />
        <button className="btn primary" onClick={() => s.editInDarktable([p.id])}>Edit in darktable</button>
        <button className="btn" title={'Full screen (' + (keysOf('fullscreen')[0] || '') + ')'} onClick={() => { const k = s.items.findIndex(x => x.id === p.id); s.setFullscreen(k >= 0 ? { list: s.items, index: k } : { list: [p], index: 0 }) }}>Full screen</button>
        <button className="btn" onClick={() => s.setViewer(null)}>Close (Esc)</button>
      </div>
      <div className="vimg">
        <Thumb p={p} size="l" />
        {idx > 0 && <button className="nav" style={{ left: 12 }} onClick={() => go(-1)} aria-label="Previous">‹</button>}
        {idx >= 0 && idx < s.items.length - 1 && <button className="nav" style={{ right: 12 }} onClick={() => go(1)} aria-label="Next">›</button>}
      </div>
      <div className="row wrap small soft" style={{ padding: '8px 16px 14px', gap: 6 }}>
        {p.tags.map(t => <span key={t} className="tagchip" title={t} style={{ paddingRight: 8 }}>{leaf(t)}</span>)}
        <span className="muted">{p.folder}</span>
      </div>
    </div>
  )
}
