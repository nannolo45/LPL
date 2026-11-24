import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { api, BoardArrow, BoardData, BoardEnd, BoardNote, BoardShape, Frame, GroupInfo, LaneInfo, Photo, Target, thumbUrl } from '../api'
import { useStore } from '../store'
import { keyOf, num2 } from '../seriesnav'
import { bumpSeries, takePendingUndo, useSeriesRev } from '../seriesops'
import { useAsk } from './Ask'
import { FolderIcon, LinkIcon } from './SeriesTree'
import { actionFor, comboOf, keysOf, typingIn, useShortcutsVersion } from '../shortcuts'
import { getSettings, setSetting, Settings, useSettings } from '../settings'
import { LABELS } from '../fields'
import { EditOnPhone } from './PhoneEdit'
import { VersionBadge } from './Versions'
import { selectionIds } from '../devices'
import { FindCount, FindReq, useFind } from '../find'

type Item = Photo & { x: number; y: number }
type Tool = 'select' | 'note' | 'arrow' | 'frame'
type Rect = { x: number; y: number; w: number; h: number }
type Pt = { x: number; y: number }

// tool icons: a mouse pointer, a sticky note with a folded corner, a connector between two dots
const TOOL_ICONS = {
  select: <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M5 2.5l13.5 11.2-6.1.6 3.6 7.1-2.6 1.3-3.6-7.2-4.8 3.9z" /></svg>,
  note: <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" aria-hidden="true"><path d="M4 4h16v10l-6 6H4z" /><path d="M14 20v-6h6" /><path d="M8 9h8M8 12.5h4" strokeLinecap="round" /></svg>,
  arrow: <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true"><circle cx="5" cy="19" r="2.2" fill="currentColor" stroke="none" /><path d="M6.8 17.2L18 6" /><path d="M11.5 5.5H18.5V12.5" strokeLinejoin="round" /></svg>,
  frame: <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true"><path d="M7 3v18M17 3v18M3 7h18M3 17h18" /></svg>,
  fit: <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" /><rect x="8.5" y="8.5" width="7" height="7" rx="1" /></svg>,
  frames: <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="4" width="8" height="7" rx="1" /><rect x="13" y="4" width="8" height="7" rx="1" /><rect x="3" y="13" width="8" height="7" rx="1" /><path d="M15 16.5h5M17.5 14v5" /></svg>,
  undo: <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M9 14L4 9l5-5" /><path d="M4 9h10.5a5.5 5.5 0 010 11H11" /></svg>,
  redo: <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M15 14l5-5-5-5" /><path d="M20 9H9.5a5.5 5.5 0 000 11H13" /></svg>,
}

// frame looks: a soft fill and a matching border in the chosen colour
export const FRAME_COLORS: [string, string][] = [['', 'None'], ['#8a8a92', 'Grey'], ['#e5c547', 'Yellow'], ['#f0a35a', 'Orange'], ['#e5636a', 'Red'], ['#e07bb0', 'Pink'], ['#a07be0', 'Purple'], ['#5b9be6', 'Blue'], ['#5cc08a', 'Green']]
export const hexA = (hex: string, a: number) => { const n = parseInt(hex.slice(1), 16); return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})` }
function frameLook(f: Frame): React.CSSProperties {
  const st: React.CSSProperties = {}
  if (f.color) { st.background = hexA(f.color, .13); st.borderColor = hexA(f.color, .8) }
  if (f.border === 'dashed') st.borderStyle = 'dashed'
  if (f.border === 'none') st.borderColor = 'transparent'
  return st
}

// where you were on each board (centre in board coordinates and zoom), so switching views or tabs doesn't lose it
type BoardViewPos = { z: number; cx: number; cy: number }
const views = new Map<number, BoardViewPos>()
function readView(sid: number | null): BoardViewPos | null {
  if (sid == null) return null
  if (views.has(sid)) return views.get(sid)!
  try { const v = JSON.parse(localStorage.getItem('lpl.boardview.' + sid) || 'null'); if (v && isFinite(v.z)) { views.set(sid, v); return v } } catch { /* ignore */ }
  return null
}
function writeView(sid: number, v: BoardViewPos) {
  views.set(sid, v)
  try { localStorage.setItem('lpl.boardview.' + sid, JSON.stringify(v)) } catch { /* ignore */ }
}

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

// The reading order (frames in order, rows inside a frame, left to right) is worked out on the server, in one
// place for board and strip: server/lpl/series.py. The board shows the numbers the server sends back.

// linked: whether the board gave the order when the snapshot was taken (undo puts that back too)
// restoreToken: the server kept what "Take the order from the board" changes; going back to this snapshot asks it to put that back.
// redoRelink: going to this snapshot (a redo) does that relink again.
type Snap = { items: (Photo & { x: number; y: number })[]; frames: Frame[]; shapes: BoardShape[]; linked?: boolean; restoreToken?: number; redoRelink?: boolean }
const snapSig = (x: Snap) => JSON.stringify([x.items.map(i => [i.id, Math.round(i.x), Math.round(i.y)]), x.frames.map(f => [f.label, Math.round(f.x), Math.round(f.y), Math.round(f.w), Math.round(f.h), f.maybe]), x.shapes, !!x.linked])

type MoveOrig = { photos: Map<number, Pt>; notes: Map<string, Pt>; ends: { key: string; end: 'a' | 'b'; x: number; y: number }[] }
type Drag =
  | ({ kind: 'move'; sx: number; sy: number; moved: boolean; click?: { photo?: number; shape?: string } } & MoveOrig)
  | ({ kind: 'frame'; fi: number; sx: number; sy: number; of: Frame; inner: Map<number, Frame>; shift: boolean } & MoveOrig)
  | { kind: 'frameDraw'; x0: number; y0: number }
  | { kind: 'resize'; fi: number; sx: number; sy: number; of: Frame }
  | { kind: 'note-resize'; key: string; sx: number; sy: number; ow: number; oh: number }
  | { kind: 'marquee'; x0: number; y0: number; basePhotos: number[]; baseShapes: string[]; last: string }
  | { kind: 'pan'; sx: number; sy: number; sl: number; st: number }
  | { kind: 'arrow'; a: BoardEnd }
  | { kind: 'pinch'; d0: number; z0: number }

export interface BoardInfo { hidden: number; maybe: number }

/** The board of one series, or the shared board of a folder of series (each frame is then one of its series).
 *  `active` false: kept but not shown (the Series view is in Strip mode): keys are not listened to. */
export function BoardView({ target, active = true, onInfo, find, onFind }: {
  target: Target; active?: boolean; onInfo?: (i: BoardInfo) => void
  /** find a photo by name (the box is in the Series view's header) */
  find?: FindReq; onFind?: (c: FindCount) => void
}) {
  const s = useStore()
  const sid: number | null = target.id
  const isFolder = target.kind === 'folder'
  const viewKey = keyOf(target)
  const rev = useSeriesRev()
  const [askNode, ask] = useAsk()
  const [linked, setLinked] = useState(true)
  const [lanes, setLanes] = useState<LaneInfo[]>([])   // a folder's series, in their order
  const [parent, setParent] = useState<string | null>(null)   // the folder a single series is in
  const [frames, setFrames] = useState<Frame[]>([])
  const [items, setItems] = useState<Item[]>([])
  const [shapes, setShapesRaw] = useState<BoardShape[]>([])
  const [selShapes, setSelShapes] = useState<string[]>([])
  const [editing, setEditing] = useState<string | null>(null)
  const [tool, setTool] = useState<Tool>('select')
  // nested folders: a subfolder is a group frame on its top-level folder's board (what is in it, for its name bar);
  // asGroup: the next frame drawn is a new subfolder; focus: the group frame to show when a subfolder is opened alone
  const [groups, setGroups] = useState<Map<number, GroupInfo>>(new Map())
  const [asGroup, setAsGroup] = useState(false)
  const focusFrame = useRef<number | null>(null)
  useEffect(() => { if (tool !== 'frame') setAsGroup(false) }, [tool])
  const [marquee, setMarquee] = useState<Rect | null>(null)
  const [ghost, setGhost] = useState<{ a: Pt; b: Pt } | null>(null)
  const [space, setSpace] = useState(false)
  const [name, setName] = useState('')
  const opt = useSettings()
  const [zoom, setZoom] = useState(() => { const st = getSettings(); return (s.isMobile ? st.boardZoomPhone : st.boardZoom) / 100 })
  const sn = (v: number) => (opt.boardSnap ? Math.round(v / 20) * 20 : v)  // snap to the grid when that's on
  const drag = useRef<Drag | null>(null)
  // while dragging, the moving things are shifted with a CSS variable instead of redrawing the whole board
  // on every mouse move (that made photos trail far behind the pointer on a big board); saved on release
  const [moving, setMoving] = useState<{ photos: Set<number>; notes: Set<string>; frames: Set<number> } | null>(null)
  const [selFrames, setSelFrames] = useState<number[]>([])  // frames picked by clicking their name bar
  const live = useRef({ dx: 0, dy: 0 })
  const saveTimer = useRef<number>()
  const shapesTimer = useRef<number>()
  const shapesDirty = useRef(false)
  const framesDirty = useRef(false)
  const inner = useRef<HTMLDivElement>(null)
  const wrap = useRef<HTMLDivElement>(null)
  const origin = useRef<{ ox: number; oy: number } | null>(null)
  const needInitialScroll = useRef(true)
  const viewTimer = useRef<number>()
  const oxy = useRef({ ox: 0, oy: 0 })

  const notes = useMemo(() => shapes.filter((x): x is BoardNote => x.kind === 'note'), [shapes])
  const arrows = useMemo(() => shapes.filter((x): x is BoardArrow => x.kind === 'arrow'), [shapes])
  const setShapes = (fn: (prev: BoardShape[]) => BoardShape[]) => { shapesDirty.current = true; setShapesRaw(fn) }

  // ---- undo / redo: snapshots of the whole board (photos on it and their places, frames, notes, arrows).
  // A snapshot is taken just before each change; going back to one adds/removes photos on the server as needed.
  const cur = useRef<Snap>({ items: [], frames: [], shapes: [] })
  cur.current = { items, frames, shapes, linked }
  const undoStack = useRef<Snap[]>([])
  const redoStack = useRef<Snap[]>([])
  const [, setHist] = useState(0)
  const chain = useRef<Promise<void>>(Promise.resolve())
  const dragCp = useRef(false)      // this drag already saved its "before"
  const editCp = useRef(false)      // this note-typing session already saved its "before"
  const newNote = useRef<string | null>(null)
  const checkpoint = (snap: Snap = cur.current) => {
    const u = undoStack.current
    if (u.length && snapSig(u[u.length - 1]) === snapSig(snap)) return
    u.push(snap)
    if (u.length > 100) u.shift()
    redoStack.current = []
    setHist(h => h + 1)
  }
  const restore = async (to: Snap): Promise<number | undefined> => {
    if (sid == null) return
    if (to.restoreToken != null || to.redoRelink) {  // "Take the order from the board": the server puts back (or does again) what it changed, then the board loads again
      try {
        let tok: number | undefined
        if (to.redoRelink) tok = (await api.tlinkSet(target, true, 'board')).undo
        else await api.tlinkRestore(target, to.restoreToken!)
        window.clearTimeout(saveTimer.current)
        await load()
        setEditing(null); setSelShapes([]); setSelFrames([])
        s.reloadSeries(); s.reloadTags()
        return tok
      } catch (e: any) { s.toast(e.message); await load() }
      return
    }
    const have = new Set(cur.current.items.map(i => i.id)), want = new Set(to.items.map(i => i.id))
    const gone = [...have].filter(id => !want.has(id)), back = [...want].filter(id => !have.has(id))
    setItems(to.items); setFrames(to.frames); setShapes(() => to.shapes)
    cur.current = to
    setEditing(null); setSelShapes(sel => sel.filter(k => to.shapes.some(x => x.key === k)))
    setSelFrames([])
    s.setSel(s.sel.filter(id => want.has(id)))
    try {
      if (to.linked !== undefined && to.linked !== linkedRef.current) {  // the link as it was then; the save below then says what that means
        await api.tlinkSet(target, to.linked, 'none')
        setLinked(to.linked); linkedRef.current = to.linked
      }
      if (isFolder) {  // a folder's board is saved whole: what came back, what left, the frames (linked: the series follow)
        window.clearTimeout(saveTimer.current)
        const n = ++saveSeq.current
        const r = await api.tboardSave(target, { items: to.items.map(it => ({ id: it.id, x: Math.round(it.x), y: Math.round(it.y) })), frames: to.frames, remove: gone, restore: true })
        if (n === saveSeq.current) applySaved(r, true)
        s.reloadSeries(); s.reloadTags()
        return
      }
      if (gone.length) await api.seriesRemove(sid, gone)
      if (back.length) await api.seriesAdd(sid, back)
      framesDirty.current = true
      save(to.items, to.frames, true)
      if (gone.length || back.length) { s.reloadSeries(); s.reloadTags() }
    } catch (e: any) { s.toast(e.message) }
  }
  const step = (from: React.MutableRefObject<Snap[]>, to: React.MutableRefObject<Snap[]>, what: string) => {
    chain.current = chain.current.then(async () => {
      const target = from.current.pop()
      if (!target) { s.toast(`Nothing to ${what}`); return }
      const pushed: Snap = from === undoStack && !target.restoreToken ? cur.current : from === undoStack ? { ...cur.current, redoRelink: true } : cur.current
      to.current.push(pushed)
      setHist(h => h + 1)
      const tok = await restore(target)
      if (tok != null) pushed.restoreToken = tok  // a redo of the relink: undoing it again asks the server for what it kept this time
      await new Promise(r => window.setTimeout(r, 30))  // let the board redraw before the next step
    })
  }
  const undo = () => step(undoStack, redoStack, 'undo')
  const redo = () => step(redoStack, undoStack, 'redo')

  const linkedRef = useRef(true)
  const saveSeq = useRef(0)
  /** What the server says after a save: every photo's series and number (it works them out, see server/lpl/series.py),
   *  the folder's series, and the frames when they were sent (new ones have their ids and, on a linked folder
   *  board, their series). The places on screen are not touched: they may have moved on since. */
  const applySaved = (r: BoardData, withFrames: boolean) => {
    const by = new Map(r.items.map(p => [p.id, p]))
    setItems(prev => prev.map(it => { const p = by.get(it.id); return p && (p.num !== it.num || p.ord !== it.ord || p.series_id !== it.series_id) ? { ...it, num: p.num, ord: p.ord, series_id: p.series_id } : it }))
    if (withFrames) { setFrames(r.frames); framesDirty.current = false }
    if (r.lanes) setLanes(r.lanes)
    if (r.groups) setGroups(new Map(r.groups.map(g => [g.id, g])))
    if (r.warnings?.length) s.toast(r.warnings[0])
  }
  const load = useCallback(() => {
    if (sid == null) return Promise.resolve(null)
    return api.tboard(target).then(r => {
      const its = r.items.map(p => ({ ...p, x: p.x || 0, y: p.y || 0 }))
      setFrames(r.frames); setItems(its); setName(r.folder && isFolder ? r.folder.name : r.series?.name || '')
      setLinked(!!r.linked); linkedRef.current = !!r.linked
      setLanes(r.lanes || []); setParent(!isFolder && r.folder ? r.folder.name : null)
      setGroups(new Map((r.groups || []).map(g => [g.id, g]))); focusFrame.current = isFolder ? r.focus ?? null : null
      shapesDirty.current = false; setShapesRaw(r.shapes || [])
      return { items: its, frames: r.frames }
    }).catch(e => { if (!/not found/.test(e.message)) s.toast(e.message); return null })  // not found: it was just deleted, the view is about to change
  }, [target.kind, target.id])
  useEffect(() => { needInitialScroll.current = true; origin.current = null; const v = readView(viewKey); if (v) setZoom(v.z); setSelShapes([]); setSelFrames([]); setEditing(null); undoStack.current = []; redoStack.current = []; setHist(h => h + 1); load() }, [load])
  // something changed elsewhere (the strip, the tree, the link, Add photos): load again; what was here before is one undo step
  const seenRev = useRef(rev)
  useEffect(() => {
    if (!active || seenRev.current === rev) return
    seenRev.current = rev
    const before = cur.current
    chain.current = chain.current.then(async () => { window.clearTimeout(saveTimer.current); const r = await load(); if (r && (before.items.length || before.frames.length)) { const tk = takePendingUndo(target); checkpoint(tk != null ? { ...before, restoreToken: tk } : before) } })
  }, [rev, active])

  // photos hidden from the library: off the board too (they stay in the series and keep their place),
  // unless "Show hidden" is on; even then they're dimmed and don't count in the reading order
  const showHidden = !!s.filters.show_hidden
  const shown = useMemo(() => (showHidden ? items : items.filter(it => !it.hidden)), [items, showHidden])
  const hiddenCount = useMemo(() => items.filter(it => it.hidden).length, [items])
  // the order and the numbers are the server's (a photo's num: its place in its series); in a folder the series follow each other
  const order = useMemo(() => {
    const lane = new Map(lanes.map((l, i) => [l.id, i]))
    return items.filter(it => !it.hidden && it.num != null).sort((a, b) => (lane.get(a.series_id ?? -1) ?? 0) - (lane.get(b.series_id ?? -1) ?? 0) || a.num! - b.num!)
  }, [items, lanes])
  const pos = useMemo(() => new Map(order.map(it => [it.id, it.num!])), [order])
  // find by name: in reading order first, then the photos outside the order, row by row
  const findList = useMemo(() => {
    const rest = shown.filter(it => !pos.has(it.id)).sort((a, b) => Math.round(a.y / 100) - Math.round(b.y / 100) || a.x - b.x)
    return [...order, ...rest]
  }, [shown, order, pos])
  const found = useFind({
    list: findList, all: items, req: find, active, onCount: onFind,
    reveal: it => { s.setSel([it.id]); setSelShapes([]); setSelFrames([]); const r = itemRect(it); showRect({ x: r.x - 150, y: r.y - 100, w: 450, h: 305 }, Math.max(zoomRef.current, .6)) },
  })
  useEffect(() => { onInfo?.({ hidden: hiddenCount, maybe: isFolder ? 0 : items.length - hiddenCount - order.length }) }, [hiddenCount, items.length, order.length])

  const save = useCallback((its: Item[], frs: Frame[], withFrames: boolean) => {
    if (sid == null) return
    window.clearTimeout(saveTimer.current)
    saveTimer.current = window.setTimeout(() => {
      const wf = withFrames || framesDirty.current
      const n = ++saveSeq.current
      const before = new Map(its.map(it => [it.id, it.series_id]))
      api.tboardSave(target, { items: its.map(it => ({ id: it.id, x: Math.round(it.x), y: Math.round(it.y) })), frames: wf ? frs : undefined })
        .then(r => {
          if (n !== saveSeq.current) return  // a later save is on its way: its answer counts
          applySaved(r, wf)
          s.reloadSeries()
          if (isFolder && (wf || r.items.some(p => before.get(p.id) !== p.series_id))) s.reloadTags()
        })
        .catch(e => s.toast(e.message))
    }, 150)
  }, [target.kind, target.id])

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
      api.tshapesSave(target, out).catch(e => s.toast(e.message))
    }, 600)
  }, [shapes])

  const toBoard = (e: { clientX: number; clientY: number }): Pt => {
    const r = inner.current!.getBoundingClientRect()
    return { x: (e.clientX - r.left) / zoom, y: (e.clientY - r.top) / zoom }
  }
  const hitAt = (p: Pt): BoardEnd => {
    for (let i = notes.length - 1; i >= 0; i--) { const n = notes[i]; if (ptIn(p, n)) return { x: p.x, y: p.y, note: n.key } }
    for (let i = shown.length - 1; i >= 0; i--) { if (ptIn(p, itemRect(shown[i]))) return { x: p.x, y: p.y, photo: shown[i].id } }
    return { x: p.x, y: p.y }
  }

  const capture = (e: React.PointerEvent) => { (e.currentTarget as Element).setPointerCapture?.(e.pointerId) }
  // on a touch screen, dragging empty space moves around (there's no Space key); on desktop it draws a selection box
  // moving around: drag with the right or middle mouse button (anywhere), or hold Space and drag;
  // on a touch screen, one finger on empty space moves around and two fingers pinch to zoom
  const panWanted = (e: React.PointerEvent, bg = false) => (e.button === 1 && opt.boardPan !== 'right') || (e.button === 2 && opt.boardPan !== 'middle') || space
    || (bg && e.pointerType === 'touch' && tool === 'select' && opt.boardTouch === 'pan')
  const touches = useRef(new Map<number, Pt>())
  const pinchInfo = () => {
    const [a, b] = [...touches.current.values()]
    return { d: Math.hypot(a.x - b.x, a.y - b.y) || 1, mid: { clientX: (a.x + b.x) / 2, clientY: (a.y + b.y) / 2 } }
  }
  const touchDown = (e: React.PointerEvent) => {
    if (e.pointerType !== 'touch') return
    touches.current.set(e.pointerId, { x: e.clientX, y: e.clientY })
    if (touches.current.size === 2) {  // a second finger: stop whatever the first one started, and pinch
      if (drag.current?.kind === 'marquee') setMarquee(null)
      if (drag.current?.kind === 'arrow') setGhost(null)
      drag.current = { kind: 'pinch', d0: pinchInfo().d, z0: zoomRef.current }
      e.stopPropagation(); e.preventDefault()
    }
  }
  const touchMove = (e: React.PointerEvent) => {
    if (e.pointerType !== 'touch' || !touches.current.has(e.pointerId)) return
    touches.current.set(e.pointerId, { x: e.clientX, y: e.clientY })
    const d = drag.current
    if (d?.kind === 'pinch' && touches.current.size >= 2) {
      const { d: dist, mid } = pinchInfo()
      zoomTo(d.z0 * dist / d.d0, mid)
      e.stopPropagation()
    }
  }
  const touchUp = (e: React.PointerEvent) => {
    if (e.pointerType !== 'touch') return
    touches.current.delete(e.pointerId)
    if (drag.current?.kind === 'pinch') { if (touches.current.size < 2) drag.current = null; e.stopPropagation() }
  }
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
  const lastTap = useRef<{ id: number; t: number } | null>(null)
  const onObjDown = (e: React.PointerEvent, obj: { photo?: number; shape?: string }) => {
    if (editing && obj.shape === editing) return  // typing in this note
    if (e.pointerType === 'touch' && obj.photo != null && tool === 'select') {  // touch screens don't send double-clicks reliably
      const now = Date.now(), lt = lastTap.current
      lastTap.current = { id: obj.photo, t: now }
      if (lt && lt.id === obj.photo && now - lt.t < 320) { e.stopPropagation(); e.preventDefault(); lastTap.current = null; photoDouble(obj.photo); return }
    }
    e.stopPropagation()
    if (panWanted(e)) { startPan(e); return }
    e.preventDefault()
    if (editing) stopEditing(editing)
    if (tool === 'arrow') { startArrow(e); return }
    if (tool === 'note') { addNote(toBoard(e)); setTool('select'); return }  // a note goes on top of whatever is there
    if (tool === 'frame') { startFrameDraw(e); return }  // a frame can be drawn over photos and notes
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
    if (editing) { stopEditing(editing); return }
    const p = toBoard(e)
    if (tool === 'note') { addNote(p); setTool('select'); return }
    if (tool === 'frame') { startFrameDraw(e); return }
    if (tool === 'arrow') { startArrow(e); return }
    const add = e.shiftKey || e.ctrlKey || e.metaKey
    if (!add) { s.setSel([]); setSelShapes([]); setSelFrames([]) }
    drag.current = { kind: 'marquee', x0: p.x, y0: p.y, basePhotos: add ? s.sel.filter(id => byPhoto.has(id)) : [], baseShapes: add ? selShapes : [], last: '' }
    capture(e)
  }

  const startArrow = (e: React.PointerEvent) => {
    const a = hitAt(toBoard(e))
    drag.current = { kind: 'arrow', a }
    setGhost({ a: center(a), b: toBoard(e) })
    capture(e)
  }

  const startFrameDraw = (e: React.PointerEvent) => {
    const p = toBoard(e)
    drag.current = { kind: 'frameDraw', x0: p.x, y0: p.y }
    capture(e)
  }
  const onFrameDown = (e: React.PointerEvent, fi: number, kind: 'frame' | 'resize') => {
    e.stopPropagation()
    if (panWanted(e)) { startPan(e); return }
    e.preventDefault()
    if (tool === 'frame') { startFrameDraw(e); return }
    if (tool !== 'select') setTool('select')
    const f = frames[fi]
    if (kind === 'resize') { drag.current = { kind, fi, sx: e.clientX, sy: e.clientY, of: { ...f } }; capture(e); return }
    // frames lying inside this one move with it (and so do their photos and notes, which are inside this one too)
    const innerFrames = new Map(frames.map((g, j) => [j, g] as [number, Frame]).filter(([j, g]) => j !== fi && g.x >= f.x && g.y >= f.y && g.x + g.w <= f.x + f.w && g.y + g.h <= f.y + f.h).map(([j, g]) => [j, { ...g }]))
    const photos = items.filter(it => centerIn(itemRect(it), f)).map(i => i.id)
    const ns = notes.filter(n => centerIn(n, f)).map(n => n.key)
    const o = moveOrig(photos, ns, [])
    for (const a of arrows) for (const end of ['a', 'b'] as const) {
      const p = a[end]
      if (p.photo == null && !p.note && ptIn(p, f)) o.ends.push({ key: a.key, end, x: p.x, y: p.y })
    }
    drag.current = { kind: 'frame', fi, sx: e.clientX, sy: e.clientY, of: { ...f }, inner: innerFrames, shift: e.shiftKey || e.ctrlKey || e.metaKey, ...o }
    capture(e)
  }

  const applyMove = (o: MoveOrig, dx: number, dy: number) => {
    if (o.photos.size) setItems(prev => prev.map(it => { const p = o.photos.get(it.id); return p ? { ...it, x: p.x + dx, y: p.y + dy } : it }))
    if (o.notes.size || o.ends.length) {
      const ends = new Map(o.ends.map(x => [x.key + x.end, x]))
      setShapes(prev => prev.map(x => {
        if (x.kind === 'note') { const p = o.notes.get(x.key); return p ? { ...x, x: p.x + dx, y: p.y + dy } : x }
        const ea = ends.get(x.key + 'a'), eb = ends.get(x.key + 'b')
        if (!ea && !eb) return x
        return { ...x, a: ea ? { ...x.a, x: ea.x + dx, y: ea.y + dy } : x.a, b: eb ? { ...x.b, x: eb.x + dx, y: eb.y + dy } : x.b }
      }))
    }
  }

  const onMove = (e: React.PointerEvent) => {
    const d = drag.current
    if (!d) return
    if (d.kind === 'pinch') return
    if (d.kind === 'pan') { const w = wrap.current!; w.scrollLeft = d.sl - (e.clientX - d.sx); w.scrollTop = d.st - (e.clientY - d.sy); return }
    if (d.kind === 'arrow') { const b = toBoard(e); setGhost(g => (g ? { ...g, b } : g)); return }
    if (d.kind === 'frameDraw') {
      const p = toBoard(e)
      setMarquee({ x: Math.min(d.x0, p.x), y: Math.min(d.y0, p.y), w: Math.abs(p.x - d.x0), h: Math.abs(p.y - d.y0) })
      return
    }
    if (d.kind === 'marquee') {
      const p = toBoard(e)
      const r = { x: Math.min(d.x0, p.x), y: Math.min(d.y0, p.y), w: Math.abs(p.x - d.x0), h: Math.abs(p.y - d.y0) }
      setMarquee(r)
      const ph = shown.filter(it => meets(itemRect(it), r)).map(it => it.id)
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
    if (d.kind !== 'move' && d.kind !== 'frame' && !dragCp.current) { dragCp.current = true; checkpoint() }  // "before" of a resize
    if (d.kind === 'move' || d.kind === 'frame') {
      if (d.kind === 'move' && !d.moved && Math.abs(dx) + Math.abs(dy) < 3 / zoom) return
      if (!dragCp.current) {
        dragCp.current = true
        checkpoint()
        if (d.kind === 'move') d.moved = true
        setMoving({ photos: new Set(d.photos.keys()), notes: new Set(d.notes.keys()), frames: d.kind === 'frame' ? new Set([d.fi, ...d.inner.keys()]) : new Set() })
      }
      // the whole group moves by the same amount, snapped on the first thing
      const pts = [...d.photos.values(), ...d.notes.values(), ...(d.kind === 'frame' ? [d.of] : [])]
      let mx = dx, my = dy
      if (pts.length) {
        mx = sn(pts[0].x + dx) - pts[0].x; my = sn(pts[0].y + dy) - pts[0].y
      }
      live.current = { dx: mx, dy: my }
      inner.current?.style.setProperty('--mdx', mx + 'px')
      inner.current?.style.setProperty('--mdy', my + 'px')
    } else if (d.kind === 'resize') {
      setFrames(prev => prev.map((f, i) => (i === d.fi ? { ...f, w: Math.max(160, sn(d.of.w + dx)), h: Math.max(120, sn(d.of.h + dy)) } : f)))
    } else if (d.kind === 'note-resize') {
      setShapes(prev => prev.map(x => (x.kind === 'note' && x.key === d.key ? { ...x, w: Math.max(80, sn(d.ow + dx)), h: Math.max(50, sn(d.oh + dy)) } : x)))
    }
  }

  const onUp = (e: React.PointerEvent) => {
    const d = drag.current
    if (!d) return
    drag.current = null
    const started = dragCp.current
    dragCp.current = false
    if (d.kind === 'pan' || d.kind === 'pinch') return
    if (d.kind === 'marquee') { setMarquee(null); return }
    if (d.kind === 'frameDraw') {
      setMarquee(null)
      const p = toBoard(e)
      let r = { x: Math.min(d.x0, p.x), y: Math.min(d.y0, p.y), w: Math.abs(p.x - d.x0), h: Math.abs(p.y - d.y0) }
      if (r.w < 40 || r.h < 40) r = { x: d.x0 - 450, y: d.y0 - 150, w: 900, h: 300 }  // a click: a frame of the usual size there
      addFrame({ x: Math.round(sn(r.x)), y: Math.round(sn(r.y)), w: Math.round(sn(r.w)), h: Math.round(sn(r.h)) })
      setTool('select')
      return
    }
    if (d.kind === 'arrow') {
      setGhost(null)
      const b = hitAt(toBoard(e))
      const pa = center(d.a), pb = center(b)
      const same = (d.a.photo != null && d.a.photo === b.photo) || (!!d.a.note && d.a.note === b.note)
      if (same || Math.hypot(pa.x - pb.x, pa.y - pb.y) < 12) { setTool('select'); return }
      const key = newKey()
      checkpoint()
      setShapes(prev => [...prev, { key, kind: 'arrow', a: d.a, b }])
      s.setSel([]); setSelShapes([key]); setTool('select')
      return
    }
    if (d.kind === 'note-resize') return
    if ((d.kind === 'move' || d.kind === 'frame') && started) {  // put everything where it was dropped
      const { dx, dy } = live.current
      inner.current?.style.removeProperty('--mdx'); inner.current?.style.removeProperty('--mdy')
      setMoving(null)
      applyMove(d, dx, dy)
      if (d.kind === 'frame') setFrames(prev => prev.map((f, i) => (i === d.fi ? { ...f, x: d.of.x + dx, y: d.of.y + dy } : d.inner.has(i) ? { ...f, x: d.inner.get(i)!.x + dx, y: d.inner.get(i)!.y + dy } : f)))
    } else if (d.kind === 'frame') {  // a click on the frame's name: pick the frame (Shift/Ctrl adds)
      setSelFrames(sf => (d.shift ? (sf.includes(d.fi) ? sf.filter(x => x !== d.fi) : [...sf, d.fi]) : [d.fi]))
      if (!d.shift) { s.setSel([]); setSelShapes([]) }
      return
    }
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
    const n: BoardNote = { key, kind: 'note', x: Math.round(sn(p.x - 90)), y: Math.round(sn(p.y - 50)), w: 180, h: 110, text, color: opt.boardNoteColor || NOTE_COLORS[0] }
    checkpoint()
    newNote.current = key; editCp.current = true
    setShapes(prev => [...prev, n])
    s.setSel([]); setSelShapes([key]); setEditing(key)
  }
  const editNote = (key: string) => { editCp.current = false; newNote.current = null; setSelShapes([key]); setEditing(key) }
  const setNoteText = (key: string, text: string) => {
    if (!editCp.current) { editCp.current = true; checkpoint() }
    setShapes(prev => prev.map(x => (x.kind === 'note' && x.key === key ? { ...x, text } : x)))
  }
  const deleteShapes = (keys: string[], cp = true) => {
    if (cp) checkpoint()
    const ks = new Set(keys)
    setShapes(prev => prev.filter(x => !ks.has(x.key) && !(x.kind === 'arrow' && ((x.a.note && ks.has(x.a.note)) || (x.b.note && ks.has(x.b.note))))))
    setSelShapes(sel => sel.filter(k => !ks.has(k)))
  }
  const stopEditing = (key: string) => {
    setEditing(cur => (cur === key ? null : cur))
    const n = notes.find(x => x.key === key)
    if (n && !n.text.trim()) {  // an empty note goes away; a new one that was never written leaves no undo step
      if (newNote.current === key) { undoStack.current.pop(); setHist(h => h + 1) }
      deleteShapes([key], false)
    }
    newNote.current = null; editCp.current = false
  }
  const labelArrow = (a: BoardArrow) => {
    const v = window.prompt('Text on the arrow', a.text || '')
    if (v == null) return
    checkpoint()
    setShapes(prev => prev.map(x => (x.key === a.key && x.kind === 'arrow' ? { ...x, text: v.trim() || undefined } : x)))
  }
  const colorNotes = (c: string) => {
    const ks = new Set(selShapes)
    checkpoint()
    setShapes(prev => prev.map(x => (x.kind === 'note' && ks.has(x.key) ? { ...x, color: c } : x)))
  }

  // keys (changeable in the Shortcuts dialog): tools, remove, hold-to-pan, zoom, select all
  useShortcutsVersion()
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (!active || s.viewer != null || typingIn(e.target)) return
      const a = actionFor(e, ['board', 'photos'])
      if (a === 'pan') { e.preventDefault(); setSpace(true); return }
      if (a === 'selectAll') { e.preventDefault(); s.setSel(shown.map(i => i.id)); setSelShapes(shapes.map(x => x.key)); return }
      if (a === 'toolSelect') setTool('select')
      else if (a === 'toolNote') setTool('note')
      else if (a === 'toolArrow') setTool('arrow')
      else if (a === 'zoomIn') { e.preventDefault(); zoomTo(zoomRef.current + .1) }
      else if (a === 'zoomOut') { e.preventDefault(); zoomTo(zoomRef.current - .1) }
      else if (a === 'zoomReset') { e.preventDefault(); zoomTo(1) }
      else if (a === 'deselect') { setTool('select'); setSelShapes([]); setSelFrames([]) }
      else if (a === 'undo') { e.preventDefault(); undo() }
      else if (a === 'fitAll') { e.preventDefault(); fitAll() }
      else if (a === 'fitSel') { e.preventDefault(); fitSelection() }
      else if (a === 'nextFrame') { e.preventDefault(); frameStep(1) }
      else if (a === 'prevFrame') { e.preventDefault(); frameStep(-1) }
      else if (a === 'newFrame') { e.preventDefault(); setTool(t => (t === 'frame' ? 'select' : 'frame')) }
      else if (a === 'fullscreen') {  // in reading order, maybe piles after
        const list = [...order, ...shown.filter(it => !pos.has(it.id))]
        const k = list.findIndex(it => it.id === selPhotosHere[0])
        if (k >= 0) { e.preventDefault(); s.setFullscreen({ list, index: k }) }
      }
      else if (a === 'redo') { e.preventDefault(); redo() }
      else if (a === 'hide') { e.preventDefault(); toggleHidden() }
      else if (a === 'boardDelete') {
        if (!selShapes.length && !selFrames.length && !s.sel.some(id => byPhoto.has(id))) return
        e.preventDefault(); removeSel()
      }
    }
    const up = (e: KeyboardEvent) => { if (keysOf('pan').includes(comboOf(e)) || e.key === ' ') setSpace(false) }
    window.addEventListener('keydown', down); window.addEventListener('keyup', up)
    return () => { window.removeEventListener('keydown', down); window.removeEventListener('keyup', up) }
  })

  // zoom: Ctrl + wheel (or a trackpad pinch) zooms the board, keeping the point under the pointer still.
  // React's wheel handler can't stop the browser zooming the whole page, so this listener is a real one.
  const zoomRef = useRef(zoom)
  zoomRef.current = zoom
  const keepAt = useRef<{ bx: number; by: number; vx: number; vy: number } | null>(null)
  const zoomTo = (z: number, at?: { clientX: number; clientY: number }) => {
    const nz = Math.min(2, Math.max(.2, +z.toFixed(2)))
    const w = wrap.current
    if (w) {
      const r = w.getBoundingClientRect()
      const vx = at ? at.clientX - r.left : w.clientWidth / 2, vy = at ? at.clientY - r.top : w.clientHeight / 2
      keepAt.current = { bx: (w.scrollLeft + vx) / zoomRef.current, by: (w.scrollTop + vy) / zoomRef.current, vx, vy }
    }
    setZoom(nz)
  }
  useLayoutEffect(() => {
    const k = keepAt.current, w = wrap.current
    if (!k || !w) return
    keepAt.current = null
    w.scrollLeft = k.bx * zoom - k.vx
    w.scrollTop = k.by * zoom - k.vy
  }, [zoom])
  useEffect(() => {
    const w = wrap.current
    if (!w) return
    const h = (e: WheelEvent) => {
      const zoomWheel = getSettings().boardWheel === 'zoom'
      if (zoomWheel && (e.ctrlKey || e.metaKey)) { e.preventDefault(); w.scrollTop += e.deltaY; return }  // zoom mode: Ctrl + wheel scrolls
      if (zoomWheel && e.shiftKey) return  // sideways, as the browser does it
      if (!zoomWheel && !e.ctrlKey && !e.metaKey) return
      e.preventDefault()
      const step = Math.max(-0.25, Math.min(0.25, -e.deltaY * (e.deltaMode === 1 ? 0.05 : 0.002)))
      zoomTo(zoomRef.current * (1 + step), e)
    }
    w.addEventListener('wheel', h, { passive: false })
    return () => w.removeEventListener('wheel', h)
  }, [sid, items.length > 0])

  // ---- finding your way: show a region (a frame, the selection, everything) filling the screen
  const goTo = useRef<{ cx: number; cy: number } | null>(null)
  const showRect = (r: Rect | null, maxZoom = 1.5) => {
    const w = wrap.current
    if (!w || !r) return
    const pad = 60
    const nz = Math.min(maxZoom, Math.max(.2, +Math.min((w.clientWidth - 2 * pad) / Math.max(r.w, 1), (w.clientHeight - 2 * pad) / Math.max(r.h, 1)).toFixed(2)))
    const cx = r.x + r.w / 2, cy = r.y + r.h / 2
    if (nz === zoomRef.current) { w.scrollTo({ left: (cx - ox) * nz - w.clientWidth / 2, top: (cy - oy) * nz - w.clientHeight / 2, behavior: 'smooth' }); return }
    goTo.current = { cx, cy }
    setZoom(nz)
  }
  useLayoutEffect(() => {
    const g = goTo.current, w = wrap.current
    if (!g || !w) return
    goTo.current = null
    w.scrollLeft = (g.cx - ox) * zoom - w.clientWidth / 2
    w.scrollTop = (g.cy - oy) * zoom - w.clientHeight / 2
  })
  const bounds = (rs: Rect[]): Rect | null => {
    if (!rs.length) return null
    const x = Math.min(...rs.map(r => r.x)), y = Math.min(...rs.map(r => r.y))
    return { x, y, w: Math.max(...rs.map(r => r.x + r.w)) - x, h: Math.max(...rs.map(r => r.y + r.h)) - y }
  }
  const fitAll = () => showRect(bounds([...frames, ...shown.map(itemRect), ...notes]), 1)
  const fitSelection = () => {
    const r = bounds([...shown.filter(it => selSet.has(it.id)).map(itemRect), ...notes.filter(n => shSet.has(n.key))])
    if (r) showRect(r); else s.toast('Select something first')
  }
  const showFrame = (fi: number) => { const f = frames[fi]; if (f) { showRect(f); setFrameMenu(false) } }
  const frameStep = (d: number) => {
    if (!frames.length) return
    const w = wrap.current
    // the frame nearest the middle of the screen, then the next/previous one in reading order
    const fr = frames.map((f, i) => ({ f, i })).sort((a, b) => Math.round(a.f.y / 100) - Math.round(b.f.y / 100) || a.f.x - b.f.x)
    let k = 0
    if (w) {
      const cx = (w.scrollLeft + w.clientWidth / 2) / zoom + ox, cy = (w.scrollTop + w.clientHeight / 2) / zoom + oy
      k = fr.reduce((best, o, j) => (Math.hypot(o.f.x + o.f.w / 2 - cx, o.f.y + o.f.h / 2 - cy) < Math.hypot(fr[best].f.x + fr[best].f.w / 2 - cx, fr[best].f.y + fr[best].f.h / 2 - cy) ? j : best), 0)
    }
    showFrame(fr[(k + d + fr.length) % fr.length].i)
  }
  const [frameMenu, setFrameMenu] = useState(false)
  const [editFrame, setEditFrame] = useState<number | null>(null)

  // a new frame goes in the middle of what you're looking at, named at once
  const addFrame = (r?: Rect) => {
    const w = wrap.current
    const cx = w ? (w.scrollLeft + w.clientWidth / 2) / zoom + ox : 490, cy = w ? (w.scrollTop + w.clientHeight / 2) / zoom + oy : 190
    const nums = frames.map(f => parseInt(f.label, 10)).filter(n => !isNaN(n))
    let label = String((nums.length ? Math.max(...nums) : 0) + 1).padStart(2, '0')
    const group = isFolder && linked && asGroup  // drawn with "New subfolder": a group frame, which is a new folder in this one
    if (isFolder && linked) {  // here a frame is a series: it gets a name of its own at once (and is renamed as you type the real one)
      const taken = new Set([...s.series.map(x => x.name), ...s.seriesFolders.map(x => x.name), ...frames.map(f => f.label)])
      const base = group ? 'New folder' : 'New series'
      label = base
      for (let k = 2; taken.has(label); k++) label = base + ' ' + k
    }
    const rect = r || { x: Math.round(sn(cx - 450)), y: Math.round(sn(cy - 150)), w: 900, h: 300 }
    const nf: Frame[] = [...frames, { label, ...rect, maybe: 0, color: group ? '' : getSettings().boardFrameColor || '', ...(group ? { group: true } : {}) }]
    checkpoint(); setFrames(nf); save(items, nf, true); setEditFrame(nf.length - 1); setSelFrames([nf.length - 1])
  }
  const styleFrames = (p: Partial<Frame>) => {
    if (!selFrames.length) return
    checkpoint()
    const nf = frames.map((f, i) => (selFrames.includes(i) ? { ...f, ...p } : f)); setFrames(nf); save(items, nf, true)
  }
  const deleteFrames = async (idx: number[]) => {
    if (!idx.length) return
    if (isFolder && linked) {  // a group frame is a subfolder: removing it dissolves the folder (nothing is deleted), after asking
      const subs = idx.map(i => frames[i]).filter(f => f?.sub_id)
      if (subs.length) {
        const f = subs[0], g = groups.get(f.sub_id!)
        const yes = await ask({ title: `Dissolve the folder “${f.label}”?`, ok: 'Dissolve folder', danger: true,
          text: `This frame is the folder ${f.label}. Removing it dissolves the folder: its ${g?.series_count ?? ''} series move up one level and keep their frames, photos and order (their tags lose “${f.label}”). No series and no photo is deleted.` })
        if (!yes) return
        checkpoint()
        try { await api.folderDelete(f.sub_id!) } catch (e: any) { s.toast(e.message) }
        setSelFrames([])
        if (target.id === f.sub_id) s.setBoardSeries(-(s.seriesFolders.find(x => x.id === f.sub_id)?.parent_id ?? 0) || null)
        s.reloadSeries(); s.reloadTags(); bumpSeries()
        await load()
        return
      }
      const hit = idx.map(i => frames[i]).filter(f => f?.series_id)
      if (hit.length) {
        const n = items.filter(it => hit.some(f => f.series_id === it.series_id)).length
        const yes = await ask({ title: hit.length > 1 ? `Delete ${hit.length} series?` : `Delete the series “${hit[0].label}”?`, ok: hit.length > 1 ? 'Delete series' : 'Delete series', danger: true,
          text: `On this board a frame is a series of ${name}. Removing ${hit.length > 1 ? 'these frames deletes those series' : 'this frame deletes that series'}: ${n === 1 ? 'its photo leaves' : `${hit.length > 1 ? 'their' : 'its'} ${n} photos leave`} the board and ${n === 1 ? 'loses its' : 'lose their'} series tag. No photo is deleted. Undo on the board brings ${hit.length > 1 ? 'them' : 'it'} back as ${hit.length > 1 ? 'new series' : 'a new series'}. To keep the series and only stop frames being series, unlink the board first.` })
        if (!yes) return
        checkpoint()
        try { for (const f of hit) await api.seriesDelete(f.series_id!) } catch (e: any) { s.toast(e.message) }
        setSelFrames([])
        s.reloadSeries(); s.reloadTags()
        await load()
        return
      }
    }
    checkpoint()
    const names = idx.map(i => frames[i]?.label).filter(Boolean)
    const nf = frames.filter((_, i) => !idx.includes(i)); setFrames(nf); save(items, nf, true); setSelFrames([])
    s.toast(`${names.length > 1 ? names.length + ' frames' : 'Frame “' + names[0] + '”'} removed; the photos stay on the board (${keysOf('undo')[0] || 'Undo'} brings it back)`)
  }
  const renameFrame = (fi: number, v: string) => {
    setEditFrame(null)
    const f = frames[fi]
    if (!f || !v.trim() || v.trim() === f.label) return
    checkpoint()
    const nf = frames.map((x, i) => (i === fi ? { ...x, label: v.trim() } : x)); setFrames(nf); save(items, nf, true)
  }
  const deleteFrame = (fi: number) => deleteFrames([fi])
  const selPhotosHere = s.sel.filter(id => byPhoto.has(id))
  const photoDouble = (id: number) => {
    const how = opt.boardPhotoDouble
    if (how === 'darktable') { s.editInDarktable([id]); return }
    const list = [...order, ...shown.filter(it => !pos.has(it.id))]
    const k = list.findIndex(it => it.id === id)
    if ((how === 'fullscreen' || s.isMobile) && k >= 0) s.setFullscreen({ list, index: k })  // the phone has no large view
    else s.setViewer(id)
  }
  const toggleHidden = async (ids = selPhotosHere) => {
    if (!ids.length) return
    const hide = !ids.every(id => byPhoto.get(id)?.hidden)
    await s.hidePhotos(ids, hide)
    const set = new Set(ids)
    setItems(prev => prev.map(it => (set.has(it.id) ? { ...it, hidden: hide ? 1 : 0 } : it)))
    if (hide && !showHidden) s.setSel(s.sel.filter(id => !set.has(id)))
    window.setTimeout(() => setItems(its => { setFrames(frs => { save(its, frs, false); return frs }); return its }), 0)  // new reading order
  }
  const removeSel = async () => {
    if (selFrames.length && !selShapes.length && !selPhotosHere.length) { deleteFrames(selFrames); return }
    if (!selShapes.length && !selPhotosHere.length) return
    checkpoint()
    if (selShapes.length) deleteShapes(selShapes, false)
    const ids = selPhotosHere
    if (!ids.length || sid == null) return
    if (isFolder) { const r = await api.folderBoardRemove(sid, ids); saveSeq.current++; applySaved(r, false) }
    else await api.seriesRemove(sid, ids)
    setItems(prev => prev.filter(i => !ids.includes(i.id))); s.setSel([]); s.reloadSeries(); s.reloadTags()
    s.toast(`${ids.length} photo${ids.length > 1 ? 's' : ''} taken off ${isFolder && !linked ? 'the board (still in their series' : `“${name}” (files untouched`}; ${keysOf('undo')[0] || 'Undo'} puts them back)`)
  }
  // an endless board: the canvas reaches EDGE board pixels past everything in every direction (also left of and
  // above where the board starts), so you can grab it and move anywhere, like in Miro. Board point (x, y) sits
  // at ((x - ox) * zoom, (y - oy) * zoom) on the canvas.
  const EDGE = 6000
  const ox = Math.min(0, ...frames.map(f => f.x), ...items.map(i => i.x), ...notes.map(n => n.x)) - EDGE
  const oy = Math.min(0, ...frames.map(f => f.y), ...items.map(i => i.y), ...notes.map(n => n.y)) - EDGE
  const W = Math.max(1400, ...frames.map(f => f.x + f.w), ...items.map(i => i.x + 150), ...notes.map(n => n.x + n.w)) + EDGE - ox
  const H = Math.max(900, ...frames.map(f => f.y + f.h), ...items.map(i => i.y + 105), ...notes.map(n => n.y + n.h)) + EDGE - oy
  oxy.current = { ox, oy }
  useLayoutEffect(() => {
    const w = wrap.current
    if (!w) return
    if (needInitialScroll.current && (items.length || frames.length)) {  // back where you were on this board, or its start at the top left
      needInitialScroll.current = false
      const fg = focusFrame.current != null ? frames.find(f => f.id === focusFrame.current) : null
      if (fg) { origin.current = { ox, oy }; showRect(fg, 1); return }  // a subfolder opened alone: the same board, at its group frame
      const v = readView(viewKey)
      if (v) {
        w.scrollLeft = (v.cx - ox) * zoom - w.clientWidth / 2
        w.scrollTop = (v.cy - oy) * zoom - w.clientHeight / 2
        origin.current = { ox, oy }
        return
      }
      w.scrollLeft = (Math.min(0, ...frames.map(f => f.x), ...items.map(i => i.x)) - ox) * zoom - 40
      w.scrollTop = (Math.min(0, ...frames.map(f => f.y), ...items.map(i => i.y)) - oy) * zoom - 40
    } else if (origin.current && (origin.current.ox !== ox || origin.current.oy !== oy)) {  // the canvas grew left/up: keep the view still
      w.scrollLeft += (origin.current.ox - ox) * zoom
      w.scrollTop += (origin.current.oy - oy) * zoom
    }
    origin.current = { ox, oy }
  })
  const seriesCount = useMemo(() => { const m = new Map<number, number>(); for (const it of items) if (it.series_id != null && !it.hidden) m.set(it.series_id, (m.get(it.series_id) || 0) + 1); return m }, [items])
  // group frames lie under the frames in them (the larger first), so the frames inside stay within reach
  const frameOrder = useMemo(() => {
    const idx = frames.map((_, i) => i)
    const isG = (f: Frame) => isFolder && (f.sub_id != null || !!f.group)
    return [...idx.filter(i => isG(frames[i])).sort((a, b) => frames[b].w * frames[b].h - frames[a].w * frames[a].h), ...idx.filter(i => !isG(frames[i]))]
  }, [frames, isFolder])
  const insideCount = (f: Frame) => shown.filter(it => centerIn(itemRect(it), f)).length
  const selSet = new Set(s.sel), shSet = new Set(selShapes)
  const selNotes = notes.filter(n => shSet.has(n.key))
  const canvasCursor = space ? 'grab' : tool === 'select' ? 'default' : 'crosshair'
  const grab = tool === 'select' && !space ? 'grab' : 'inherit'
  const nSh = selShapes.length

  return (
    <>
            <div className="board-area">
              <div className="canvas-wrap" ref={wrap} style={{ cursor: drag.current?.kind === 'pan' ? 'grabbing' : canvasCursor }} onContextMenu={e => e.preventDefault()}
                onScroll={e => {  // remember where you are on this board (centre and zoom), for coming back
                  if (needInitialScroll.current || sid == null) return
                  const w = e.currentTarget
                  window.clearTimeout(viewTimer.current)
                  viewTimer.current = window.setTimeout(() => writeView(viewKey, { z: zoomRef.current, cx: (w.scrollLeft + w.clientWidth / 2) / zoomRef.current + oxy.current.ox, cy: (w.scrollTop + w.clientHeight / 2) / zoomRef.current + oxy.current.oy }), 200)
                }}
                onPointerDownCapture={touchDown} onPointerMoveCapture={touchMove} onPointerUpCapture={touchUp} onPointerCancelCapture={touchUp}>
                <div className="canvas" data-bg="1" style={{ width: W * zoom, height: H * zoom }} onPointerDown={onBgDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}
                  onDoubleClick={e => { if (tool === 'select' && (e.target as HTMLElement).dataset?.bg) addNote(toBoard(e)) }}>
                  <div ref={inner} data-bg="1" style={{ transform: `scale(${zoom}) translate(${-ox}px, ${-oy}px)`, transformOrigin: '0 0', width: W, height: H, position: 'absolute', left: 0, top: 0 }}>
                    {frameOrder.map(i => { const f = frames[i]; const grp = isFolder && (f.sub_id != null || f.group) ? groups.get(f.sub_id ?? -1) ?? null : undefined; return (
                      <div key={f.id ?? 'n' + i} data-bg="1" data-sub={f.sub_id ?? undefined} className={'frame' + (grp !== undefined ? ' group' + (linked ? '' : ' loose') : '') + (f.maybe ? ' maybe' : '') + (moving?.frames.has(i) ? ' moving' : '') + (selFrames.includes(i) ? ' fsel' : '')}
                        style={{ left: f.x, top: f.y, width: f.w, height: f.h, ...frameLook(f) }}>
                        {(['l', 'r', 'b'] as const).map(side => <span key={side} className={'fedge ' + side} onPointerDown={e => onFrameDown(e, i, 'frame')} />)}
                        {editFrame === i
                          ? <input className="fl fl-in" autoFocus defaultValue={f.label} aria-label="Frame name" onPointerDown={e => e.stopPropagation()} onFocus={e => e.target.select()}
                              onBlur={e => renameFrame(i, e.target.value)} onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); if (e.key === 'Escape') setEditFrame(null) }} />
                          : <span className="fl" onPointerDown={e => onFrameDown(e, i, 'frame')} onDoubleClick={e => { e.stopPropagation(); setEditFrame(i) }} title="Drag here or by the edges to move the frame with what's inside; double-click the name to rename">
                              {grp !== undefined && <span className="fl-folder"><FolderIcon size={15} /></span>}
                              <span className="fl-t">{f.label}{f.maybe ? ' · not in series' : ''}</span>
                              {grp && <span className="fchip" title={`This frame is the folder ${f.label}: the frames in it are its series`} style={f.color ? { background: hexA(f.color, .22), borderColor: hexA(f.color, .6) } : undefined}>
                                {grp.series_count} series · {grp.count} photo{grp.count === 1 ? '' : 's'}</span>}
                              {isFolder && linked && f.series_id != null && (
                                <span className="fchip" title={`This frame is the series ${f.label} in ${name}`} style={f.color ? { background: hexA(f.color, .22), borderColor: hexA(f.color, .6) } : undefined}>
                                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="7" width="13" height="13" rx="2" /><path d="M8 7V5a1 1 0 011-1h11a1 1 0 011 1v11a1 1 0 01-1 1h-2" /></svg>
                                  series · {seriesCount.get(f.series_id) || 0}
                                </span>
                              )}
                              {isFolder && !linked && <span className="fcount">{insideCount(f)}</span>}
                            </span>}
                        <button className="fx" aria-label={`Remove frame ${f.label}`} onPointerDown={e => e.stopPropagation()} onClick={() => deleteFrame(i)}>×</button>
                        <span className="rs" onPointerDown={e => onFrameDown(e, i, 'resize')} />
                      </div>
                    ) })}
                    {shown.map(it => {
                      const { w, h } = size(it)
                      const n = pos.get(it.id)
                      return (
                        <button key={it.id} className={'bitem' + (selSet.has(it.id) ? ' sel' : '') + (found.has(it.id) ? ' found' : '') + (it.hidden ? ' hid' : '') + (moving?.photos.has(it.id) ? ' moving' : '')} style={{ left: it.x, top: it.y, width: w, height: h, zIndex: selSet.has(it.id) ? 5 : 1, cursor: grab }}
                          onPointerDown={e => onObjDown(e, { photo: it.id })} onDoubleClick={e => { e.stopPropagation(); if (tool === 'select') photoDouble(it.id) }} aria-label={it.filename + (n ? `, position ${n}` : ', maybe')}>
                          <img src={thumbUrl(it)} alt="" draggable={false} />
                          <VersionBadge p={it} />
                          {it.hidden ? <span className="num maybe">hidden</span> : opt.boardNumbers && linked && <span className={'num' + (n ? '' : ' maybe')}>{n ? num2(n) : '?'}</span>}
                        </button>
                      )
                    })}
                    {notes.map(nt => (
                      <div key={nt.key} className={'bnote' + (shSet.has(nt.key) ? ' sel' : '') + (moving?.notes.has(nt.key) ? ' moving' : '')} style={{ left: nt.x, top: nt.y, width: nt.w, height: nt.h, background: nt.color, cursor: editing === nt.key ? 'text' : grab }}
                        onPointerDown={e => onObjDown(e, { shape: nt.key })} onDoubleClick={e => { e.stopPropagation(); if (tool === 'select') editNote(nt.key) }}>
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
                    {marquee && <div className={'marquee' + (tool === 'frame' ? ' fdraw' : '')} style={{ left: marquee.x, top: marquee.y, width: marquee.w, height: marquee.h }} />}
                  </div>
                </div>
              </div>
              <div className="btools" role="toolbar" aria-label="Board tools">
                {([['select', 'toolSelect', 'Select and move'], ['note', 'toolNote', 'Note: click where it goes'], ['arrow', 'toolArrow', 'Arrow: drag from one thing to another']] as const).map(([t, act, label]) => {
                  const title = `${label} (${keysOf(act).join(', ') || 'no key'})`
                  return <button key={t} className={tool === t ? 'on' : ''} title={title} aria-label={title} aria-pressed={tool === t} onClick={() => setTool(t)}>{TOOL_ICONS[t]}</button>
                })}
                <button className={tool === 'frame' && !asGroup ? 'on' : ''} aria-pressed={tool === 'frame' && !asGroup} title={`${isFolder && linked ? `Frame tool: draw a frame to create a series in ${name}` : 'Frame: drag to draw one (around photos and notes to put them in it), or click for the usual size'} (${keysOf('newFrame').join(', ') || 'no key'})`} aria-label={isFolder && linked ? `Frame tool: draw a frame to create a series in ${name}` : 'Frame tool'} onClick={() => setTool(t => (t === 'frame' ? 'select' : 'frame'))}>{TOOL_ICONS.frame}</button>
                {isFolder && linked && <button className={tool === 'frame' && asGroup ? 'on' : ''} aria-pressed={tool === 'frame' && asGroup} title={`New subfolder: draw a frame to create a folder in ${name} (around series' frames to put them in it)`} aria-label="New subfolder"
                  onClick={() => { const on = tool === 'frame' && asGroup; setTool(on ? 'select' : 'frame'); window.setTimeout(() => setAsGroup(!on), 0) }}><FolderIcon size={18} /></button>}
                <span className="sep" />
                <button title={`Fit everything on screen (${keysOf('fitAll').join(', ') || 'no key'})`} aria-label="Fit everything" onClick={fitAll}>{TOOL_ICONS.fit}</button>
                <div className="bt-pop">
                  <button className={frameMenu ? 'on' : ''} title={`Go to a frame (${[...keysOf('prevFrame'), ...keysOf('nextFrame')].join(' / ') || 'no key'} for the previous / next one)`} aria-label="Go to a frame" aria-expanded={frameMenu} onClick={() => setFrameMenu(m => !m)}>{TOOL_ICONS.frames}</button>
                  {frameMenu && (
                    <div className="bt-menu" role="menu">
                      <span className="caps" style={{ padding: '4px 10px' }}>Go to</span>
                      <button role="menuitem" onClick={() => { fitAll(); setFrameMenu(false) }}>Everything</button>
                      {(selPhotosHere.length > 0 || selShapes.length > 0) && <button role="menuitem" onClick={() => { fitSelection(); setFrameMenu(false) }}>The selection</button>}
                      {frames.map((f, i) => ({ f, i })).sort((a, b) => Math.round(a.f.y / 100) - Math.round(b.f.y / 100) || a.f.x - b.f.x).map(({ f, i }) => (
                        <button key={f.id ?? 'n' + i} role="menuitem" onClick={() => showFrame(i)}>{f.label || 'Frame'}</button>
                      ))}
                      {!frames.length && <span className="small muted" style={{ padding: '4px 10px' }}>No frames yet</span>}
                    </div>
                  )}
                </div>
                <span className="sep" />
                <button disabled={!undoStack.current.length} title={`Undo (${keysOf('undo').join(', ') || 'no key'})`} aria-label="Undo" onClick={undo}>{TOOL_ICONS.undo}</button>
                <button disabled={!redoStack.current.length} title={`Redo (${keysOf('redo').join(', ') || 'no key'})`} aria-label="Redo" onClick={redo}>{TOOL_ICONS.redo}</button>
              </div>
              {selFrames.length > 0 && !selPhotosHere.length && !nSh && (
                <div className="bselbar">
                  <span className="small soft">{selFrames.length > 1 ? `${selFrames.length} frames` : `Frame “${frames[selFrames[0]]?.label}”`}</span>
                  {FRAME_COLORS.map(([c, n]) => (
                    <button key={n} className={'swatch' + (c ? '' : ' none') + ((frames[selFrames[0]]?.color || '') === c ? ' on' : '')} style={{ background: c || 'transparent' }} title={n} aria-label={`Frame colour: ${n}`} onClick={() => styleFrames({ color: c })} />
                  ))}
                  <div className="seg">
                    {([['', 'Solid'], ['dashed', 'Dashed'], ['none', 'No border']] as const).map(([b, l]) => (
                      <button key={l} className={(frames[selFrames[0]]?.border || '') === b ? 'on' : ''} onClick={() => styleFrames({ border: b })}>{l}</button>
                    ))}
                  </div>
                  {selFrames.length === 1 && <button className="btn" onClick={() => setEditFrame(selFrames[0])}>Rename</button>}
                  <button className="btn" onClick={() => { const r = frames[selFrames[0]]; if (r) showRect(r) }}>Go to</button>
                  <button className="btn" onClick={() => deleteFrames(selFrames)} title={keysOf('boardDelete').join(', ')}>Delete {isFolder && linked ? 'series' : 'frame' + (selFrames.length > 1 ? 's' : '')}</button>
                </div>
              )}
              {(selPhotosHere.length > 0 || nSh > 0) && (
                <div className="bselbar">
                  <span className="small soft">{[selPhotosHere.length ? `${selPhotosHere.length} photo${selPhotosHere.length > 1 ? 's' : ''}` : '', nSh ? `${nSh} note${nSh > 1 ? 's' : ''} or arrow${nSh > 1 ? 's' : ''}` : ''].filter(Boolean).join(' · ')}</span>
                  {selNotes.length > 0 && NOTE_COLORS.map(c => <button key={c} className="swatch" style={{ background: c }} aria-label="Note color" onClick={() => colorNotes(c)} />)}
                  {selPhotosHere.length > 0 && <button className="btn" onClick={() => s.editInDarktable(selPhotosHere)}>Edit in darktable</button>}
                  {selPhotosHere.length > 0 && s.isMobile && (() => {  // the selected photos in the board's own order (as the full screen view pages through it)
                    const ids = selectionIds([...order, ...shown.filter(it => !pos.has(it.id))].map(it => it.id), selPhotosHere)
                    return <EditOnPhone ids={ids} start={ids[0]} series={isFolder ? null : sid} title={name} className="btn" />
                  })()}
                  {selPhotosHere.length > 0 && <button className="btn" title={`Hide from the library and the board (${keysOf('hide')[0] || 'no key'})`} onClick={() => toggleHidden()}>{selPhotosHere.every(id => byPhoto.get(id)?.hidden) ? 'Unhide' : 'Hide'}</button>}
                  <button className="btn" onClick={removeSel} title={keysOf('boardDelete').join(', ')}>{selPhotosHere.length ? (isFolder && linked ? 'Remove from series' : 'Remove from board') : 'Delete'}</button>
                </div>
              )}
              {(isFolder || !linked) && !s.isMobile && (
                <div className={'bhint' + (linked ? ' on' : '')}>
                  <LinkIcon off={!linked} size={14} />
                  <span>{isFolder
                    ? linked ? `Each frame is a series in ${name}. Moving a photo to another frame moves it to that series.` : 'Free board. Frames here are only visual groups; the order comes from the strip.'
                    : parent ? `Free board: inside the folder ${parent} the order of this series comes from the folder.` : 'Free board. Moving photos does not change the order; it comes from the strip.'}</span>
                </div>
              )}
              <div className="bzoom">
                <button onClick={() => zoomTo(zoom - .1)} aria-label="Zoom out" title={'Zoom out (' + keysOf('zoomOut').join(', ') + ', or Ctrl + wheel)'}>−</button>
                <button className="small muted zpct" onClick={() => zoomTo(1)} title="Back to 100%">{Math.round(zoom * 100)}%</button>
                <button onClick={() => zoomTo(zoom + .1)} aria-label="Zoom in" title={'Zoom in (' + keysOf('zoomIn').join(', ') + ', or Ctrl + wheel)'}>+</button>
              </div>
            </div>
            {!s.isMobile && <PhotoStrip photos={shown} pos={pos} sel={selSet} lanes={isFolder ? lanes : null} linked={linked} name={name}
              onPick={(it, e) => {
                const add = e.ctrlKey || e.metaKey || e.shiftKey
                s.setSel(add ? (selSet.has(it.id) ? s.sel.filter(x => x !== it.id) : [...s.sel, it.id]) : [it.id]); if (!add) setSelShapes([])
                const r = itemRect(it); showRect({ x: r.x - 150, y: r.y - 100, w: 450, h: 305 }, zoomRef.current)
              }}
              onEditAll={() => s.editInDarktable(order.map(i => i.id))} canEditAll={order.length > 0} />}
      {askNode}
    </>
  )
}


const STRIP_SORTS: [Settings['boardStripSort'], string][] = [['reading', 'Board order'], ['taken', 'Capture time'], ['name', 'Filename'], ['rating', 'Rating'], ['label', 'Colour label'], ['folder', 'Folder']]

/** Every photo on the board in a strip you can sort as you like (it doesn't jump around while you arrange the board).
 *  Click one to go to it on the board, Ctrl/Shift-click to select several; the wheel scrolls the strip.
 *  A folder's board (`lanes`): in the board's (or strip's) order the photos stand series by series, each group
 *  with the series' chip and an underline in its colour, numbered from 01 as in its lane and its frame. */
function PhotoStrip({ photos, pos, sel, lanes, linked, name, onPick, onEditAll, canEditAll }: {
  photos: Item[]; pos: Map<number, number>; sel: Set<number>; lanes: LaneInfo[] | null; linked: boolean; name: string
  onPick: (it: Item, e: React.MouseEvent) => void; onEditAll: () => void; canEditAll: boolean
}) {
  const st = useSettings()
  const list = useMemo(() => {
    const key = st.boardStripSort, dir = st.boardStripDesc ? -1 : 1
    const lane = new Map((lanes || []).map((l, i) => [l.id, i]))
    const v = (p: Item): number | string => {
      switch (key) {
        case 'taken': return p.taken || ''
        case 'name': return p.filename.toLowerCase()
        case 'rating': return p.rating
        case 'label': return p.labels.length ? Math.min(...p.labels) : 99
        case 'folder': return p.relpath.toLowerCase()
        default: return (lane.get(p.series_id ?? -1) ?? 999) * 1e6 + (pos.get(p.id) ?? 9e5)
      }
    }
    return [...photos].sort((a, b) => { const x = v(a), y = v(b); return (x < y ? -1 : x > y ? 1 : 0) * dir || a.filename.localeCompare(b.filename) })
  }, [photos, pos, lanes, st.boardStripSort, st.boardStripDesc])
  const strip = useRef<HTMLDivElement>(null)
  useEffect(() => {  // the mouse wheel scrolls the strip sideways
    const el = strip.current
    if (!el) return
    const h = (e: WheelEvent) => { if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) { e.preventDefault(); el.scrollLeft += e.deltaY } }
    el.addEventListener('wheel', h, { passive: false })
    return () => el.removeEventListener('wheel', h)
  }, [st.boardStrip])
  if (!st.boardStrip) return (
    <div className="seqstrip folded"><button className="btn ghost small" onClick={() => setSetting('boardStrip', true)}>▴ Show the photo strip ({photos.length})</button></div>
  )
  const thumb = (it: Item, group?: string) => {
    const n = pos.get(it.id)
    return (
      <button key={it.id} className={'sthumb' + (sel.has(it.id) ? ' sel' : '')} onClick={e => onPick(it, e)} aria-label={`${group ? group + ' ' : ''}${n ? num2(n) + ' ' : ''}${it.filename}`}
        title={`${it.filename} — go to it on the board (Ctrl/Shift-click to select several)`}>
        <img src={thumbUrl(it)} alt="" draggable={false} />
        <span className="tiny muted">
          {st.boardStripSort === 'rating' ? (it.rating > 0 ? '★' + it.rating : it.rating < 0 ? 'rej' : '–')
            : st.boardStripSort === 'label' ? (it.labels.length ? it.labels.map(l => <i key={l} className="ldot" style={{ background: LABELS[l].c }} />) : '–')
            : n ? num2(n) : it.series_id === null && lanes ? '–' : 'maybe'}
        </span>
      </button>
    )
  }
  // in the board's own order a folder's photos are shown series by series
  const grouped = lanes && st.boardStripSort === 'reading'
  const groups = grouped ? (st.boardStripDesc ? [...lanes!].reverse() : lanes!).map(l => ({ l, its: list.filter(p => p.series_id === l.id) })) : []
  const loose = grouped ? list.filter(p => !lanes!.some(l => l.id === p.series_id)) : []
  return (
    <div className="seqstrip">
      <div className="col" style={{ gap: 6, width: 170 }}>
        <div className="row" style={{ gap: 6 }}>
          <span className="caps grow">Photos · {photos.length}</span>
          <button className="btn ghost tiny" title="Hide the strip" aria-label="Hide the strip" onClick={() => setSetting('boardStrip', false)}>▾</button>
        </div>
        <div className="row" style={{ gap: 4 }}>
          <select className="input" style={{ flex: '1 1 auto', padding: '4px 6px' }} value={st.boardStripSort} aria-label="Sort the strip by" onChange={e => setSetting('boardStripSort', e.target.value as Settings['boardStripSort'])}>
            {STRIP_SORTS.map(([v, l]) => <option key={v} value={v}>{v === 'reading' && !linked ? 'Strip order' : l}</option>)}
          </select>
          <button className="btn" style={{ padding: '4px 8px' }} title={st.boardStripDesc ? 'Descending' : 'Ascending'} aria-label="Reverse the order" onClick={() => setSetting('boardStripDesc', !st.boardStripDesc)}>{st.boardStripDesc ? '↓' : '↑'}</button>
        </div>
      </div>
      <div className={'thumbs' + (grouped ? ' grouped' : '')} ref={strip}>
        {grouped ? <>
          {groups.map(({ l, its }) => (
            <div key={l.id} className="sgroup">
              <span className="sgchip" style={l.color ? { borderColor: hexA(l.color, .6), background: hexA(l.color, .13) } : undefined}><i style={l.color ? { background: l.color } : undefined} />{l.name}<span className="soft">{its.length}</span></span>
              <div className="sgcol"><div className="sgrow">{its.map(it => thumb(it, l.name))}</div><span className="sgline" style={l.color ? { background: l.color } : undefined} /></div>
            </div>
          ))}
          {loose.length > 0 && (
            <div className="sgroup">
              <span className="sgchip">In no series<span className="soft">{loose.length}</span></span>
              <div className="sgcol"><div className="sgrow">{loose.map(it => thumb(it))}</div><span className="sgline" /></div>
            </div>
          )}
        </> : list.map(it => thumb(it))}
      </div>
      <button className="btn" onClick={onEditAll} disabled={!canEditAll}>{lanes ? `Edit ${name} in darktable` : 'Edit whole series in darktable'}</button>
    </div>
  )
}
