import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, BoardArrow, BoardEnd, BoardNote, BoardShape, Frame, LastAdd, Photo, thumbUrl } from '../api'
import { useStore } from '../store'
import { AddPhotosDialog } from './AddPhotos'

type Item = Photo & { x: number; y: number }
type Tool = 'select' | 'note' | 'arrow'
type Rect = { x: number; y: number; w: number; h: number }
type Pt = { x: number; y: number }

export const NOTE_COLORS = ['#f5d76e', '#f6b26b', '#f4a6c0', '#b9a6f0', '#9cc8f0', '#a8dba0', '#d8d8dc']

function size(p: Photo) {
  let w = p.width || 3, h = p.height || 2
  if ([5, 6, 7, 8].includes(p.orientation)) [w, h] = [h, w]
  const r = w / h
  return r >= 1 ? { w: 150, h: Math.round(150 / r) } : { w: Math.round(105 * r), h: 105 }
}
const itemRect = (it: Item): Rect => ({ x: it.x, y: it.y, ...size(it) })
const meets = (a: Rect, b: Rect) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h
const centerIn = (r: Rect, f: Rect) => { const cx = r.x + r.w / 2, cy = r.y + r.h / 2; return cx >= f.x && cx <= f.x + f.w && cy >= f.y && cy <= f.y + f.h }
const ptIn = (p: Pt, f: Rect) => p.x >= f.x && p.x <= f.x + f.w && p.y >= f.y && p.y <= f.y + f.h
const newKey = () => Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4)
const round = (p: Pt) => ({ x: Math.round(p.x), y: Math.round(p.y) })

// where an arrow touches a box: on its border, on the line towards the other end
function edgePoint(r: Rect, toward: Pt): Pt {
  const cx = r.x + r.w / 2, cy = r.y + r.h / 2
  const dx = toward.x - cx, dy = toward.y - cy
  if (!dx && !dy) return { x: cx, y: cy }
  const t = Math.min(Math.abs((r.w / 2 + 6) / (dx || 1e-9)), Math.abs((r.h / 2 + 6) / (dy || 1e-9)))
  return t >= 1 ? { x: toward.x, y: toward.y } : { x: cx + dx * t, y: cy + dy * t }
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

type MoveOrig = { photos: Map<number, Pt>; notes: Map<string, Pt>; ends: { key: string; end: 'a' | 'b'; x: number; y: number }[] }
type Drag =
  | ({ kind: 'move'; sx: number; sy: number; moved: boolean; click?: { photo?: number; shape?: string } } & MoveOrig)
  | ({ kind: 'frame'; fi: number; sx: number; sy: number; of: Frame } & MoveOrig)
  | { kind: 'resize'; fi: number; sx: number; sy: number; of: Frame }
  | { kind: 'note-resize'; key: string; sx: number; sy: number; ow: number; oh: number }
  | { kind: 'marquee'; x0: number; y0: number; basePhotos: number[]; baseShapes: string[]; last: string }
  | { kind: 'pan'; sx: number; sy: number; sl: number; st: number }
  | { kind: 'arrow'; a: BoardEnd }

export function BoardView() {
  const s = useStore()
  const sid = s.boardSeries ?? s.series[0]?.id ?? null
  const [frames, setFrames] = useState<Frame[]>([])
  const [items, setItems] = useState<Item[]>([])
  const [shapes, setShapesRaw] = useState<BoardShape[]>([])
  const [selShapes, setSelShapes] = useState<string[]>([])
  const [editing, setEditing] = useState<string | null>(null)
  const [tool, setTool] = useState<Tool>('select')
  const [marquee, setMarquee] = useState<Rect | null>(null)
  const [ghost, setGhost] = useState<{ a: Pt; b: Pt } | null>(null)
  const [space, setSpace] = useState(false)
  const [name, setName] = useState('')
  const [zoom, setZoom] = useState(1)
  const [lastAdd, setLastAdd] = useState<LastAdd | null>(null)
  const [adding, setAdding] = useState(false)
  const drag = useRef<Drag | null>(null)
  const saveTimer = useRef<number>()
  const shapesTimer = useRef<number>()
  const shapesDirty = useRef(false)
  const framesDirty = useRef(false)
  const inner = useRef<HTMLDivElement>(null)
  const wrap = useRef<HTMLDivElement>(null)

  const notes = useMemo(() => shapes.filter((x): x is BoardNote => x.kind === 'note'), [shapes])
  const arrows = useMemo(() => shapes.filter((x): x is BoardArrow => x.kind === 'arrow'), [shapes])
  const setShapes = (fn: (prev: BoardShape[]) => BoardShape[]) => { shapesDirty.current = true; setShapesRaw(fn) }

  const load = useCallback(() => {
    if (sid == null) return Promise.resolve(null)
    return api.board(sid).then(r => {
      const its = r.items.map(p => ({ ...p, x: p.x || 0, y: p.y || 0 }))
      setFrames(r.frames); setItems(its); setName(r.series.name); setLastAdd(r.last_add || null)
      shapesDirty.current = false; setShapesRaw(r.shapes || [])
      return { items: its, frames: r.frames }
    }).catch(e => { s.toast(e.message); return null })
  }, [sid])
  useEffect(() => { setAdding(false); setSelShapes([]); setEditing(null); load() }, [load])

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

  // where each arrow end is right now: on the photo or note it's attached to, else its own point
  const byPhoto = useMemo(() => new Map(items.map(it => [it.id, it])), [items])
  const byNote = useMemo(() => new Map(notes.map(n => [n.key, n])), [notes])
  const endRect = (e: BoardEnd): Rect | null => {
    if (e.photo != null) { const it = byPhoto.get(e.photo); return it ? itemRect(it) : null }
    if (e.note) { const n = byNote.get(e.note); return n ? { x: n.x, y: n.y, w: n.w, h: n.h } : null }
    return null
  }
  const center = (e: BoardEnd): Pt => { const r = endRect(e); return r ? { x: r.x + r.w / 2, y: r.y + r.h / 2 } : { x: e.x, y: e.y } }
  const arrowPts = (a: BoardArrow) => {
    const ra = endRect(a.a), rb = endRect(a.b)
    const ca = center(a.a), cb = center(a.b)
    return { p: ra ? edgePoint(ra, cb) : ca, q: rb ? edgePoint(rb, ca) : cb }
  }

  // notes and arrows: saved a moment after the last change, with arrow ends' points kept current
  useEffect(() => {
    if (!shapesDirty.current || sid == null) return
    window.clearTimeout(shapesTimer.current)
    shapesTimer.current = window.setTimeout(() => {
      shapesDirty.current = false
      const out = shapes.map(x => (x.kind === 'arrow' ? { ...x, a: { ...x.a, ...round(center(x.a)) }, b: { ...x.b, ...round(center(x.b)) } } : x))
      api.shapesSave(sid, out).catch(e => s.toast(e.message))
    }, 600)
  }, [shapes])

  const toBoard = (e: { clientX: number; clientY: number }): Pt => {
    const r = inner.current!.getBoundingClientRect()
    return { x: (e.clientX - r.left) / zoom, y: (e.clientY - r.top) / zoom }
  }
  const hitAt = (p: Pt): BoardEnd => {
    for (let i = notes.length - 1; i >= 0; i--) { const n = notes[i]; if (ptIn(p, n)) return { x: p.x, y: p.y, note: n.key } }
    for (let i = items.length - 1; i >= 0; i--) { if (ptIn(p, itemRect(items[i]))) return { x: p.x, y: p.y, photo: items[i].id } }
    return { x: p.x, y: p.y }
  }

  const capture = (e: React.PointerEvent) => { (e.currentTarget as Element).setPointerCapture?.(e.pointerId) }
  // on a touch screen, dragging empty space moves around (there's no Space key); on desktop it draws a selection box
  const panWanted = (e: React.PointerEvent, bg = false) => e.button === 1 || space || (bg && e.pointerType === 'touch' && tool === 'select')
  const startPan = (e: React.PointerEvent) => {
    e.preventDefault()
    const w = wrap.current!
    drag.current = { kind: 'pan', sx: e.clientX, sy: e.clientY, sl: w.scrollLeft, st: w.scrollTop }
    capture(e)
  }

  const moveOrig = (photoIds: number[], noteKeys: string[], arrowKeys: string[]): MoveOrig => {
    const ps = new Set(photoIds), ns = new Set(noteKeys), as = new Set(arrowKeys)
    const ends: MoveOrig['ends'] = []
    for (const a of arrows) {
      for (const end of ['a', 'b'] as const) {
        const e = a[end]
        if (e.photo == null && !e.note && as.has(a.key)) ends.push({ key: a.key, end, x: e.x, y: e.y })
      }
    }
    return {
      photos: new Map(items.filter(x => ps.has(x.id)).map(x => [x.id, { x: x.x, y: x.y }])),
      notes: new Map(notes.filter(n => ns.has(n.key)).map(n => [n.key, { x: n.x, y: n.y }])),
      ends,
    }
  }
  const startMove = (e: React.PointerEvent, photoIds: number[], shapeKeys: string[], click?: { photo?: number; shape?: string }) => {
    const ks = new Set(shapeKeys)
    const o = moveOrig(photoIds, notes.filter(n => ks.has(n.key)).map(n => n.key), arrows.filter(a => ks.has(a.key)).map(a => a.key))
    drag.current = { kind: 'move', sx: e.clientX, sy: e.clientY, moved: false, click, ...o }
    capture(e)
  }

  // pressing on a photo, a note or an arrow
  const onObjDown = (e: React.PointerEvent, obj: { photo?: number; shape?: string }) => {
    if (editing && obj.shape === editing) return  // typing in this note
    e.stopPropagation()
    if (panWanted(e)) { startPan(e); return }
    e.preventDefault()
    if (editing) setEditing(null)
    if (tool === 'arrow') { startArrow(e); return }
    if (tool === 'note') { addNote(toBoard(e)); setTool('select'); return }  // a note goes on top of whatever is there
    let photos = s.sel.filter(id => byPhoto.has(id)), sh = selShapes
    const isSel = obj.photo != null ? photos.includes(obj.photo) : sh.includes(obj.shape!)
    if (e.ctrlKey || e.metaKey || e.shiftKey) {
      if (obj.photo != null) photos = isSel ? photos.filter(x => x !== obj.photo) : [...photos, obj.photo]
      else sh = isSel ? sh.filter(x => x !== obj.shape) : [...sh, obj.shape!]
      s.setSel(photos); setSelShapes(sh)
      if (isSel) return
      startMove(e, photos, sh)
      return
    }
    if (!isSel) {
      photos = obj.photo != null ? [obj.photo] : []
      sh = obj.shape ? [obj.shape] : []
      s.setSel(photos); setSelShapes(sh)
      startMove(e, photos, sh)
    } else startMove(e, photos, sh, obj)  // a plain click (no drag) on a selected one selects just it
  }

  const onBgDown = (e: React.PointerEvent) => {
    if (e.target !== e.currentTarget && !(e.target as HTMLElement).dataset?.bg) return
    if (panWanted(e, true)) { startPan(e); return }
    if (e.button !== 0) return
    e.preventDefault()
    if (editing) { setEditing(null); return }
    const p = toBoard(e)
    if (tool === 'note') { addNote(p); setTool('select'); return }
    if (tool === 'arrow') { startArrow(e); return }
    const add = e.shiftKey || e.ctrlKey || e.metaKey
    if (!add) { s.setSel([]); setSelShapes([]) }
    drag.current = { kind: 'marquee', x0: p.x, y0: p.y, basePhotos: add ? s.sel.filter(id => byPhoto.has(id)) : [], baseShapes: add ? selShapes : [], last: '' }
    capture(e)
  }

  const startArrow = (e: React.PointerEvent) => {
    const a = hitAt(toBoard(e))
    drag.current = { kind: 'arrow', a }
    setGhost({ a: center(a), b: toBoard(e) })
    capture(e)
  }

  const onFrameDown = (e: React.PointerEvent, fi: number, kind: 'frame' | 'resize') => {
    e.stopPropagation()
    if (panWanted(e)) { startPan(e); return }
    e.preventDefault()
    const f = frames[fi]
    if (kind === 'resize') { drag.current = { kind, fi, sx: e.clientX, sy: e.clientY, of: { ...f } }; capture(e); return }
    const photos = items.filter(it => centerIn(itemRect(it), f)).map(i => i.id)
    const ns = notes.filter(n => centerIn(n, f)).map(n => n.key)
    const o = moveOrig(photos, ns, [])
    for (const a of arrows) for (const end of ['a', 'b'] as const) {
      const p = a[end]
      if (p.photo == null && !p.note && ptIn(p, f)) o.ends.push({ key: a.key, end, x: p.x, y: p.y })
    }
    drag.current = { kind: 'frame', fi, sx: e.clientX, sy: e.clientY, of: { ...f }, ...o }
    capture(e)
  }

  const applyMove = (o: MoveOrig, dx: number, dy: number) => {
    if (o.photos.size) setItems(prev => prev.map(it => { const p = o.photos.get(it.id); return p ? { ...it, x: Math.max(0, p.x + dx), y: Math.max(0, p.y + dy) } : it }))
    if (o.notes.size || o.ends.length) {
      const ends = new Map(o.ends.map(x => [x.key + x.end, x]))
      setShapes(prev => prev.map(x => {
        if (x.kind === 'note') { const p = o.notes.get(x.key); return p ? { ...x, x: Math.max(0, p.x + dx), y: Math.max(0, p.y + dy) } : x }
        const ea = ends.get(x.key + 'a'), eb = ends.get(x.key + 'b')
        if (!ea && !eb) return x
        return { ...x, a: ea ? { ...x.a, x: ea.x + dx, y: ea.y + dy } : x.a, b: eb ? { ...x.b, x: eb.x + dx, y: eb.y + dy } : x.b }
      }))
    }
  }

  const onMove = (e: React.PointerEvent) => {
    const d = drag.current
    if (!d) return
    if (d.kind === 'pan') { const w = wrap.current!; w.scrollLeft = d.sl - (e.clientX - d.sx); w.scrollTop = d.st - (e.clientY - d.sy); return }
    if (d.kind === 'arrow') { const b = toBoard(e); setGhost(g => (g ? { ...g, b } : g)); return }
    if (d.kind === 'marquee') {
      const p = toBoard(e)
      const r = { x: Math.min(d.x0, p.x), y: Math.min(d.y0, p.y), w: Math.abs(p.x - d.x0), h: Math.abs(p.y - d.y0) }
      setMarquee(r)
      const ph = items.filter(it => meets(itemRect(it), r)).map(it => it.id)
      const sh = [
        ...notes.filter(n => meets(n, r)).map(n => n.key),
        ...arrows.filter(a => { const { p: a1, q: b1 } = arrowPts(a); return ptIn(a1, r) && ptIn(b1, r) }).map(a => a.key),
      ]
      const photos = Array.from(new Set([...d.basePhotos, ...ph])), shs = Array.from(new Set([...d.baseShapes, ...sh]))
      const sig = photos.join(',') + '|' + shs.join(',')
      if (sig !== d.last) { d.last = sig; s.setSel(photos); setSelShapes(shs) }
      return
    }
    const dx = (e.clientX - d.sx) / zoom, dy = (e.clientY - d.sy) / zoom
    if (d.kind === 'move') {
      if (!d.moved && Math.abs(dx) + Math.abs(dy) < 3 / zoom) return
      d.moved = true
      applyMove(d, dx, dy)
    } else if (d.kind === 'frame') {
      applyMove(d, dx, dy)
      setFrames(prev => prev.map((f, i) => (i === d.fi ? { ...f, x: Math.max(0, d.of.x + dx), y: Math.max(0, d.of.y + dy) } : f)))
    } else if (d.kind === 'resize') {
      setFrames(prev => prev.map((f, i) => (i === d.fi ? { ...f, w: Math.max(160, d.of.w + dx), h: Math.max(120, d.of.h + dy) } : f)))
    } else if (d.kind === 'note-resize') {
      setShapes(prev => prev.map(x => (x.kind === 'note' && x.key === d.key ? { ...x, w: Math.max(80, d.ow + dx), h: Math.max(50, d.oh + dy) } : x)))
    }
  }

  const onUp = (e: React.PointerEvent) => {
    const d = drag.current
    if (!d) return
    drag.current = null
    if (d.kind === 'pan') return
    if (d.kind === 'marquee') { setMarquee(null); return }
    if (d.kind === 'arrow') {
      setGhost(null)
      const b = hitAt(toBoard(e))
      const pa = center(d.a), pb = center(b)
      const same = (d.a.photo != null && d.a.photo === b.photo) || (!!d.a.note && d.a.note === b.note)
      if (same || Math.hypot(pa.x - pb.x, pa.y - pb.y) < 12) { setTool('select'); return }
      const key = newKey()
      setShapes(prev => [...prev, { key, kind: 'arrow', a: d.a, b }])
      s.setSel([]); setSelShapes([key]); setTool('select')
      return
    }
    if (d.kind === 'note-resize') return
    if (d.kind === 'move' && !d.moved) {
      if (d.click) { s.setSel(d.click.photo != null ? [d.click.photo] : []); setSelShapes(d.click.shape ? [d.click.shape] : []) }
      return
    }
    if (d.kind === 'move' && !d.photos.size) return
    if (d.kind !== 'move') framesDirty.current = true
    setItems(its => { setFrames(frs => { save(its, frs, d.kind !== 'move'); return frs }); return its })
  }

  const addNote = (p: Pt, text = '') => {
    const key = newKey()
    const n: BoardNote = { key, kind: 'note', x: Math.max(0, Math.round(p.x - 90)), y: Math.max(0, Math.round(p.y - 50)), w: 180, h: 110, text, color: NOTE_COLORS[0] }
    setShapes(prev => [...prev, n])
    s.setSel([]); setSelShapes([key]); setEditing(key)
  }
  const setNoteText = (key: string, text: string) => setShapes(prev => prev.map(x => (x.kind === 'note' && x.key === key ? { ...x, text } : x)))
  const deleteShapes = (keys: string[]) => {
    const ks = new Set(keys)
    setShapes(prev => prev.filter(x => !ks.has(x.key) && !(x.kind === 'arrow' && ((x.a.note && ks.has(x.a.note)) || (x.b.note && ks.has(x.b.note))))))
    setSelShapes(sel => sel.filter(k => !ks.has(k)))
  }
  const stopEditing = (key: string) => {
    setEditing(cur => (cur === key ? null : cur))
    const n = notes.find(x => x.key === key)
    if (n && !n.text.trim()) deleteShapes([key])  // an empty note goes away
  }
  const labelArrow = (a: BoardArrow) => {
    const v = window.prompt('Text on the arrow', a.text || '')
    if (v == null) return
    setShapes(prev => prev.map(x => (x.key === a.key && x.kind === 'arrow' ? { ...x, text: v.trim() || undefined } : x)))
  }
  const colorNotes = (c: string) => {
    const ks = new Set(selShapes)
    setShapes(prev => prev.map(x => (x.kind === 'note' && ks.has(x.key) ? { ...x, color: c } : x)))
  }

  // keys: V select, N note, A arrow, Space (hold) to pan, Delete, Esc, Ctrl+A
  useEffect(() => {
    const typing = (t: EventTarget | null) => { const el = t as HTMLElement; return !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable) }
    const down = (e: KeyboardEvent) => {
      if (adding || typing(e.target)) return
      if (e.key === ' ') { e.preventDefault(); setSpace(true); return }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a') { e.preventDefault(); s.setSel(items.map(i => i.id)); setSelShapes(shapes.map(x => x.key)); return }
      if (e.ctrlKey || e.metaKey || e.altKey) return
      const k = e.key.toLowerCase()
      if (k === 'v') setTool('select')
      else if (k === 'n') setTool('note')
      else if (k === 'a') setTool('arrow')
      else if (e.key === 'Escape') { setTool('select'); setSelShapes([]) }
      else if (e.key === 'Delete' || e.key === 'Backspace') {
        if (selShapes.length) { e.preventDefault(); deleteShapes(selShapes) }
        else if (s.sel.some(id => byPhoto.has(id))) s.toast('To take photos off the board, use “Remove from board”.')
      }
    }
    const up = (e: KeyboardEvent) => { if (e.key === ' ') setSpace(false) }
    window.addEventListener('keydown', down); window.addEventListener('keyup', up)
    return () => { window.removeEventListener('keydown', down); window.removeEventListener('keyup', up) }
  })

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
  const selPhotosHere = s.sel.filter(id => byPhoto.has(id))
  const removeSel = async () => {
    if (selShapes.length) deleteShapes(selShapes)
    const ids = selPhotosHere
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
  const onAdded = async (n: number) => {
    setAdding(false)
    s.toast(`${n} photo${n === 1 ? '' : 's'} added to “${name}”`)
    const r = await load()
    if (r) save(r.items, r.frames, false)  // stores the new reading order
    s.reloadSeries(); s.reloadTags()
  }
  const showInLibrary = () => { if (sid == null) return; s.patchFilters({ series: sid, folder: null, month: null, tag: null }); s.setSort([{ f: 'custom', d: 'asc' }]); s.setView('grid') }

  const W = Math.max(1400, ...frames.map(f => f.x + f.w + 200), ...items.map(i => i.x + 400), ...notes.map(n => n.x + n.w + 300))
  const H = Math.max(900, ...frames.map(f => f.y + f.h + 200), ...items.map(i => i.y + 300), ...notes.map(n => n.y + n.h + 300))
  const selSet = new Set(s.sel), shSet = new Set(selShapes)
  const selNotes = notes.filter(n => shSet.has(n.key))
  const canvasCursor = space ? 'grab' : tool === 'select' ? 'default' : 'crosshair'
  const grab = tool === 'select' && !space ? 'grab' : 'inherit'
  const nSh = selShapes.length

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
            “+ Add photos” brings in whole folders or everything matching a rating, label, tag or date. Frames are chapters; photos in a Maybe pile are not part of the series.
            <br /><br />Drag on empty space to select several things (Shift adds). Hold Space or the middle mouse button and drag to move around. Keys: V select, N note, A arrow, Delete removes notes and arrows. Double-click empty space for a note, an arrow to write on it.
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
              <button className="btn primary" onClick={() => setAdding(true)} title="Add photos from folders, or by rating, label, tag and date">+ Add photos</button>
              <button className="btn" onClick={() => addFrame(false)}>+ Frame</button>
              <button className="btn" onClick={() => addFrame(true)}>+ Maybe pile</button>
              <button className="btn" onClick={showInLibrary}>Show in library</button>
              <button className="btn" onClick={rename}>Rename</button>
              <button className="btn danger" onClick={del}>Delete</button>
            </div>
            <div className="board-area">
              <div className="canvas-wrap" ref={wrap} style={{ cursor: canvasCursor }}
                onWheel={e => { if (e.ctrlKey) { e.preventDefault(); setZoom(z => Math.min(2, Math.max(.3, +(z - Math.sign(e.deltaY) * .1).toFixed(2)))) } }}>
                <div className="canvas" data-bg="1" style={{ width: W * zoom, height: H * zoom }} onPointerDown={onBgDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}
                  onDoubleClick={e => { if (tool === 'select' && (e.target as HTMLElement).dataset?.bg) addNote(toBoard(e)) }}>
                  <div ref={inner} data-bg="1" style={{ transform: `scale(${zoom})`, transformOrigin: '0 0', width: W, height: H, position: 'absolute', left: 0, top: 0 }}>
                    {frames.map((f, i) => (
                      <div key={f.id ?? 'n' + i} data-bg="1" className={'frame' + (f.maybe ? ' maybe' : '')} style={{ left: f.x, top: f.y, width: f.w, height: f.h }}>
                        <span className="fl" onPointerDown={e => onFrameDown(e, i, 'frame')} onDoubleClick={e => { e.stopPropagation(); renameFrame(i) }} title="Drag to move (with what's inside), double-click to rename">
                          {f.label}{f.maybe ? ' · not in series' : ''}
                        </span>
                        <button className="fx" aria-label="Remove frame" onPointerDown={e => e.stopPropagation()} onClick={() => deleteFrame(i)}>×</button>
                        <span className="rs" onPointerDown={e => onFrameDown(e, i, 'resize')} />
                      </div>
                    ))}
                    {items.map(it => {
                      const { w, h } = size(it)
                      const n = pos.get(it.id)
                      return (
                        <button key={it.id} className={'bitem' + (selSet.has(it.id) ? ' sel' : '')} style={{ left: it.x, top: it.y, width: w, height: h, zIndex: selSet.has(it.id) ? 5 : 1, cursor: grab }}
                          onPointerDown={e => onObjDown(e, { photo: it.id })} onDoubleClick={e => { e.stopPropagation(); if (tool === 'select') s.editInDarktable([it.id]) }} aria-label={it.filename + (n ? `, position ${n}` : ', maybe')}>
                          <img src={thumbUrl(it)} alt="" draggable={false} />
                          <span className={'num' + (n ? '' : ' maybe')}>{n ? String(n).padStart(2, '0') : '?'}</span>
                        </button>
                      )
                    })}
                    {notes.map(nt => (
                      <div key={nt.key} className={'bnote' + (shSet.has(nt.key) ? ' sel' : '')} style={{ left: nt.x, top: nt.y, width: nt.w, height: nt.h, background: nt.color, cursor: editing === nt.key ? 'text' : grab }}
                        onPointerDown={e => onObjDown(e, { shape: nt.key })} onDoubleClick={e => { e.stopPropagation(); if (tool === 'select') { setSelShapes([nt.key]); setEditing(nt.key) } }}>
                        {editing === nt.key
                          ? <textarea autoFocus value={nt.text} placeholder="Write a note" onChange={e => setNoteText(nt.key, e.target.value)}
                              onBlur={() => stopEditing(nt.key)} onKeyDown={e => { if (e.key === 'Escape') (e.target as HTMLTextAreaElement).blur() }} />
                          : <div className="bnote-t">{nt.text}</div>}
                        <span className="nrs" onPointerDown={e => { e.stopPropagation(); e.preventDefault(); drag.current = { kind: 'note-resize', key: nt.key, sx: e.clientX, sy: e.clientY, ow: nt.w, oh: nt.h }; capture(e) }} />
                      </div>
                    ))}
                    <svg className="barrows" width={W} height={H}>
                      <defs>
                        <marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#c8c8cc" /></marker>
                        <marker id="ahs" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#E8A33D" /></marker>
                      </defs>
                      {arrows.map(a => {
                        const { p, q } = arrowPts(a)
                        const on = shSet.has(a.key)
                        return (
                          <g key={a.key}>
                            <line x1={p.x} y1={p.y} x2={q.x} y2={q.y} stroke={on ? '#E8A33D' : '#c8c8cc'} strokeWidth={2} markerEnd={`url(#${on ? 'ahs' : 'ah'})`} />
                            <line className="hit" x1={p.x} y1={p.y} x2={q.x} y2={q.y} style={{ cursor: grab === 'grab' ? 'pointer' : 'inherit' }}
                              onPointerDown={e => onObjDown(e as unknown as React.PointerEvent, { shape: a.key })} onDoubleClick={e => { e.stopPropagation(); labelArrow(a) }} />
                          </g>
                        )
                      })}
                      {ghost && <line x1={ghost.a.x} y1={ghost.a.y} x2={ghost.b.x} y2={ghost.b.y} stroke="#E8A33D" strokeWidth={2} strokeDasharray="6 4" markerEnd="url(#ahs)" />}
                    </svg>
                    {arrows.filter(a => a.text).map(a => {
                      const { p, q } = arrowPts(a)
                      return (
                        <span key={a.key} className={'alabel' + (shSet.has(a.key) ? ' sel' : '')} style={{ left: (p.x + q.x) / 2, top: (p.y + q.y) / 2 }}
                          onPointerDown={e => onObjDown(e, { shape: a.key })} onDoubleClick={e => { e.stopPropagation(); labelArrow(a) }}>{a.text}</span>
                      )
                    })}
                    {marquee && <div className="marquee" style={{ left: marquee.x, top: marquee.y, width: marquee.w, height: marquee.h }} />}
                  </div>
                </div>
              </div>
              <div className="btools" role="toolbar" aria-label="Board tools">
                {([['select', 'Select and move (V)', '↖'], ['note', 'Note (N): click where it goes', '▭'], ['arrow', 'Arrow (A): drag from one thing to another', '↗']] as const).map(([t, title, icon]) => (
                  <button key={t} className={tool === t ? 'on' : ''} title={title} aria-label={title} aria-pressed={tool === t} onClick={() => setTool(t)}>{icon}</button>
                ))}
              </div>
              {(selPhotosHere.length > 0 || nSh > 0) && (
                <div className="bselbar">
                  <span className="small soft">{[selPhotosHere.length ? `${selPhotosHere.length} photo${selPhotosHere.length > 1 ? 's' : ''}` : '', nSh ? `${nSh} note${nSh > 1 ? 's' : ''} or arrow${nSh > 1 ? 's' : ''}` : ''].filter(Boolean).join(' · ')}</span>
                  {selNotes.length > 0 && NOTE_COLORS.map(c => <button key={c} className="swatch" style={{ background: c }} aria-label="Note color" onClick={() => colorNotes(c)} />)}
                  {selPhotosHere.length > 0 && <button className="btn" onClick={() => s.editInDarktable(selPhotosHere)}>Edit in darktable</button>}
                  <button className="btn" onClick={removeSel}>{selPhotosHere.length ? 'Remove from board' : 'Delete'}</button>
                </div>
              )}
              <div className="bzoom">
                <button onClick={() => setZoom(z => Math.max(.3, +(z - .1).toFixed(2)))} aria-label="Zoom out">−</button>
                <span className="small muted">{Math.round(zoom * 100)}%</span>
                <button onClick={() => setZoom(z => Math.min(2, +(z + .1).toFixed(2)))} aria-label="Zoom in">+</button>
              </div>
            </div>
            <div className="seqstrip">
              <div className="col" style={{ gap: 2, width: 150 }}>
                <span className="caps">Reading order</span>
                <span className="small muted">updates as you drag</span>
              </div>
              <div className="thumbs">
                {order.map((it, i) => (
                  <div key={it.id} onClick={() => { s.setSel([it.id]); setSelShapes([]) }} style={{ cursor: 'pointer' }}>
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
      {adding && sid != null && (
        <AddPhotosDialog sid={sid} name={name} frames={frames} lastAdd={lastAdd} onClose={() => setAdding(false)} onAdded={onAdded} />
      )}
    </div>
  )
}
