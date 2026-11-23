// SPDX-License-Identifier: GPL-3.0-or-later
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, GroupInfo, Lane, Photo, SeriesFolder, StripData, Target, thumbUrl } from '../api'
import { useStore } from '../store'
import { getSettings } from '../settings'
import { actionFor, keysOf, typingIn, useShortcutsVersion } from '../shortcuts'
import { buildTree, dropBefore, folderSubtree, movedLanes, num2, numAtSlot, renumber, slotAt } from '../seriesnav'
import { bumpSeries, MenuButton, MenuItem, SeriesOps, seriesRev, useSeriesRev } from '../seriesops'
import { useBackLayer } from '../back'
import { selectionIds } from '../devices'
import { EditOnPhone } from './PhoneEdit'
import { FolderIcon } from './SeriesTree'
import { VersionBadge } from './Versions'
import { byDay, tray, useTray } from '../tray'

const hexA = (hex: string, a: number) => { const n = parseInt(hex.slice(1), 16); return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})` }
const LinkIcon = () => <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M10 14a4.5 4.5 0 006.4 0l3-3a4.5 4.5 0 00-6.4-6.4l-1.2 1.2" /><path d="M14 10a4.5 4.5 0 00-6.4 0l-3 3a4.5 4.5 0 006.4 6.4l1.2-1.2" /></svg>
const Grip = () => <svg width="14" height="16" viewBox="0 0 14 16" fill="currentColor" aria-hidden="true"><circle cx="4" cy="3" r="1.3" /><circle cx="10" cy="3" r="1.3" /><circle cx="4" cy="8" r="1.3" /><circle cx="10" cy="8" r="1.3" /><circle cx="4" cy="13" r="1.3" /><circle cx="10" cy="13" r="1.3" /></svg>

export interface StripInfo { photos: number; hidden: number; lanes: number
  /** which lane each photo of the strip is in (its label): the photo panel dims those and says where they are */
  members?: Map<number, string> }

/** Dragging photos in from outside the strip (the photo panel next to it): the panel starts the drag and feeds it
 *  the pointer; the strip shows where they would land and does the drop. */
export interface ExtDrag { begin: (ids: number[], label: string, from: HTMLElement | null, x: number, y: number, touch: boolean) => void
  move: (x: number, y: number) => void; end: (commit: boolean) => void }

const Pencil = () => <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4 20h4L19 9l-4-4L4 16v4z" /><path d="M13.5 6.5l4 4" /></svg>

/** The name of a lane or a group heading, renamed in place: double-click (or the pencil, or F2), Enter keeps it, Esc does not. */
function NameEdit({ value, label, onDone }: { value: string; label: string; onDone: (name: string | null) => void }) {
  const done = useRef(false)
  const finish = (v: string | null) => { if (!done.current) { done.current = true; onDone(v) } }
  return <input className="lane-edit display" defaultValue={value} aria-label={label} autoFocus onFocus={e => e.currentTarget.select()}
    onKeyDown={e => { e.stopPropagation(); if (e.key === 'Enter') finish(e.currentTarget.value); else if (e.key === 'Escape') finish(null) }}
    onBlur={e => finish(e.currentTarget.value)} onPointerDown={e => e.stopPropagation()} />
}

type LaneProps = {
  lane: Lane; shown: Photo[]; selKey: string; folder: boolean; linked: boolean; canReorder: boolean; phone: boolean; menu: MenuItem[] | null; depth?: number
  editing: boolean; canRename: boolean; act: LaneActs; trayN: number
}
/** What a lane's header can do (one object for all lanes, so a lane is drawn again only when its own things change). */
type LaneActs = { edit: (key: string | null) => void; rename: (key: string, name: string | null) => void; trayAdd: (key: string) => void }
/** One lane: its header and its photos in a row that scrolls sideways. Drawn again only when its own photos,
 *  numbers or selection change; everything that happens while dragging is done on the page directly (StripView). */
const LaneRow = memo(function LaneRow({ lane, shown, selKey, folder, linked, canReorder, phone, menu, depth = 0, editing, canRename, act, trayN }: LaneProps) {
  const sel = useMemo(() => new Set(selKey ? selKey.split(',').map(Number) : []), [selKey])
  const c = lane.color
  const n = shown.length
  return (
    <section className={'lane' + (lane.maybe ? ' maybe' : '')} aria-label={lane.label} data-lane={lane.key}
      data-skey={folder && lane.series ? 's' + lane.series.id : undefined} data-parent={folder ? lane.folder_id ?? undefined : undefined} data-depth={depth || undefined}
      style={{ ...(c ? { borderColor: hexA(c, .55), background: hexA(c, .07) } : {}), ...(depth ? { marginLeft: depth * (phone ? 10 : 22) } : {}) }}>
      <div className="lane-h">
        {canReorder && <button type="button" className="lane-grip" data-grip={lane.key} aria-label={`${phone ? 'Hold and drag' : 'Drag'} to reorder the series ${lane.label}`}><Grip /></button>}
        <span className="lane-chip" style={c ? { background: c } : undefined} />
        {editing
          ? <NameEdit value={lane.label} label={`New name for ${lane.label}`} onDone={v => act.rename(lane.key, v)} />
          : <span className="lane-name display" onDoubleClick={canRename ? () => act.edit(lane.key) : undefined} title={canRename ? 'Double-click to rename' : undefined}>{lane.label}</span>}
        {canRename && !editing && <button type="button" className="lane-ren" aria-label={`Rename ${lane.label}`} title="Rename (or double-click the name, or F2)" onClick={() => act.edit(lane.key)}><Pencil /></button>}
        <span className="lane-n">{phone ? n : `${n} photo${n === 1 ? '' : 's'}`}{lane.maybe ? ' · not in series' : ''}</span>
        <div className="grow" />
        {!phone && linked && lane.frame && <span className="lane-frame"><LinkIcon />frame on the board</span>}
        {trayN > 0 && !lane.maybe && <button type="button" className="btn lane-tray" onClick={() => act.trayAdd(lane.key)}>+ {trayN} here</button>}
        {menu && <MenuButton label={folder ? `More for ${lane.label}: rename, colour, move to another folder, delete` : `More for ${lane.label}`} items={menu} className="smore lane-more" />}
      </div>
      <div className="srow" data-row={lane.key}>
        {shown.map(p => (
          <button key={p.id} type="button" className={'sth' + (sel.has(p.id) ? ' sel' : '') + (p.hidden ? ' hid' : '')} data-id={p.id} aria-label={`${num2(p.num) || (p.hidden ? 'hidden' : 'not in the order')} ${p.filename}`} aria-pressed={sel.has(p.id)}>
            <span className="box"><img src={thumbUrl(p)} alt="" draggable={false} loading="lazy" />
              <VersionBadge p={p} />
              {p.hidden ? <span className="num maybe">hidden</span> : <span className={'num' + (p.num ? '' : ' maybe')}>{num2(p.num) || '?'}</span>}</span>
            <span className="cap">{p.filename}</span>
          </button>
        ))}
        {!n && <span className="srow-empty">Drop photos here</span>}
      </div>
    </section>
  )
})

type DragState = {
  kind: 'photos' | 'lane'
  ids: number[]               // photos being moved, in display order
  from: string                // lane the grabbed photo (or the lane header) came from
  label: string; num: number | null
  x: number; y: number
  drop: { lane: string; slot: number; into?: number | null } | null
  touch: boolean
  /** photos from outside the strip (the photo panel): added where they are dropped, or to a new series */
  ext?: boolean
}

/** Strip mode: one lane per series of a folder (or per frame of a single linked series), photos as numbered
 *  thumbnails in order. Drag a photo to another place or lane, several at once with Ctrl/Shift, a lane by its
 *  handle; on a touch screen press and hold first. While dragging nothing is drawn again by React: the lifted
 *  photo, the drop mark and the note next to the pointer are moved on the page directly. */
export function StripView({ target, name, folder, ops, onInfo, onNewSeries, ext, onAdded }: {
  target: Target; name: string; folder: SeriesFolder | null; ops: SeriesOps
  onInfo?: (i: StripInfo) => void; onNewSeries?: (ids?: number[]) => void
  /** filled by the strip: lets the photo panel drag photos in */
  ext?: React.MutableRefObject<ExtDrag | null>
  /** photos came in from outside (the panel shows them as placed) */
  onAdded?: (ids: number[]) => void
}) {
  const s = useStore()
  const rev = useSeriesRev()
  const phone = s.isMobile
  const [data, setData] = useState<StripData | null>(null)
  // group headings that are folded, remembered on this device
  const [folded, setFolded] = useState<number[]>(() => { try { const v = JSON.parse(localStorage.getItem('lpl.strip.folded') || '[]'); return Array.isArray(v) ? v : [] } catch { return [] } })
  const fold = (id: number) => setFolded(o => { const n = o.includes(id) ? o.filter(x => x !== id) : [...o, id]; try { localStorage.setItem('lpl.strip.folded', JSON.stringify(n)) } catch { /* ignore */ } return n })
  const [lifted, setLifted] = useState(false)   // a drag is on (the phone's Back lets go of it)
  const root = useRef<HTMLDivElement>(null)
  const ghost = useRef<HTMLDivElement>(null)
  const mark = useRef<HTMLDivElement>(null)
  const tip = useRef<HTMLDivElement>(null)
  const drag = useRef<DragState | null>(null)
  const raf = useRef(0)
  const anchor = useRef<number | null>(null)
  const req = useRef(0)
  const isFolder = target.kind === 'folder'
  const showHidden = !!s.filters.show_hidden
  const [editKey, setEditKey] = useState<string | null>(null)
  const trayIds = useTray()
  const [traySel, setTraySel] = useState<number[]>([])
  const [trayPhotos, setTrayPhotos] = useState<Photo[]>([])
  const trayOn = phone && trayIds.length > 0

  const load = useCallback(() => {
    const id = ++req.current
    api.tstrip(target).then(r => { if (id === req.current) setData(r) }).catch(e => { if (!/not found/.test(e.message)) s.toast(e.message) })  // not found: it was just deleted, the view is about to change
  }, [target.kind, target.id])
  useEffect(() => { setData(null); load() }, [load])
  // something changed elsewhere: load again (not for a change this strip made itself: it has the answer already)
  const ownRev = useRef(0)
  const told = () => { ownRev.current = seriesRev() + 1; bumpSeries() }  // the board, if it is there, loads again when it is shown
  useEffect(() => { if (rev && rev !== ownRev.current) load() }, [rev])

  const lanes = data?.lanes || []
  const shownOf = useMemo(() => new Map(lanes.map(l => [l.key, showHidden ? l.photos : l.photos.filter(p => !p.hidden)])), [lanes, showHidden])
  const all = useMemo(() => lanes.flatMap(l => shownOf.get(l.key) || []), [lanes, shownOf])
  const byId = useMemo(() => new Map(all.map(p => [p.id, p])), [all])
  const laneOf = useMemo(() => { const m = new Map<number, string>(); for (const l of lanes) for (const p of l.photos) m.set(p.id, l.key); return m }, [lanes])
  const selHere = s.sel.filter(id => byId.has(id))
  const state = useRef({ lanes, shownOf, all, byId, laneOf, sel: s.sel, selHere })
  state.current = { lanes, shownOf, all, byId, laneOf, sel: s.sel, selHere }

  useEffect(() => {
    if (!data) return
    const members = new Map<number, string>()
    for (const l of lanes) for (const p of l.photos) members.set(p.id, l.label)
    onInfo?.({ photos: lanes.reduce((n, l) => n + l.photos.filter(p => !p.hidden).length, 0), hidden: lanes.reduce((n, l) => n + l.photos.filter(p => p.hidden).length, 0), lanes: lanes.length, members })
  }, [data])

  // ---- what the photos do
  const pageList = () => state.current.all
  const openPhoto = (id: number) => {
    const how = getSettings().boardPhotoDouble
    if (how === 'darktable') { s.editInDarktable([id]); return }
    const list = pageList(), k = list.findIndex(p => p.id === id)
    if ((how === 'fullscreen' || phone) && k >= 0) s.setFullscreen({ list, index: k })
    else s.setViewer(id)
  }
  const pick = (id: number, e: { ctrlKey?: boolean; metaKey?: boolean; shiftKey?: boolean }) => {
    const st = state.current
    if (e.shiftKey && anchor.current != null && st.byId.has(anchor.current)) {
      const ids = st.all.map(p => p.id), a = ids.indexOf(anchor.current), b = ids.indexOf(id)
      s.setSel(Array.from(new Set([...st.sel, ...ids.slice(Math.min(a, b), Math.max(a, b) + 1)])))
      return
    }
    anchor.current = id
    if (e.ctrlKey || e.metaKey) s.setSel(st.sel.includes(id) ? st.sel.filter(x => x !== id) : [...st.sel, id])
    else s.setSel([id])
  }
  const apply = async (ids: number[], laneKey: string, before: number | null) => {
    const st = state.current
    const lane = st.lanes.find(l => l.key === laneKey)
    if (!lane || !data) return
    const id = ++req.current
    setData({ ...data, lanes: renumber(movedLanes(st.lanes, ids, laneKey, before), isFolder) })  // at once; the server's answer follows
    try {
      const r = await api.tstripMove(target, ids, lane, before)
      if (id === req.current) setData(r)
      told()
      if (isFolder) { s.reloadSeries(); s.reloadTags() }
    } catch (e: any) { s.toast(e.message); load() }
  }
  const removeSel = async () => {
    const st = state.current
    if (!st.selHere.length) return
    const groups = new Map<number, number[]>()
    for (const id of st.selHere) {
      const sid = isFolder ? st.lanes.find(l => l.key === st.laneOf.get(id))?.series?.id : target.id
      if (sid != null) groups.set(sid, [...(groups.get(sid) || []), id])
    }
    try {
      for (const [sid, ids] of groups) await api.seriesRemove(sid, ids)
      s.toast(`${st.selHere.length} photo${st.selHere.length > 1 ? 's' : ''} taken out of ${groups.size > 1 ? 'their series' : 'the series'} (files untouched)`)
      s.setSel(st.sel.filter(id => !st.selHere.includes(id)))
      s.reloadSeries(); s.reloadTags(); load(); told()
    } catch (e: any) { s.toast(e.message) }
  }
  const toggleHidden = async () => {
    const st = state.current
    if (!st.selHere.length) return
    await s.hidePhotos(st.selHere, !st.selHere.every(id => st.byId.get(id)?.hidden))
    load(); told()
  }

  // ---- keys: the same as on the board where they mean the same
  useShortcutsVersion()
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && drag.current) { e.preventDefault(); endDrag(false); return }
      if (s.viewer != null || s.fullscreen || typingIn(e.target) || !root.current || root.current.offsetParent === null) return
      const st = state.current
      if (e.key === 'F2' && !e.ctrlKey && !e.metaKey && !e.altKey && st.selHere.length) {
        const k = st.laneOf.get(st.selHere[0]), l = k ? st.lanes.find(x => x.key === k) : undefined
        if (l && canRename(l)) { e.preventDefault(); setEditKey(l.key) }
        return
      }
      const a = actionFor(e, ['board', 'photos'])
      if (a === 'selectAll') { e.preventDefault(); s.setSel(st.all.map(p => p.id)) }
      else if (a === 'fullscreen') {
        const k = st.all.findIndex(p => p.id === st.selHere[0])
        if (k >= 0) { e.preventDefault(); s.setFullscreen({ list: st.all, index: k }) }
      }
      else if (a === 'hide') { e.preventDefault(); toggleHidden() }
      else if (a === 'boardDelete' && st.selHere.length) { e.preventDefault(); removeSel() }
    }
    window.addEventListener('keydown', down)
    return () => window.removeEventListener('keydown', down)
  })

  // ---- dragging, done on the page itself
  const rows = () => Array.from(root.current?.querySelectorAll<HTMLElement>('.srow') || [])
  const srows = () => Array.from(root.current?.querySelectorAll<HTMLElement>('[data-skey]') || [])  // a folder's lanes and group headings, top to bottom
  const sections = () => Array.from(root.current?.querySelectorAll<HTMLElement>('section.lane') || [])
  const cellOf = (row: HTMLElement) => {
    const a = row.querySelector<HTMLElement>('.sth')
    const gap = parseFloat(getComputedStyle(row).columnGap || '8') || 8
    return { w: (a ? a.offsetWidth : (phone ? 108 : 96)) + gap, gap }
  }
  const frame = () => {
    raf.current = 0
    const d = drag.current, r = root.current
    if (!d || !r) return
    if (ghost.current) ghost.current.style.transform = `translate(${d.x}px, ${d.y}px)`
    const rr = r.getBoundingClientRect()
    // near the top or bottom edge the page of lanes moves; near a lane's left or right edge that lane moves
    const edge = phone ? 56 : 44
    if (d.y < rr.top + edge) r.scrollTop -= Math.ceil((rr.top + edge - d.y) / 4)
    else if (d.y > rr.bottom - edge) r.scrollTop += Math.ceil((d.y - rr.bottom + edge) / 4)
    let again = d.y < rr.top + edge || d.y > rr.bottom - edge
    const m = mark.current
    if (d.kind === 'lane' && isFolder) {
      // a lane or a group heading among the rows of the tree: in front of the row the pointer is above (in that row's
      // folder); on the middle of a group heading: into that folder, at its end; under everything: last in the open folder
      const els = srows()
      let slot = els.length, top = 0, into: number | null = null, left = 0
      for (let i = 0; i < els.length; i++) {
        const b = els[i].getBoundingClientRect(), key = els[i].dataset.skey!
        if (key[0] === 'f' && key !== d.from && d.y > b.top + b.height * .25 && d.y < b.bottom - b.height * .25) { into = +key.slice(1); slot = i; top = b.bottom + 1; left = b.left - rr.left + 22; break }
        if (d.y < b.top + b.height / 2) { slot = i; top = b.top - 6; left = b.left - rr.left; break }
        top = b.bottom + 4
      }
      d.drop = { lane: '', slot, into }
      if (m) { if (m.className !== 'strip-mark h') m.className = 'strip-mark h'; m.style.transform = `translate(${rr.left + Math.max(12, left)}px, ${top}px)`; m.style.width = rr.width - 12 - Math.max(12, left) + 'px'; m.style.height = ''; m.style.display = 'block' }
    } else if (d.kind === 'lane') {
      const secs = sections()
      let slot = secs.length, top = 0
      for (let i = 0; i < secs.length; i++) { const b = secs[i].getBoundingClientRect(); if (d.y < b.top + b.height / 2) { slot = i; top = b.top - 6; break } top = b.bottom + 4 }
      const lim = isFolder ? secs.length : state.current.lanes.filter(l => l.frame && !l.maybe).length  // a series' frames come before "Outside" and the maybes
      if (slot > lim && lim > 0) { slot = lim; top = secs[lim - 1].getBoundingClientRect().bottom + 4 }
      d.drop = { lane: '', slot }
      if (m) { if (m.className !== 'strip-mark h') m.className = 'strip-mark h'; m.style.transform = `translate(${rr.left + 12}px, ${top}px)`; m.style.width = rr.width - 24 + 'px'; m.style.height = ''; m.style.display = 'block' }
    } else {
      const nz = d.ext ? root.current?.querySelector<HTMLElement>('.strip-new') : null
      if (nz) {
        const b = nz.getBoundingClientRect()
        const inZone = d.x >= b.left && d.x <= b.right && d.y >= b.top - 8 && d.y <= b.bottom + 8
        if (inZone !== !!nz.dataset.hot) { if (inZone) nz.dataset.hot = '1'; else delete nz.dataset.hot }
        if (inZone) {
          d.drop = { lane: '__new', slot: 0 }
          if (m) m.style.display = 'none'
          if (tip.current) {
            const text = `${d.ids.length > 1 ? d.ids.length + ' photos' : d.label} → a new series`
            if (tip.current.textContent !== text) tip.current.textContent = text
            tip.current.style.transform = `translate(${Math.min(window.innerWidth - tip.current.offsetWidth - 8, d.x + 22)}px, ${d.y - 10}px)`
          }
          if (again) raf.current = requestAnimationFrame(frame)
          return
        }
      }
      let hit: HTMLElement | null = null, best = 1e9
      for (const row of rows()) {  // the lane the pointer is in, else the nearest one
        const b = row.parentElement!.getBoundingClientRect()
        const dist = d.y < b.top ? b.top - d.y : d.y > b.bottom ? d.y - b.bottom : 0
        if (dist < best) { best = dist; hit = row }
      }
      if (hit) {
        const b = hit.getBoundingClientRect()
        if (hit.scrollWidth > hit.clientWidth) {
          if (d.x < b.left + edge && hit.scrollLeft > 0) { hit.scrollLeft -= Math.ceil((b.left + edge - d.x) / 3); again = true }
          else if (d.x > b.right - edge && hit.scrollLeft < hit.scrollWidth - hit.clientWidth) { hit.scrollLeft += Math.ceil((d.x - b.right + edge) / 3); again = true }
        }
        const key = hit.dataset.row!
        const shown = state.current.shownOf.get(key) || []
        const { w, gap } = cellOf(hit)
        const slot = slotAt(d.x - b.left + hit.scrollLeft, w, shown.length)
        d.drop = { lane: key, slot }
        const x = b.left - hit.scrollLeft + slot * w - gap / 2 - 2
        if (m) {
          const box = hit.querySelector<HTMLElement>('.box')
          if (m.className !== 'strip-mark') m.className = 'strip-mark'
          m.style.width = ''; m.style.height = (box ? box.offsetHeight : 64) + 'px'
          m.style.transform = `translate(${Math.max(b.left - 3, Math.min(b.right - 1, x))}px, ${b.top}px)`
          m.style.display = 'block'
        }
        if (tip.current) {
          const lane = state.current.lanes.find(l => l.key === key)
          const to = lane?.maybe ? null : numAtSlot(shown, d.ids, slot, isFolder ? 0 : (state.current.lanes.slice(0, state.current.lanes.findIndex(l => l.key === key)).reduce((n, l) => n + (l.maybe ? 0 : (state.current.shownOf.get(l.key) || []).filter(p => !p.hidden && !d.ids.includes(p.id)).length), 0)))
          const where = key !== d.from && lane ? ` in ${lane.label}` : ''
          const text = d.ids.length > 1 ? `${d.ids.length} photos → ${to ? num2(to) : lane?.label}${to ? where : ''}`
            : `${d.label} · ${d.num ? num2(d.num) + ' → ' : '→ '}${to ? num2(to) : lane?.label}${to ? where : ''}`
          if (tip.current.textContent !== text) tip.current.textContent = text
          tip.current.style.transform = `translate(${Math.min(window.innerWidth - tip.current.offsetWidth - 8, d.x + (d.touch ? -40 : 22))}px, ${d.y + (d.touch ? -96 : -10)}px)`
        }
      }
    }
    if (again) raf.current = requestAnimationFrame(frame)
  }
  const tick = () => { if (!raf.current) raf.current = requestAnimationFrame(frame) }

  const startDrag = (kind: 'photos' | 'lane', id: number | string, x: number, y: number, touch: boolean) => {
    const st = state.current
    const g = ghost.current
    if (kind === 'lane') {
      const grp = isFolder && String(id)[0] === 'f' ? (data?.groups || []).find(x => 'f' + x.id === id) : undefined  // a group heading: the subfolder moves
      const lane = st.lanes.find(l => l.key === id)
      if ((!lane && !grp) || !g) return
      const key = grp ? 'f' + grp.id : lane!.key, label = grp ? grp.name : lane!.label
      drag.current = { kind, ids: [], from: key, label, num: null, x, y, drop: null, touch }
      g.className = 'strip-ghost lane'; g.textContent = label
      root.current?.querySelector(grp ? `.strip-group[data-skey="${key}"]` : `section.lane[data-lane="${key}"]`)?.setAttribute('data-moving', '1')
    } else {
      const pid = id as number
      const p = st.byId.get(pid)
      if (!p || !g) return
      let ids = st.sel.includes(pid) ? selectionIds(st.all.map(q => q.id), st.selHere) : [pid]
      if (!st.sel.includes(pid)) { s.setSel([pid]); anchor.current = pid }
      ids = ids.filter(i => st.byId.has(i))
      drag.current = { kind, ids, from: st.laneOf.get(pid) || '', label: p.filename, num: p.num ?? null, x, y, drop: null, touch }
      const src = root.current?.querySelector<HTMLElement>(`.sth[data-id="${pid}"] .box`)
      g.className = 'strip-ghost'
      g.innerHTML = ''
      if (src) {
        const c = src.cloneNode(true) as HTMLElement
        c.style.width = src.offsetWidth + 'px'; c.style.height = src.offsetHeight + 'px'
        g.appendChild(c)
      }
      if (ids.length > 1) { const b = document.createElement('span'); b.className = 'count'; b.textContent = String(ids.length); g.appendChild(b) }
      // the empty place it came from (an attribute of its own: React rewrites the class when the selection changes)
      for (const i of ids) root.current?.querySelector(`.sth[data-id="${i}"]`)?.setAttribute('data-moving', '1')
      if (tip.current) { tip.current.textContent = ''; tip.current.style.display = 'block' }
    }
    g.style.display = 'block'
    root.current?.setAttribute('data-dragging', '1')
    setLifted(true)
    if (touch) try { navigator.vibrate?.(12) } catch { /* ignore */ }
    tick()
  }
  const endDrag = (commit: boolean) => {
    cancelAnimationFrame(raf.current)
    if (commit && drag.current) frame()  // where the pointer is now, not where it was at the last drawn frame
    const d = drag.current
    drag.current = null
    cancelAnimationFrame(raf.current); raf.current = 0
    for (const el of [ghost.current, mark.current, tip.current]) if (el) el.style.display = 'none'
    root.current?.removeAttribute('data-dragging')
    root.current?.querySelectorAll('[data-moving]').forEach(el => el.removeAttribute('data-moving'))
    root.current?.querySelectorAll<HTMLElement>('[data-hot]').forEach(el => { delete el.dataset.hot })
    setLifted(false)
    if (!d || !commit || !d.drop) return
    const st = state.current
    if (d.ext) { dropIn(d.ids, d.drop.lane, d.drop.slot); return }
    if (d.kind === 'lane' && isFolder) {  // a series or a subfolder to another place in the tree (the server moves frames and tags)
      const els = srows(), key = d.from, id = +key.slice(1)
      const el = d.drop.into == null ? els[d.drop.slot] : undefined
      const parent = d.drop.into != null ? d.drop.into : el ? +(el.dataset.parent || target.id) : target.id
      const before = el ? el.dataset.skey! : null
      if (before === key) return
      const own = els.findIndex(x => x.dataset.skey === key), ownParent = own >= 0 ? +(els[own].dataset.parent || target.id) : null
      if (key[0] === 'f' && folderSubtree(s.seriesFolders, id).has(parent)) return  // (not into itself)
      if (key[0] === 's' && d.drop.into == null && ownParent === parent && d.drop.slot === own + 1) return  // dropped where it is
      const bs = before && before[0] === 's' ? +before.slice(1) : null, bf = before && before[0] === 'f' ? +before.slice(1) : null
      const p = key[0] === 'f' ? api.folderMove(id, parent, bs, bf) : api.seriesMove(id, parent, bs, bf)
      p.then(() => { s.reloadSeries(); if (ownParent !== parent) s.reloadTags(); load(); told() }).catch(e => { s.toast(e.message); load() })
      return
    }
    if (d.kind === 'lane') {
      const keys = st.lanes.map(l => l.key), i = keys.indexOf(d.from)
      const rest = keys.filter(k => k !== d.from)
      const at = d.drop.slot > i ? d.drop.slot - 1 : d.drop.slot
      rest.splice(at, 0, d.from)
      if (rest.every((k, j) => k === keys[j]) || !data) return
      const order = rest.map(k => st.lanes.find(l => l.key === k)!)
      if (!folder) {  // the frames of a single series: the server moves the frame on the board to the matching place
        const next = order.find((l, j) => j > at && l.frame && !l.maybe)
        const id = ++req.current
        setData({ ...data, lanes: renumber(order, false) })
        api.seriesLanes(target.id, d.from, next ? next.key : null).then(r => { if (id === req.current) setData(r); told() }).catch(e => { s.toast(e.message); load() })
        return
      }
      setData({ ...data, lanes: order })
      api.seriesOrder(folder.id, order.map(l => l.series!.id)).then(() => { s.reloadSeries(); load(); told() }).catch(e => { s.toast(e.message); load() })
      return
    }
    const shown = (st.shownOf.get(d.drop.lane) || []).map(p => p.id)
    const same = d.ids.every(i => st.laneOf.get(i) === d.drop!.lane)
    const { before, noop } = dropBefore(shown, d.ids, d.drop.slot, same)
    if (!noop) apply(d.ids, d.drop.lane, before)
  }
  // photos from outside the strip (the photo panel, the phone's tray) into a lane at a place, or into a new series
  const dropIn = async (ids: number[], laneKey: string, slot: number | null) => {
    if (laneKey === '__new') { onNewSeries?.(ids); return }
    const st = state.current
    const lane = st.lanes.find(l => l.key === laneKey)
    if (!lane || !ids.length) return
    const shown = (st.shownOf.get(laneKey) || []).map(p => p.id)
    const before = slot == null ? null : dropBefore(shown, ids, slot, false).before
    const id = ++req.current
    try {
      const r = await api.tstripMove(target, ids, lane, before)
      if (id === req.current) setData(r)
      told(); s.reloadSeries(); s.reloadTags()
      onAdded?.(ids)
      s.toast(`${ids.length} photo${ids.length > 1 ? 's' : ''} added to ${lane.label}`)
    } catch (e: any) { s.toast(e.message); load() }
  }
  const startExternal = (ids: number[], label: string, from: HTMLElement | null, x: number, y: number, touch: boolean) => {
    const g = ghost.current
    if (!g || !ids.length || drag.current) return
    drag.current = { kind: 'photos', ids, from: '', label, num: null, x, y, drop: null, touch, ext: true }
    g.className = 'strip-ghost'
    g.innerHTML = ''
    if (from) {
      const c = from.cloneNode(true) as HTMLElement
      c.style.width = Math.min(from.offsetWidth, 120) + 'px'; c.style.height = Math.min(from.offsetHeight, 80) + 'px'
      g.appendChild(c)
    }
    if (ids.length > 1) { const b = document.createElement('span'); b.className = 'count'; b.textContent = String(ids.length); g.appendChild(b) }
    if (tip.current) { tip.current.textContent = ''; tip.current.style.display = 'block' }
    g.style.display = 'block'
    root.current?.setAttribute('data-dragging', '1')
    setLifted(true)
    tick()
  }
  if (ext) ext.current = {
    begin: startExternal,
    move: (x, y) => { if (drag.current?.ext) { drag.current.x = x; drag.current.y = y; tick() } },
    end: commit => { if (drag.current?.ext) endDrag(commit) },
  }
  useEffect(() => () => { if (ext) ext.current = null }, [])

  // ---- renaming in place: a series (in a folder), a frame (of a single series), the series itself (its one lane)
  const canRename = (l: Lane) => !!l.series || (!isFolder && (l.frame || l.key === 'all'))
  const rename = async (key: string, raw: string | null) => {
    setEditKey(null)
    const v = (raw || '').trim()
    if (key[0] === 'g') {  // a group heading: a subfolder
      const f = s.seriesFolders.find(x => x.id === +key.slice(1))
      if (!f || !v || v === f.name) return
      try { await api.folderRename(f.id, v); s.reloadSeries(); s.reloadTags(); load(); told() } catch (e: any) { s.toast(e.message) }
      return
    }
    const l = state.current.lanes.find(x => x.key === key)
    if (!l || !v || v === l.label || !data) return
    setData({ ...data, lanes: data.lanes.map(x => x.key === key ? { ...x, label: v } : x) })  // at once
    try {
      if (l.series || l.key === 'all') {
        await api.seriesRename(l.series ? l.series.id : target.id, v)
        s.reloadSeries(); s.reloadTags(); load()
      } else {
        const r = await api.frameRename(target.id, +key.slice(1), v)
        setData(r)
      }
      told()
    } catch (e: any) { s.toast(e.message); load() }
  }
  // the phone's tray: the photos picked in it (none picked: all of them) go to the end of a lane
  const trayPick = () => (traySel.length ? traySel.filter(i => trayIds.includes(i)) : trayIds)
  const trayAdd = async (key: string) => {
    const ids = trayPick()
    await dropIn(ids, key, null)
    tray.remove(ids); setTraySel([])
  }
  const acts = useRef<LaneActs>({ edit: () => {}, rename: () => {}, trayAdd: () => {} })
  acts.current.edit = setEditKey; acts.current.rename = rename; acts.current.trayAdd = trayAdd
  const stableActs = useMemo<LaneActs>(() => ({ edit: k => acts.current.edit(k), rename: (k, v) => acts.current.rename(k, v), trayAdd: k => acts.current.trayAdd(k) }), [])
  useEffect(() => { if (!trayOn) { setTrayPhotos([]); return } api.get(trayIds).then(r => setTrayPhotos(r.items)).catch(() => {}) }, [trayOn, trayIds.join(',')])
  const trayPerDay = async () => {
    if (!folder) return
    const ids = trayPick()
    const days = byDay(trayPhotos.filter(p => ids.includes(p.id)))
    let made = 0
    for (const [day, group] of days) {
      const base = day ? new Date(day + 'T12:00:00').toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : 'No date'
      for (let k = 1; k <= 6; k++) {
        try { await api.seriesCreate(k === 1 ? base : `${base} (${k})`, group, folder.id); made++; break }
        catch (e: any) { if (k === 6 || !/exist|taken|already|unique/i.test(e.message)) { s.toast(e.message); break } }
      }
    }
    tray.remove(ids); setTraySel([])
    s.reloadSeries(); s.reloadTags(); load(); told()
    s.toast(`${made} new series in ${folder.name}, one per day`)
  }
  useBackLayer(lifted && phone, () => endDrag(false))

  // mouse and pen: press, move a little, and the photo (or the lane) comes along
  const press = useRef<{ kind: 'photos' | 'lane'; id: number | string; x: number; y: number; e: { ctrlKey: boolean; metaKey: boolean; shiftKey: boolean } } | null>(null)
  const lastClick = useRef<{ id: number; t: number } | null>(null)
  const targetOfEvent = (el: EventTarget | null): { kind: 'photos' | 'lane'; id: number | string } | null => {
    const t = el as HTMLElement | null
    const th = t?.closest?.<HTMLElement>('.sth')
    if (th) return { kind: 'photos', id: +th.dataset.id! }
    const grip = t?.closest?.<HTMLElement>('.lane-grip')
    return grip ? { kind: 'lane', id: grip.dataset.grip! } : null  // (a group heading's grip names its folder: "f<id>")
  }
  const onPointerDown = (e: React.PointerEvent) => {
    if (e.pointerType === 'touch' || e.button !== 0) return
    const t = targetOfEvent(e.target)
    if (!t) { if (!(e.target as HTMLElement).closest('button, input, .smenu, .strip-tray')) s.setSel(s.sel.filter(id => !byId.has(id))); return }
    e.preventDefault()
    press.current = { ...t, x: e.clientX, y: e.clientY, e: { ctrlKey: e.ctrlKey, metaKey: e.metaKey, shiftKey: e.shiftKey } }
    const move = (ev: PointerEvent) => {
      const p = press.current
      if (!p) return
      if (!drag.current) {
        if (Math.hypot(ev.clientX - p.x, ev.clientY - p.y) < 5) return
        startDrag(p.kind, p.id, ev.clientX, ev.clientY, false)
        if (!drag.current) return
      }
      drag.current.x = ev.clientX; drag.current.y = ev.clientY
      tick()
    }
    const up = (ev: PointerEvent) => {
      window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); window.removeEventListener('pointercancel', up)
      const p = press.current
      press.current = null
      if (drag.current) { endDrag(ev.type === 'pointerup'); return }
      if (p?.kind === 'photos' && ev.type === 'pointerup') {
        const id = p.id as number, now = Date.now(), lc = lastClick.current
        if (lc && lc.id === id && now - lc.t < 400 && !p.e.ctrlKey && !p.e.shiftKey && !p.e.metaKey) { lastClick.current = null; openPhoto(id); return }
        lastClick.current = { id, t: now }
        pick(id, p.e)
      }
    }
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up); window.addEventListener('pointercancel', up)
  }

  // touch: press and hold lifts the photo (a short move before that is the lane scrolling, as usual)
  useEffect(() => {
    const el = root.current
    if (!el) return
    let timer = 0
    let t0: { kind: 'photos' | 'lane'; id: number | string; x: number; y: number; moved: boolean } | null = null
    let lastTap: { id: number; t: number } | null = null
    const start = (e: TouchEvent) => {
      if (e.touches.length !== 1) { window.clearTimeout(timer); t0 = null; if (drag.current) endDrag(false); return }
      const t = targetOfEvent(e.target)
      if (!t) return
      const p = e.touches[0]
      t0 = { ...t, x: p.clientX, y: p.clientY, moved: false }
      window.clearTimeout(timer)
      timer = window.setTimeout(() => { if (t0 && !t0.moved) startDrag(t0.kind, t0.id, t0.x, t0.y, true) }, 350)
    }
    const move = (e: TouchEvent) => {
      const p = e.touches[0]
      if (drag.current) {  // lifted: the finger moves the photo, not the page
        if (e.cancelable) e.preventDefault()
        drag.current.x = p.clientX; drag.current.y = p.clientY
        tick()
        return
      }
      if (t0 && Math.hypot(p.clientX - t0.x, p.clientY - t0.y) > 8) { t0.moved = true; window.clearTimeout(timer) }
    }
    const end = (e: TouchEvent) => {
      window.clearTimeout(timer)
      const was = t0
      t0 = null
      if (drag.current) { if (e.cancelable) e.preventDefault(); endDrag(e.type === 'touchend'); return }
      if (was && !was.moved && was.kind === 'photos' && e.type === 'touchend') {
        if (e.cancelable) e.preventDefault()  // no click after it
        const id = was.id as number, now = Date.now()
        if (lastTap && lastTap.id === id && now - lastTap.t < 350) { lastTap = null; openPhoto(id); return }
        lastTap = { id, t: now }
        const st = state.current
        s.setSel(st.sel.length === 1 && st.sel[0] === id ? [] : [id])
      }
    }
    const menu = (e: Event) => { if ((e.target as HTMLElement).closest?.('.sth, .lane-grip')) e.preventDefault() }  // no "save image" menu on a long press
    // the wheel moves a lane sideways while it can still go that way; at its end the page of lanes scrolls on
    const wheel = (e: WheelEvent) => {
      if (e.ctrlKey || e.metaKey || e.shiftKey || Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return
      const row = (e.target as HTMLElement).closest?.<HTMLElement>('.srow')
      if (!row || row.scrollWidth <= row.clientWidth) return
      const max = row.scrollWidth - row.clientWidth
      if ((e.deltaY < 0 && row.scrollLeft <= 0) || (e.deltaY > 0 && row.scrollLeft >= max - 1)) return
      e.preventDefault()
      row.scrollLeft += e.deltaMode === 1 ? e.deltaY * 32 : e.deltaY
    }
    el.addEventListener('touchstart', start, { passive: true })
    el.addEventListener('touchmove', move, { passive: false })
    el.addEventListener('touchend', end, { passive: false })
    el.addEventListener('touchcancel', end, { passive: false })
    el.addEventListener('contextmenu', menu)
    el.addEventListener('wheel', wheel, { passive: false })
    return () => {
      window.clearTimeout(timer)
      el.removeEventListener('touchstart', start); el.removeEventListener('touchmove', move); el.removeEventListener('touchend', end)
      el.removeEventListener('touchcancel', end); el.removeEventListener('contextmenu', menu); el.removeEventListener('wheel', wheel)
    }
  }, [data != null])
  useEffect(() => () => cancelAnimationFrame(raf.current), [])

  const laneMenu = (l: Lane): MenuItem[] | null => {
    if (!l.series) return canRename(l) && l.key !== 'all' ? [{ label: 'Rename frame', onClick: () => setEditKey(l.key) }] : null
    const x = s.series.find(y => y.id === l.series!.id)
    if (!x) return null
    const others = buildTree(s.series, s.seriesFolders).folders.filter(f => f.folder.id !== x.folder_id)
    return [
      { label: 'Open this series alone', onClick: () => ops.open({ kind: 'series', id: x.id }) },
      { label: 'Rename', onClick: () => setEditKey(l.key) },
      { label: 'Colour', swatches: { value: l.color, pick: c => ops.colorSeries(x, c) } },
      { label: 'Move to folder…', sub: [{ label: 'Top level (out of the folder)', onClick: () => ops.moveSeries(x, null) }, ...others.map(f => ({ label: '\u2003'.repeat(f.depth) + f.folder.name, onClick: () => ops.moveSeries(x, f.folder.id) }))] },
      { label: 'Delete series', danger: true, onClick: () => { ops.deleteSeries(x) } },
    ]
  }
  const groupMenu = (g: GroupInfo): MenuItem[] => {
    const f = s.seriesFolders.find(x => x.id === g.id)
    if (!f) return []
    return [
      { label: 'Open this folder alone', onClick: () => ops.open({ kind: 'folder', id: f.id }) },
      { label: `New series in ${f.name}`, onClick: () => { ops.newSeries(f) } },
      { label: `New folder in ${f.name}`, onClick: () => { ops.newFolder(f) } },
      { label: 'Rename', onClick: () => setEditKey('g' + g.id) },
      { label: 'Colour', swatches: { value: g.color, pick: c => { api.folderColor(f.id, c).then(() => ops.done()).catch(e => ops.fail(e)) } } },
      { label: 'Delete folder', danger: true, onClick: () => { ops.deleteFolder(f) } },
    ]
  }
  const linked = !!data?.linked
  const groups = data?.groups || []
  const sel = new Set(s.sel)
  const where = isFolder ? name : 'this series'
  return (
    <div className={'strip' + (phone ? ' phone' : '')} ref={root} onPointerDown={onPointerDown}>
      {!data && <div className="muted" style={{ padding: 20 }}>Loading…</div>}
      {(() => {
        const row = (l: Lane, depth = 0) => {
          const shown = shownOf.get(l.key) || []
          return <LaneRow key={l.key} lane={l} shown={shown} selKey={shown.filter(p => sel.has(p.id)).map(p => p.id).join(',')} folder={isFolder} linked={linked} depth={depth}
            canReorder={isFolder ? lanes.length > 1 || groups.length > 0 : linked && l.frame && !l.maybe && lanes.filter(x => x.frame && !x.maybe).length > 1} phone={phone} menu={laneMenu(l)}
            editing={editKey === l.key} canRename={canRename(l)} act={stableActs} trayN={trayOn ? trayPick().length : 0} />
        }
        if (!isFolder || !data?.tree || !groups.length) return lanes.map(l => row(l))
        // a folder with subfolders: each subfolder's lanes together under a heading that folds, indented per depth
        const out: React.ReactNode[] = []
        let hide = -1  // rows deeper than this are inside a folded group
        for (const e of data.tree) {
          if (hide >= 0 && e.depth > hide) continue
          hide = -1
          if (e.series != null) { const l = lanes.find(x => x.series?.id === e.series); if (l) out.push(row(l, e.depth)); continue }
          const g = groups.find(x => x.id === e.folder)
          if (!g) continue
          const isFolded = folded.includes(g.id)
          if (isFolded) hide = e.depth
          out.push(
            <div key={'g' + g.id} className={'strip-group' + (isFolded ? ' folded' : '')} role="group" aria-label={`Folder ${g.name}`} data-skey={'f' + g.id} data-parent={g.parent_id ?? undefined} data-depth={e.depth}
              style={{ marginLeft: e.depth * (phone ? 10 : 22), ...(g.color ? { borderColor: hexA(g.color, .6), background: hexA(g.color, .1) } : {}) }}>
              <button type="button" className="lane-grip" data-grip={'f' + g.id} aria-label={`${phone ? 'Hold and drag' : 'Drag'} to move the folder ${g.name}`}><Grip /></button>
              <button type="button" className="sg-fold" aria-expanded={!isFolded} aria-label={`${isFolded ? 'Unfold' : 'Fold'} ${g.name}`} onClick={() => fold(g.id)}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ transform: isFolded ? 'none' : 'rotate(90deg)', transition: 'transform .12s' }}><path d="M9 6l6 6-6 6" /></svg>
              </button>
              <span className="sg-icon" style={g.color ? { color: g.color } : undefined}><FolderIcon size={16} /></span>
              {editKey === 'g' + g.id
                ? <NameEdit value={g.name} label={`New name for the folder ${g.name}`} onDone={v => rename('g' + g.id, v)} />
                : <span className="sg-name display" onDoubleClick={() => setEditKey('g' + g.id)} title="Double-click to rename">{g.name}</span>}
              <span className="lane-n">{phone ? `${g.series_count} · ${g.count}` : `${g.series_count} series · ${g.count} photo${g.count === 1 ? '' : 's'}`}</span>
              <div className="grow" />
              <MenuButton label={`More for the folder ${g.name}: open, rename, colour, new series, delete`} items={groupMenu(g)} className="smore lane-more" />
            </div>)
        }
        return out
      })()}
      {data && isFolder && <button type="button" className="strip-new" onClick={() => onNewSeries?.()}>{lifted ? `Drop here: a new series in ${name}` : `+ New series in ${name}`}</button>}
      {data && !isFolder && !all.length && <div className="muted" style={{ padding: '6px 2px' }}>No photos in this series yet. “+ Add photos” brings them in.</div>}
      {data && !(phone && (selHere.length > 0 || trayOn)) && (
        <div className="strip-hint">
          {phone
            ? <><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" aria-hidden="true"><circle cx="12" cy="12" r="8.5" /><circle cx="12" cy="12" r="3" fill="currentColor" stroke="none" /></svg>
                <span>Press and hold a photo, then drag it to a new place or another lane.{isFolder && lanes.length > 1 ? ' Hold a lane handle to move the series.' : linked && lanes.filter(l => l.frame && !l.maybe).length > 1 ? ' Hold a frame handle to move the frame.' : ''}</span></>
            : isFolder
              ? <>Drag a photo to change its place, or drop it in another lane to move it to that series. Drag a lane by its handle to reorder the series. {linked ? 'The board follows.' : 'The board is not linked: it stays as it is.'}</>
              : <>Drag a photo to change its place{lanes.length > 1 ? ', or drop it in another lane to move it to that frame' : ''}. {linked && lanes.filter(l => l.frame && !l.maybe).length > 1 ? 'Drag a frame by its handle to change the reading order. ' : ''}Ctrl or Shift selects several. {linked ? 'The board follows.' : data.folder ? `The board of ${data.folder.name} follows if it is linked.` : 'The board is not linked: it stays as it is.'}</>}
        </div>
      )}
      {selHere.length > 0 && !lifted && (
        <div className="strip-sel">
          <span className="small soft">{selHere.length} photo{selHere.length > 1 ? 's' : ''}</span>
          <button className="btn" onClick={() => s.editInDarktable(selectionIds(all.map(p => p.id), selHere))}>Edit in darktable</button>
          {phone && (() => { const ids = selectionIds(all.map(p => p.id), selHere); return <EditOnPhone ids={ids} start={ids[0]} series={isFolder ? null : target.id} title={name} className="btn" /> })()}
          <button className="btn" title={`Hide from the library, the board and the strip (${keysOf('hide')[0] || 'no key'})`} onClick={toggleHidden}>{selHere.every(id => byId.get(id)?.hidden) ? 'Unhide' : 'Hide'}</button>
          <button className="btn" title={`Take out of ${where}; the files stay (${keysOf('boardDelete').join(', ') || 'no key'})`} onClick={removeSel}>Remove from series</button>
        </div>
      )}
      {trayOn && !lifted && (
        <div className="strip-tray" role="region" aria-label="Tray">
          <div className="row" style={{ gap: 8 }}>
            <span className="caps">Tray</span>
            <span className="small muted grow">{traySel.length ? `${traySel.length} of ${trayIds.length} picked` : `${trayIds.length} photo${trayIds.length > 1 ? 's' : ''}: tap “+ here” on a lane`}</span>
            <button className="btn" onClick={() => { tray.clear(); setTraySel([]) }}>Clear</button>
          </div>
          <div className="tray-row">
            {trayIds.map(id => { const p = trayPhotos.find(x => x.id === id); const on = traySel.includes(id)
              return <button key={id} type="button" className={'tray-th' + (on ? ' sel' : '')} aria-pressed={on} aria-label={p ? p.filename : 'photo'}
                onClick={() => setTraySel(on ? traySel.filter(x => x !== id) : [...traySel, id])}>{p && <img src={thumbUrl(p)} alt="" />}</button> })}
          </div>
          {isFolder && <div className="row wrap" style={{ gap: 6 }}>
            <button className="btn" onClick={() => onNewSeries?.(trayPick())}>{traySel.length ? 'New series from these' : 'New series from all'}</button>
            <button className="btn" onClick={trayPerDay}>One series per day</button>
          </div>}
        </div>
      )}
      <div className="strip-ghost" ref={ghost} style={{ display: 'none' }} aria-hidden="true" />
      <div className="strip-mark" ref={mark} style={{ display: 'none' }} aria-hidden="true" />
      <div className="strip-tip" ref={tip} style={{ display: 'none' }} role="status" />
    </div>
  )
}
