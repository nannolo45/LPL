import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, Frame, Photo, thumbUrl } from '../api'
import { useStore } from '../store'

type Item = Photo & { x: number; y: number }

function size(p: Photo) {
  let w = p.width || 3, h = p.height || 2
  if ([5, 6, 7, 8].includes(p.orientation)) [w, h] = [h, w]
  const r = w / h
  return r >= 1 ? { w: 150, h: Math.round(150 / r) } : { w: Math.round(105 * r), h: 105 }
}

export function readingOrder(items: Item[], frames: Frame[]) {
  const fr = frames.map((f, i) => ({ f, i })).sort((a, b) => Math.round(a.f.y / 100) - Math.round(b.f.y / 100) || a.f.x - b.f.x)
  const frameOf = (it: Item) => {
    const { w, h } = size(it)
    const cx = it.x + w / 2, cy = it.y + h / 2
    for (let k = 0; k < fr.length; k++) {
      const f = fr[k].f
      if (cx >= f.x && cx <= f.x + f.w && cy >= f.y && cy <= f.y + f.h) return k
    }
    return 999
  }
  return items
    .map(it => ({ it, k: frameOf(it) }))
    .filter(o => o.k === 999 || !fr[o.k].f.maybe)
    .sort((a, b) => a.k - b.k || Math.round(a.it.y / 110) - Math.round(b.it.y / 110) || a.it.x - b.it.x)
    .map(o => o.it)
}

export function BoardView() {
  const s = useStore()
  const sid = s.boardSeries ?? s.series[0]?.id ?? null
  const [frames, setFrames] = useState<Frame[]>([])
  const [items, setItems] = useState<Item[]>([])
  const [name, setName] = useState('')
  const [zoom, setZoom] = useState(1)
  const drag = useRef<null | { kind: 'items' | 'frame' | 'resize'; ids: number[]; fi: number; sx: number; sy: number; orig: Map<number, { x: number; y: number }>; of: Frame; inside: number[] }>(null)
  const saveTimer = useRef<number>()
  const framesDirty = useRef(false)

  const load = useCallback(() => {
    if (sid == null) return
    api.board(sid).then(r => { setFrames(r.frames); setItems(r.items.map(p => ({ ...p, x: p.x || 0, y: p.y || 0 }))); setName(r.series.name) })
      .catch(e => s.toast(e.message))
  }, [sid])
  useEffect(() => { load() }, [load])

  const order = useMemo(() => readingOrder(items, frames), [items, frames])
  const pos = useMemo(() => new Map(order.map((it, i) => [it.id, i + 1])), [order])

  const save = useCallback((its: Item[], frs: Frame[], withFrames: boolean) => {
    if (sid == null) return
    window.clearTimeout(saveTimer.current)
    saveTimer.current = window.setTimeout(() => {
      const ord = new Map(readingOrder(its, frs).map((it, i) => [it.id, i + 1]))
      api.boardSave(sid, its.map(it => ({ id: it.id, x: Math.round(it.x), y: Math.round(it.y), ord: ord.get(it.id) ?? null })), withFrames || framesDirty.current ? frs : undefined)
        .then(r => { if (withFrames || framesDirty.current) { setFrames(r.frames); framesDirty.current = false } s.reloadSeries() })
        .catch(e => s.toast(e.message))
    }, 500)
  }, [sid])

  const selSet = new Set(s.sel)
  const onItemDown = (e: React.PointerEvent, it: Item) => {
    e.preventDefault(); e.stopPropagation()
    let ids = s.sel.includes(it.id) ? s.sel : [it.id]
    if (e.ctrlKey || e.metaKey || e.shiftKey) { ids = s.sel.includes(it.id) ? s.sel.filter(x => x !== it.id) : [...s.sel, it.id]; s.setSel(ids) }
    else if (!s.sel.includes(it.id)) s.setSel([it.id])
    const orig = new Map(items.filter(x => ids.includes(x.id)).map(x => [x.id, { x: x.x, y: x.y }]))
    drag.current = { kind: 'items', ids, fi: -1, sx: e.clientX, sy: e.clientY, orig, of: frames[0], inside: [] }
    ;(e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId)
  }
  const onFrameDown = (e: React.PointerEvent, fi: number, kind: 'frame' | 'resize') => {
    e.preventDefault(); e.stopPropagation()
    const f = frames[fi]
    const inside = kind === 'frame' ? items.filter(it => { const { w, h } = size(it); const cx = it.x + w / 2, cy = it.y + h / 2; return cx >= f.x && cx <= f.x + f.w && cy >= f.y && cy <= f.y + f.h }).map(i => i.id) : []
    const orig = new Map(items.filter(x => inside.includes(x.id)).map(x => [x.id, { x: x.x, y: x.y }]))
    drag.current = { kind, ids: [], fi, sx: e.clientX, sy: e.clientY, orig, of: { ...f }, inside }
    ;(e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId)
  }
  const onMove = (e: React.PointerEvent) => {
    const d = drag.current
    if (!d) return
    const dx = (e.clientX - d.sx) / zoom, dy = (e.clientY - d.sy) / zoom
    if (d.kind === 'items' || d.kind === 'frame') {
      setItems(prev => prev.map(it => { const o = d.orig.get(it.id); return o ? { ...it, x: Math.max(0, o.x + dx), y: Math.max(0, o.y + dy) } : it }))
    }
    if (d.kind === 'frame') setFrames(prev => prev.map((f, i) => (i === d.fi ? { ...f, x: Math.max(0, d.of.x + dx), y: Math.max(0, d.of.y + dy) } : f)))
    if (d.kind === 'resize') setFrames(prev => prev.map((f, i) => (i === d.fi ? { ...f, w: Math.max(160, d.of.w + dx), h: Math.max(120, d.of.h + dy) } : f)))
  }
  const onUp = () => {
    const d = drag.current
    if (!d) return
    drag.current = null
    if (d.kind !== 'items') framesDirty.current = true
    setItems(its => { setFrames(frs => { save(its, frs, d.kind !== 'items'); return frs }); return its })
  }

  const addFrame = (maybe: boolean) => {
    const maxY = Math.max(0, ...frames.map(f => f.y + f.h), ...items.map(i => i.y + 120))
    const label = maybe ? 'Maybe pile' : String(frames.filter(f => !f.maybe).length + 1).padStart(2, '0')
    const nf = [...frames, { label, x: 40, y: maxY + 40, w: maybe ? 380 : 900, h: 300, maybe: maybe ? 1 : 0 }]
    setFrames(nf); save(items, nf, true)
  }
  const renameFrame = (fi: number) => {
    const v = window.prompt('Chapter name', frames[fi].label)
    if (v == null) return
    const nf = frames.map((f, i) => (i === fi ? { ...f, label: v } : f)); setFrames(nf); save(items, nf, true)
  }
  const deleteFrame = (fi: number) => {
    if (!window.confirm(`Remove the frame “${frames[fi].label}”? The photos stay on the board.`)) return
    const nf = frames.filter((_, i) => i !== fi); setFrames(nf); save(items, nf, true)
  }
  const removeSel = async () => {
    const ids = s.sel.filter(id => items.some(i => i.id === id))
    if (!ids.length || sid == null) return
    await api.seriesRemove(sid, ids)
    setItems(prev => prev.filter(i => !ids.includes(i.id))); s.setSel([]); s.reloadSeries(); s.reloadTags()
  }
  const newSeries = async () => {
    const n = window.prompt('Name of the new series')
    if (!n) return
    try {
      const r = await api.seriesCreate(n, s.sel)
      s.reloadSeries(); s.setBoardSeries(r.id)
    } catch (e: any) { s.toast(e.message) }
  }
  const rename = async () => {
    if (sid == null) return
    const n = window.prompt('Rename series (its tag is renamed too)', name)
    if (!n || n === name) return
    await api.seriesRename(sid, n); setName(n); s.reloadSeries(); s.reloadTags()
  }
  const del = async () => {
    if (sid == null || !window.confirm(`Delete the series “${name}”? Photos are not deleted; their series tag is removed.`)) return
    await api.seriesDelete(sid); s.setBoardSeries(null); s.reloadSeries(); s.reloadTags()
  }
  const showInLibrary = () => { if (sid == null) return; s.patchFilters({ series: sid, folder: null, month: null, tag: null }); s.setSort([{ f: 'custom', d: 'asc' }]); s.setView('grid') }

  const W = Math.max(1400, ...frames.map(f => f.x + f.w + 200), ...items.map(i => i.x + 400))
  const H = Math.max(900, ...frames.map(f => f.y + f.h + 200), ...items.map(i => i.y + 300))

  return (
    <div className="board-wrap">
      {!s.isMobile && (
        <div className="series-list">
          <span className="caps" style={{ padding: '0 8px 6px' }}>Series</span>
          {s.series.map(x => (
            <button key={x.id} className={'it' + (x.id === sid ? ' on' : '')} onClick={() => s.setBoardSeries(x.id)}>
              <span className="ellipsis">{x.name}</span><span className="muted">{x.count}</span>
            </button>
          ))}
          <button className="btn dashed" style={{ margin: '6px 4px' }} onClick={newSeries}>+ New series{s.sel.length ? ` from ${s.sel.length} selected` : ''}</button>
          <div className="small muted" style={{ padding: 8, lineHeight: 1.5 }}>
            Add photos from the Library: select them, then “Add to series…”. Frames are chapters; photos in a Maybe pile are not part of the series.
          </div>
        </div>
      )}
      <div className="center">
        {sid == null ? (
          <div className="col" style={{ padding: 40, alignItems: 'flex-start' }}>
            <span className="display" style={{ fontSize: 20 }}>No series yet</span>
            <span className="muted">Select photos in the Library, then use “Add to series… → new series”.</span>
            <button className="btn primary" onClick={newSeries}>New series</button>
          </div>
        ) : (
          <>
            <div className="ctxbar">
              <span className="display" style={{ fontSize: 17 }}>{name}</span>
              <span className="muted">{order.length} in series · {items.length - order.length} in maybe piles</span>
              <div className="grow" />
              <button className="btn" onClick={() => addFrame(false)}>+ Frame</button>
              <button className="btn" onClick={() => addFrame(true)}>+ Maybe pile</button>
              <button className="btn" onClick={() => setZoom(z => Math.max(.3, +(z - .1).toFixed(2)))} aria-label="Zoom out">−</button>
              <span className="small muted">{Math.round(zoom * 100)}%</span>
              <button className="btn" onClick={() => setZoom(z => Math.min(2, +(z + .1).toFixed(2)))} aria-label="Zoom in">+</button>
              <button className="btn" onClick={showInLibrary}>Show in library</button>
              <button className="btn" onClick={rename}>Rename</button>
              <button className="btn" disabled={!s.sel.some(id => items.some(i => i.id === id))} onClick={removeSel}>Remove selected</button>
              <button className="btn danger" onClick={del}>Delete</button>
            </div>
            <div className="canvas-wrap" onPointerDown={e => { if (e.target === e.currentTarget) s.setSel([]) }}
              onWheel={e => { if (e.ctrlKey) { e.preventDefault(); setZoom(z => Math.min(2, Math.max(.3, +(z - Math.sign(e.deltaY) * .1).toFixed(2)))) } }}>
              <div className="canvas" style={{ width: W * zoom, height: H * zoom }} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}
                onPointerDown={e => { if (e.target === e.currentTarget) s.setSel([]) }}>
                <div style={{ transform: `scale(${zoom})`, transformOrigin: '0 0', width: W, height: H, position: 'absolute', left: 0, top: 0 }}
                  onPointerDown={e => { if (e.target === e.currentTarget) s.setSel([]) }}>
                  {frames.map((f, i) => (
                    <div key={f.id ?? 'n' + i} className={'frame' + (f.maybe ? ' maybe' : '')} style={{ left: f.x, top: f.y, width: f.w, height: f.h }}>
                      <span className="fl" onPointerDown={e => onFrameDown(e, i, 'frame')} onDoubleClick={() => renameFrame(i)} title="Drag to move (with its photos), double-click to rename">
                        {f.label}{f.maybe ? ' · not in series' : ''}
                      </span>
                      <button className="fx" aria-label="Remove frame" onClick={() => deleteFrame(i)}>×</button>
                      <span className="rs" onPointerDown={e => onFrameDown(e, i, 'resize')} />
                    </div>
                  ))}
                  {items.map(it => {
                    const { w, h } = size(it)
                    const n = pos.get(it.id)
                    return (
                      <button key={it.id} className={'bitem' + (selSet.has(it.id) ? ' sel' : '')} style={{ left: it.x, top: it.y, width: w, height: h, zIndex: selSet.has(it.id) ? 5 : 1 }}
                        onPointerDown={e => onItemDown(e, it)} onDoubleClick={() => s.editInDarktable([it.id])} aria-label={it.filename + (n ? `, position ${n}` : ', maybe')}>
                        <img src={thumbUrl(it)} alt="" draggable={false} />
                        <span className={'num' + (n ? '' : ' maybe')}>{n ? String(n).padStart(2, '0') : '?'}</span>
                      </button>
                    )
                  })}
                </div>
              </div>
            </div>
            <div className="seqstrip">
              <div className="col" style={{ gap: 2, width: 150 }}>
                <span className="caps">Reading order</span>
                <span className="small muted">updates as you drag</span>
              </div>
              <div className="thumbs">
                {order.map((it, i) => (
                  <div key={it.id} onClick={() => s.setSel([it.id])} style={{ cursor: 'pointer' }}>
                    <img src={thumbUrl(it)} alt="" />
                    <span className="tiny muted">{String(i + 1).padStart(2, '0')}</span>
                  </div>
                ))}
              </div>
              <button className="btn" onClick={() => s.editInDarktable(order.map(i => i.id))} disabled={!order.length}>Edit whole series in darktable</button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
