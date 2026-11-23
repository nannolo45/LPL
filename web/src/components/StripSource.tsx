// SPDX-License-Identifier: GPL-3.0-or-later
// The Strip's photo panel (left of the lanes, instead of the series list): browse the whole library with a few
// filters, select, and drag photos into a lane (at a place) or onto "+ New series". Photos already in the open
// series or folder are dimmed, with the lane they are in.
import { useEffect, useRef, useState } from 'react'
import { api, Filters, Photo, Target, thumbUrl } from '../api'
import { useStore } from '../store'
import { ExtDrag } from './Strip'

type Scope = 'here' | 'any' | 'all'
const PAGE = 200

export function StripSource({ target, name, members, ext, rev }: {
  target: Target; name: string; members: Map<number, string>; ext: React.MutableRefObject<ExtDrag | null>
  /** goes up when photos were dropped into the strip: load again */
  rev: number
}) {
  const s = useStore()
  const [q, setQ] = useState('')
  const [scope, setScope] = useState<Scope>('here')
  const [folder, setFolder] = useState<string>(() => { try { return localStorage.getItem('lpl.stripsrc.folder') || '' } catch { return '' } })
  const [stars, setStars] = useState(0)
  const [bnw, setBnw] = useState(false)
  const [paths, setPaths] = useState<string[]>([])
  const [items, setItems] = useState<Photo[]>([])
  const [total, setTotal] = useState(0)
  const [sel, setSel] = useState<number[]>([])
  const anchor = useRef<number | null>(null)
  const req = useRef(0)
  useEffect(() => { api.folders().then(r => setPaths(r.folders.map(f => f.path).filter(Boolean).sort())).catch(() => {}) }, [])
  useEffect(() => { try { localStorage.setItem('lpl.stripsrc.folder', folder) } catch { /* ignore */ } }, [folder])

  const filters = (): Filters & Record<string, unknown> => ({
    q: q.trim() || undefined, folder: folder || null, subfolders: true, rating: stars ? 'r' + stars : undefined,
    look: bnw ? ['bnw'] : undefined,
    ...(scope === 'any' ? { no_series: true } : scope === 'here' ? (target.kind === 'folder' ? { not_in_series_folder: target.id } : { not_in_series: target.id }) : {}),
  })
  const load = (more = false) => {
    const id = ++req.current
    api.query([{ f: 'taken', d: 'asc' }, { f: 'name', d: 'asc' }], filters(), more ? items.length : 0, PAGE, 0).then(r => {
      if (id !== req.current) return
      setItems(more ? [...items, ...r.items] : r.items); setTotal(r.total)
    }).catch(e => s.toast(e.message))
  }
  useEffect(() => { const t = window.setTimeout(() => load(), q ? 250 : 0); return () => window.clearTimeout(t) }, [q, scope, folder, stars, bnw, target.kind, target.id, rev])
  useEffect(() => { setSel([]) }, [rev, target.kind, target.id])

  const pick = (id: number, e: { ctrlKey: boolean; metaKey: boolean; shiftKey: boolean }) => {
    if (e.shiftKey && anchor.current != null) {
      const ids = items.map(p => p.id), a = ids.indexOf(anchor.current), b = ids.indexOf(id)
      if (a >= 0 && b >= 0) { setSel(Array.from(new Set([...sel, ...ids.slice(Math.min(a, b), Math.max(a, b) + 1)]))); return }
    }
    anchor.current = id
    if (e.ctrlKey || e.metaKey) setSel(sel.includes(id) ? sel.filter(x => x !== id) : [...sel, id])
    else setSel(sel.length === 1 && sel[0] === id ? [] : [id])
  }

  // press on a photo and move: the selection (or this photo) is dragged into the strip; a touch screen holds first
  const lastClick = useRef<{ id: number; t: number } | null>(null)
  const onDown = (e: React.PointerEvent, p: Photo) => {
    if (e.button !== 0) return
    const touch = e.pointerType === 'touch'
    if (!touch) e.preventDefault()
    const x0 = e.clientX, y0 = e.clientY, mods = { ctrlKey: e.ctrlKey, metaKey: e.metaKey, shiftKey: e.shiftKey }
    const el = (e.currentTarget as HTMLElement).querySelector('img') as HTMLElement | null
    let started = false, held = !touch, timer = 0
    const begin = (x: number, y: number) => {
      if (!ext.current) { s.toast('Open the Strip to drag photos into it'); return false }
      const ids = sel.includes(p.id) ? items.map(i => i.id).filter(i => sel.includes(i)) : [p.id]
      if (!sel.includes(p.id)) setSel([p.id])
      ext.current.begin(ids, p.filename, el, x, y, touch)
      return true
    }
    if (touch) timer = window.setTimeout(() => { held = true; started = begin(x0, y0); try { navigator.vibrate?.(12) } catch { /* ignore */ } }, 350)
    const move = (ev: PointerEvent) => {
      if (!started) {
        if (Math.hypot(ev.clientX - x0, ev.clientY - y0) < (touch ? 8 : 5)) return
        if (!held) { window.clearTimeout(timer); cleanup(); return }  // a touch that moves first is a scroll
        started = begin(ev.clientX, ev.clientY)
        if (!started) { cleanup(); return }
      }
      if (ev.cancelable) ev.preventDefault()
      ext.current?.move(ev.clientX, ev.clientY)
    }
    const up = (ev: PointerEvent) => {
      cleanup()
      window.clearTimeout(timer)
      if (started) { ext.current?.end(ev.type === 'pointerup'); return }
      if (ev.type !== 'pointerup' || held && touch) return
      const now = Date.now(), lc = lastClick.current
      if (lc && lc.id === p.id && now - lc.t < 400) { lastClick.current = null; s.setViewer(p.id); return }
      lastClick.current = { id: p.id, t: now }
      pick(p.id, mods)
    }
    const cleanup = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); window.removeEventListener('pointercancel', up) }
    window.addEventListener('pointermove', move, { passive: false }); window.addEventListener('pointerup', up); window.addEventListener('pointercancel', up)
  }

  const here = target.kind === 'folder' ? `Not in ${name}` : `Not in ${name}`
  return (
    <div className="ssrc">
      <label className="ssrc-search">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></svg>
        <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search filename, folder, tag…" aria-label="Search photos" />
      </label>
      <div className="row wrap" style={{ gap: 5 }} role="group" aria-label="Which photos">
        {([['here', here], ['any', 'In no series yet'], ['all', 'All']] as [Scope, string][]).map(([v, l]) =>
          <button key={v} className={'chip' + (scope === v ? ' on' : '')} aria-pressed={scope === v} onClick={() => setScope(v)}>{l}</button>)}
      </div>
      <div className="row" style={{ gap: 6 }}>
        <select className="input grow" value={folder} onChange={e => setFolder(e.target.value)} aria-label="Library folder">
          <option value="">All folders</option>
          {paths.map(p => <option key={p} value={p}>{p}</option>)}
        </select>
        <select className="input" value={stars} onChange={e => setStars(+e.target.value)} aria-label="Rating at least">
          <option value={0}>any ★</option>{[1, 2, 3, 4, 5].map(n => <option key={n} value={n}>{'★'.repeat(n)}+</option>)}
        </select>
        <button className={'chip' + (bnw ? ' on' : '')} aria-pressed={bnw} title="Black & white (auto-tagging)" onClick={() => setBnw(!bnw)}>B&amp;W</button>
      </div>
      <div className="row tiny muted" style={{ gap: 6 }}>
        <span className="grow">{total.toLocaleString()} photo{total === 1 ? '' : 's'}{sel.length ? ` · ${sel.length} selected` : ''}</span>
        {sel.length > 0 && <button className="linkbtn" onClick={() => setSel([])}>none</button>}
        {items.length > 0 && <button className="linkbtn" onClick={() => setSel(items.map(p => p.id))}>select all</button>}
      </div>
      <div className="ssrc-grid">
        {items.map(p => {
          const inLane = members.get(p.id)
          return (
            <button key={p.id} type="button" className={'ssrc-th' + (sel.includes(p.id) ? ' sel' : '') + (inLane ? ' used' : '')} aria-pressed={sel.includes(p.id)}
              aria-label={`${p.filename}${inLane ? ', in ' + inLane : ''}`} title={inLane ? `${p.filename} · in ${inLane}` : p.filename} onPointerDown={e => onDown(e, p)}>
              <img src={thumbUrl(p)} alt="" draggable={false} loading="lazy" />
              {inLane && <em>{inLane}</em>}
            </button>
          )
        })}
      </div>
      {items.length < total && <button className="btn" onClick={() => load(true)}>Show more ({(total - items.length).toLocaleString()})</button>}
      {!items.length && <span className="small muted">{scope === 'here' ? `Every photo here is already in ${name}.` : 'No photos match.'}</span>}
      <span className="tiny muted">Drag photos onto a lane, between two photos, or onto “+ New series”. Ctrl or Shift selects several; double-click opens one large.</span>
    </div>
  )
}
