// SPDX-License-Identifier: GPL-3.0-or-later
import { useEffect, useMemo, useRef, useState } from 'react'
import { api, SeriesFolder, SeriesInfo, Target } from '../api'
import { useStore } from '../store'
import { buildTree, TreeEntry } from '../seriesnav'
import { MenuButton, MenuItem, useSeriesOps } from '../seriesops'

const Chevron = ({ open }: { open: boolean }) => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ transform: open ? 'rotate(90deg)' : 'none', transition: 'transform .12s' }}><path d="M9 6l6 6-6 6" /></svg>
)
export const FolderIcon = ({ size = 16 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinejoin="round" aria-hidden="true"><path d="M3 6.5A1.5 1.5 0 014.5 5h4.2l2 2.2h8.8A1.5 1.5 0 0121 8.7v9.8a1.5 1.5 0 01-1.5 1.5h-15A1.5 1.5 0 013 18.5z" /></svg>
)

export const LinkIcon = ({ off = false, size = 15 }: { off?: boolean; size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M10 14a4.5 4.5 0 006.4 0l3-3a4.5 4.5 0 00-6.4-6.4l-1.2 1.2" /><path d="M14 10a4.5 4.5 0 00-6.4 0l-3 3a4.5 4.5 0 006.4 6.4l1.2-1.2" />
    {off && <path d="M4 4l16 16" />}
  </svg>
)

// which folders are unfolded, remembered on this device
function readOpen(): number[] {
  try { const v = JSON.parse(localStorage.getItem('lpl.sfolders.open') || '[]'); return Array.isArray(v) ? v.filter(x => typeof x === 'number') : [] } catch { return [] }
}

type DragOver = { kind: 'folder' | 'series' | 'top'; id?: number; side?: 'before' | 'after' | 'in' } | null

/** The series as a tree: folders (fold and unfold, remembered) with their series, then the series at the top
 *  level. Click opens a folder or a series; drag a series onto a folder to put it inside, onto another series
 *  to place it in front of that one, onto the empty part of the list to take it out of its folder; the same
 *  moves are in each row's menu. `onOpened` is called after something was opened (the phone closes its sheet). */
export function SeriesTree({ current, phone = false, onOpened }: { current: Target | null; phone?: boolean; onOpened?: () => void }) {
  const s = useStore()
  const ops = useSeriesOps()
  const tree = useMemo(() => buildTree(s.series, s.seriesFolders), [s.series, s.seriesFolders])
  const [open, setOpenRaw] = useState<number[]>(readOpen)
  const setOpen = (fn: (o: number[]) => number[]) => setOpenRaw(o => { const n = fn(o); try { localStorage.setItem('lpl.sfolders.open', JSON.stringify(n)) } catch { /* ignore */ } return n })
  const [drag, setDrag] = useState<number | null>(null)
  const [dragF, setDragF] = useState<number | null>(null)  // a folder being dragged (to a new place among the top-level entries)
  const [over, setOver] = useState<DragOver>(null)

  // the folder of what is open is unfolded, so you see where you are
  const curFolder = current?.kind === 'folder' ? current.id : s.series.find(x => x.id === current?.id)?.folder_id ?? null
  // (when you go there; a folder you folded yourself stays folded when the page is loaded again)
  const seen = useRef<number | null | undefined>(localStorage.getItem('lpl.sfolders.open') == null ? undefined : null)
  useEffect(() => {
    if (curFolder == null) return
    if (seen.current === null) { seen.current = curFolder; return }
    if (seen.current !== curFolder) { seen.current = curFolder; setOpen(o => (o.includes(curFolder) ? o : [...o, curFolder])) }
  }, [curFolder])

  const go = (t: Target) => { ops.open(t); onOpened?.() }
  const isOn = (t: Target) => current?.kind === t.kind && current.id === t.id
  const dragged = drag != null ? s.series.find(x => x.id === drag) : undefined
  const keyOf = (e: TreeEntry) => (e.kind === 'folder' ? 'f' + e.folder.id : 's' + e.series.id)
  const asEntry = (k: string) => (k[0] === 'f' ? { folder: +k.slice(1) } : { series: +k.slice(1) })
  const keys = tree.entries.map(keyOf)
  /** Put a top-level entry (or a series that comes out of a folder) in front of entry `before` (null: last). */
  const placeTop = (k: string, before: string | null) => {
    const x = k[0] === 's' ? s.series.find(y => y.id === +k.slice(1)) : undefined
    if (x && x.folder_id != null) {  // out of a folder, to that place
      api.seriesMove(x.id, null, before && before[0] === 's' ? +before.slice(1) : null, before && before[0] === 'f' ? +before.slice(1) : null).then(() => ops.done()).catch(e => ops.fail(e))
      return
    }
    const rest = keys.filter(y => y !== k)
    rest.splice(before ? rest.indexOf(before) : rest.length, 0, k)
    if (rest.every((y, i) => y === keys[i])) return
    api.seriesTopOrder(rest.map(asEntry)).then(() => ops.done()).catch(e => ops.fail(e))
  }
  const nextOf = (k: string, side: 'before' | 'after') => {  // the entry a drop on `k` goes in front of
    if (side === 'before') return k
    const i = keys.indexOf(k)
    return keys[i + 1] ?? null
  }
  const drop = (o: DragOver) => {
    const f = dragF
    setOver(null); setDrag(null); setDragF(null)
    if (!o) return
    if (f != null) {
      if (o.kind === 'top') placeTop('f' + f, null)
      else if (o.side && o.id != null) { const t = (o.kind === 'folder' ? 'f' : 's') + o.id; if (t !== 'f' + f) placeTop('f' + f, nextOf(t, o.side === 'after' ? 'after' : 'before')) }
      return
    }
    if (!dragged) return
    if (o.kind === 'folder') {
      if (o.side === 'before') placeTop('s' + dragged.id, 'f' + o.id)
      else if (dragged.folder_id !== o.id) ops.moveSeries(dragged, o.id!)
    }
    else if (o.kind === 'top') { if (dragged.folder_id != null) ops.moveSeries(dragged, null) }
    else if (o.id !== dragged.id) {
      const t = s.series.find(x => x.id === o.id)
      if (!t) return
      if ((t.folder_id ?? null) === null) placeTop('s' + dragged.id, nextOf('s' + t.id, o.side === 'after' ? 'after' : 'before'))
      else ops.moveSeries(dragged, t.folder_id ?? null, t.id)
    }
  }
  // where in a row the pointer is: a dragged folder goes before (upper half) or after (lower half) the row; a dragged series
  // goes before a folder at its very top, else into it
  const sideOf = (e: React.DragEvent, o: NonNullable<DragOver>): 'before' | 'after' | 'in' | undefined => {
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect(), y = (e.clientY - r.top) / Math.max(1, r.height)
    if (dragF != null) return o.kind === 'series' && s.series.find(x => x.id === o.id)?.folder_id != null ? undefined : y < .5 ? 'before' : 'after'
    if (o.kind === 'folder') return y < .28 ? 'before' : 'in'
    return y < .5 ? 'before' : 'after'
  }
  const dnd = (o: NonNullable<DragOver>) => (phone ? {} : {
    onDragOver: (e: React.DragEvent) => {
      if (drag == null && dragF == null) return
      e.preventDefault(); e.stopPropagation()
      const side = o.kind === 'top' ? undefined : sideOf(e, o)
      if (over?.kind !== o.kind || over?.id !== o.id || over?.side !== side) setOver({ ...o, side })
    },
    onDrop: (e: React.DragEvent) => { e.preventDefault(); e.stopPropagation(); drop(o.kind === 'top' ? o : { ...o, side: sideOf(e, o) }) },
  })
  const move = (k: string, d: -1 | 1) => { const i = keys.indexOf(k), j = d < 0 ? i - 1 : i + 2; placeTop(k, keys[j] ?? null) }  // one place up or down among the top-level entries

  const moveTargets = (x: SeriesInfo): MenuItem[] => [
    ...(x.folder_id != null ? [{ label: 'Top level (out of the folder)', onClick: () => ops.moveSeries(x, null) }] : []),
    ...tree.folders.filter(f => f.folder.id !== x.folder_id).map(f => ({ label: f.folder.name, onClick: () => ops.moveSeries(x, f.folder.id) })),
  ]
  const seriesMenu = (x: SeriesInfo, list: SeriesInfo[]): MenuItem[] => {
    const i = list.findIndex(y => y.id === x.id)
    const targets = moveTargets(x)
    return [
      { label: 'Rename', onClick: () => ops.renameSeries(x) },
      { label: 'Move to folder…', sub: targets.length ? targets : [{ label: 'No folder yet: make one with “+ New folder”', disabled: true }] },
      ...(x.folder_id == null ? [
        ...(keys.indexOf('s' + x.id) > 0 ? [{ label: 'Move up', onClick: () => move('s' + x.id, -1) }] : []),
        ...(keys.indexOf('s' + x.id) < keys.length - 1 ? [{ label: 'Move down', onClick: () => move('s' + x.id, 1) }] : []),
      ] : [
        ...(i > 0 ? [{ label: 'Move up', onClick: () => ops.moveSeries(x, x.folder_id ?? null, list[i - 1].id) }] : []),
        ...(i >= 0 && i < list.length - 1 ? [{ label: 'Move down', onClick: () => ops.moveSeries(x, x.folder_id ?? null, list[i + 2]?.id ?? null) }] : []),
      ]),
      { label: 'Delete series', danger: true, onClick: () => { ops.deleteSeries(x) } },
    ]
  }
  const folderMenu = (f: SeriesFolder): MenuItem[] => [
    { label: `New series in ${f.name}`, onClick: () => { ops.newSeries(f) } },
    { label: 'Rename', onClick: () => ops.renameFolder(f) },
    ...(keys.indexOf('f' + f.id) > 0 ? [{ label: 'Move up', onClick: () => move('f' + f.id, -1) }] : []),
    ...(keys.indexOf('f' + f.id) < keys.length - 1 ? [{ label: 'Move down', onClick: () => move('f' + f.id, 1) }] : []),
    { label: 'Delete folder', danger: true, onClick: () => ops.deleteFolder(f) },
  ]

  const seriesRow = (x: SeriesInfo, list: SeriesInfo[], inFolder: boolean) => {
    const t: Target = { kind: 'series', id: x.id }
    return (
      <div key={x.id} className={'st-row' + (inFolder ? ' in' : '') + (isOn(t) ? ' on' : '') + (over?.kind === 'series' && over.id === x.id && drag !== x.id ? (over.side === 'before' ? ' line-b' : over.side === 'after' ? ' line-a' : ' over') : '') + (drag === x.id ? ' dragging' : '')}
        draggable={!phone} onDragStart={e => { setDrag(x.id); e.dataTransfer.setData('text/plain', 's' + x.id); e.dataTransfer.effectAllowed = 'move' }}
        onDragEnd={() => { setDrag(null); setOver(null) }} {...dnd({ kind: 'series', id: x.id })}>
        <button type="button" className="st-main" onClick={() => go(t)} aria-current={isOn(t) ? 'true' : undefined} title={x.name}>
          <span className="st-chip" style={x.color ? { background: x.color } : undefined} />
          <span className="st-name">{x.name}</span>
          <span className="st-n">{x.count}</span>
        </button>
        <MenuButton label={`More for ${x.name}: rename, move to folder, delete`} items={seriesMenu(x, list)} className="smore st-more" />
      </div>
    )
  }

  return (
    <div className={'stree' + (phone ? ' phone' : '')} {...dnd({ kind: 'top' })}>
      {!phone && <span className="caps" style={{ padding: '0 8px 6px' }}>Series</span>}
      {tree.entries.map(e => {
        if (e.kind === 'series') return seriesRow(e.series, tree.top, false)
        const { folder: f, series } = e
        const t: Target = { kind: 'folder', id: f.id }
        const isOpen = open.includes(f.id)
        return (
          <div key={'f' + f.id} className={'st-folder' + (dragF === f.id ? ' dragging' : '') + (dragF != null && over?.kind === 'folder' && over.id === f.id ? (over.side === 'after' ? ' line-a' : ' line-b') : '')}>
            <div className={'st-row folder' + (isOn(t) ? ' on' : '') + (over?.kind === 'folder' && over.id === f.id && dragF == null ? (over.side === 'before' ? ' line-b' : ' over') : '')} {...dnd({ kind: 'folder', id: f.id })}
              draggable={!phone} onDragStart={ev => { ev.stopPropagation(); setDragF(f.id); ev.dataTransfer.setData('text/plain', 'f' + f.id); ev.dataTransfer.effectAllowed = 'move' }}
              onDragEnd={() => { setDragF(null); setOver(null) }}>
              <button type="button" className="st-chev" aria-expanded={isOpen} aria-label={`${isOpen ? 'Fold' : 'Unfold'} ${f.name}`}
                onClick={() => setOpen(o => (o.includes(f.id) ? o.filter(x => x !== f.id) : [...o, f.id]))}><Chevron open={isOpen} /></button>
              <button type="button" className="st-main" onClick={() => { go(t); setOpen(o => (o.includes(f.id) ? o : [...o, f.id])) }} aria-current={isOn(t) ? 'true' : undefined} title={f.name}>
                <span className="st-ficon"><FolderIcon /></span>
                <span className="st-fname">{f.name}</span>
                <span className="st-sub">{f.series_count} series</span>
                <span className="st-n">{f.count}</span>
              </button>
              <MenuButton label={`More for ${f.name}: new series, rename, delete folder`} items={folderMenu(f)} className="smore st-more" />
            </div>
            {isOpen && (
              <div className="st-kids">
                {series.map(x => seriesRow(x, series, true))}
                <button type="button" className="st-add" onClick={() => ops.newSeries(f)}>+ New series in {f.name}</button>
              </div>
            )}
          </div>
        )
      })}
      {!tree.entries.length && <span className="small muted" style={{ padding: 8 }}>No series yet.</span>}
      <div className={'st-new' + (over?.kind === 'top' && (dragged?.folder_id != null || dragF != null) ? ' over' : '')}>
        <button type="button" className={'btn dashed' + (phone ? ' big' : '')} onClick={() => ops.newSeries(null, phone ? [] : s.sel)}>+ New series{!phone && s.sel.length ? ` from ${s.sel.length} selected` : ''}</button>
        <button type="button" className={'btn dashed' + (phone ? ' big' : '')} onClick={() => ops.newFolder().then(id => { if (id != null) onOpened?.() })}>+ New folder</button>
      </div>
      <div className="st-hint">{phone
        ? 'A folder holds several series. “⋯” next to a series moves it into a folder or out again.'
        : 'Drag a series onto a folder to put it inside, back to this list to take it out. Drag a folder or a series up or down to put it where you want it.'}</div>
      {ops.node}
    </div>
  )
}
