// SPDX-License-Identifier: GPL-3.0-or-later
import { useEffect, useMemo, useRef, useState } from 'react'
import { api, SeriesFolder, SeriesInfo, Target } from '../api'
import { useStore } from '../store'
import { buildTree, folderSubtree, TreeEntry } from '../seriesnav'
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

/** The series as a tree: folders (to any depth; fold and unfold, remembered per folder) with their subfolders and
 *  series, and the series at the top level. Click opens a folder or a series; drag a series or a folder onto a
 *  folder to put it inside, onto the line between two rows to place it there, onto the empty part of the list to
 *  take it out to the top level; the same moves are in each row's menu. `onOpened` is called after something was
 *  opened (the phone closes its sheet). */
export function SeriesTree({ current, phone = false, onOpened }: { current: Target | null; phone?: boolean; onOpened?: () => void }) {
  const s = useStore()
  const ops = useSeriesOps()
  const tree = useMemo(() => buildTree(s.series, s.seriesFolders), [s.series, s.seriesFolders])
  const [open, setOpenRaw] = useState<number[]>(readOpen)
  const setOpen = (fn: (o: number[]) => number[]) => setOpenRaw(o => { const n = fn(o); try { localStorage.setItem('lpl.sfolders.open', JSON.stringify(n)) } catch { /* ignore */ } return n })
  const [drag, setDrag] = useState<number | null>(null)
  const [dragF, setDragF] = useState<number | null>(null)  // a folder being dragged (to another place, or into / out of a folder)
  const [over, setOver] = useState<DragOver>(null)
  const folderById = (id: number | null | undefined) => s.seriesFolders.find(f => f.id === id)

  // the folder of what is open is unfolded (and the folders above it), so you see where you are
  const curFolder = current?.kind === 'folder' ? current.id : s.series.find(x => x.id === current?.id)?.folder_id ?? null
  // (when you go there; a folder you folded yourself stays folded when the page is loaded again)
  const seen = useRef<number | null | undefined>(localStorage.getItem('lpl.sfolders.open') == null ? undefined : null)
  useEffect(() => {
    if (curFolder == null) return
    if (seen.current === null) { seen.current = curFolder; return }
    if (seen.current !== curFolder) {
      seen.current = curFolder
      const up: number[] = []
      for (let f = folderById(curFolder), n = 0; f && n < 50; f = folderById(f.parent_id), n++) up.push(f.id)
      setOpen(o => [...o, ...up.filter(id => !o.includes(id))])
    }
  }, [curFolder])

  const go = (t: Target) => { ops.open(t); onOpened?.() }
  const isOn = (t: Target) => current?.kind === t.kind && current.id === t.id
  const dragged = drag != null ? s.series.find(x => x.id === drag) : undefined
  const keyOf = (e: TreeEntry) => (e.kind === 'folder' ? 'f' + e.folder.id : 's' + e.series.id)
  /** What lies in a folder (null: the top level), as keys in order. */
  const kidsOf = (parent: number | null): string[] => {
    if (parent == null) return tree.entries.map(keyOf)
    const find = (es: TreeEntry[]): TreeEntry[] | null => { for (const e of es) if (e.kind === 'folder') { if (e.folder.id === parent) return e.kids; const r = find(e.kids); if (r) return r } return null }
    return (find(tree.entries) || []).map(keyOf)
  }
  const parentOf = (k: string): number | null => (k[0] === 'f' ? folderById(+k.slice(1))?.parent_id : s.series.find(x => x.id === +k.slice(1))?.folder_id) ?? null
  /** Put a series or a folder (key) into folder `parent` (null: the top level) in front of entry `before` (null: last). */
  const place = (k: string, parent: number | null, before: string | null) => {
    if (before === k) return
    const id = +k.slice(1)
    if (k[0] === 'f' && parent != null && folderSubtree(s.seriesFolders, id).has(parent)) { s.toast('A folder can not be moved into itself or into one of its subfolders.'); return }
    if (parentOf(k) === parent) {  // the same place again: nothing to do
      const keys = kidsOf(parent), rest = keys.filter(y => y !== k)
      rest.splice(before && rest.includes(before) ? rest.indexOf(before) : rest.length, 0, k)
      if (rest.every((y, i) => y === keys[i])) return
    }
    const bs = before && before[0] === 's' ? +before.slice(1) : null, bf = before && before[0] === 'f' ? +before.slice(1) : null
    const p = k[0] === 'f' ? api.folderMove(id, parent, bs, bf) : api.seriesMove(id, parent, bs, bf)
    p.then(() => ops.done()).catch(e => ops.fail(e))
  }
  const nextOf = (k: string, side: 'before' | 'after') => {  // the entry a drop on `k` goes in front of
    if (side === 'before') return k
    const keys = kidsOf(parentOf(k))
    return keys[keys.indexOf(k) + 1] ?? null
  }
  const drop = (o: DragOver) => {
    const k = dragF != null ? 'f' + dragF : dragged ? 's' + dragged.id : null
    setOver(null); setDrag(null); setDragF(null)
    if (!o || !k) return
    if (o.kind === 'top') { if (k[0] === 'f' || parentOf(k) != null) place(k, null, null); return }
    const t = (o.kind === 'folder' ? 'f' : 's') + o.id
    if (t === k) return
    if (o.kind === 'folder' && o.side === 'in') { if (parentOf(k) !== o.id) place(k, o.id!, null); return }
    place(k, parentOf(t), nextOf(t, o.side === 'after' ? 'after' : 'before'))
  }
  // where in a row the pointer is. On a folder: its top edge = in front of it, its bottom edge (a dragged folder) = after
  // it, else into it; on a series: the upper half = in front of it, the lower half = after it
  const sideOf = (e: React.DragEvent, o: NonNullable<DragOver>): 'before' | 'after' | 'in' | undefined => {
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect(), y = (e.clientY - r.top) / Math.max(1, r.height)
    if (o.kind === 'folder') {
      if (dragF != null) return folderSubtree(s.seriesFolders, dragF).has(o.id!) ? undefined : y < .3 ? 'before' : y > .7 ? 'after' : 'in'
      return y < .28 ? 'before' : 'in'
    }
    return y < .5 ? 'before' : 'after'
  }
  const dnd = (o: NonNullable<DragOver>) => (phone ? {} : {
    onDragOver: (e: React.DragEvent) => {
      if (drag == null && dragF == null) return
      e.preventDefault(); e.stopPropagation()
      const side = o.kind === 'top' ? undefined : sideOf(e, o)
      if (over?.kind !== o.kind || over?.id !== o.id || over?.side !== side) setOver({ ...o, side })
    },
    onDrop: (e: React.DragEvent) => { e.preventDefault(); e.stopPropagation(); const side = o.kind === 'top' ? undefined : sideOf(e, o); drop(o.kind === 'top' ? o : side ? { ...o, side } : null) },
  })
  const move = (k: string, d: -1 | 1) => { const keys = kidsOf(parentOf(k)), i = keys.indexOf(k), j = d < 0 ? i - 1 : i + 2; place(k, parentOf(k), keys[j] ?? null) }  // one place up or down among what lies next to it
  const upDown = (k: string): MenuItem[] => {
    const keys = kidsOf(parentOf(k)), i = keys.indexOf(k)
    return [...(i > 0 ? [{ label: 'Move up', onClick: () => move(k, -1) }] : []), ...(i >= 0 && i < keys.length - 1 ? [{ label: 'Move down', onClick: () => move(k, 1) }] : [])]
  }
  /** "Move to folder…": the tree of folders (indented), without where it is and, for a folder, without itself and what is in it. */
  const moveTargets = (k: string): MenuItem[] => {
    const cur = parentOf(k), not = k[0] === 'f' ? folderSubtree(s.seriesFolders, +k.slice(1)) : new Set<number>()
    return [
      ...(cur != null ? [{ label: 'Top level (out of the folder)', onClick: () => place(k, null, null) }] : []),
      ...tree.folders.filter(f => f.folder.id !== cur && !not.has(f.folder.id)).map(f => ({ label: ' '.repeat(f.depth) + f.folder.name, onClick: () => place(k, f.folder.id, null) })),
    ]
  }
  const seriesMenu = (x: SeriesInfo): MenuItem[] => {
    const targets = moveTargets('s' + x.id)
    return [
      { label: 'Rename', onClick: () => ops.renameSeries(x) },
      { label: 'Move to folder…', sub: targets.length ? targets : [{ label: 'No folder yet: make one with “+ New folder”', disabled: true }] },
      ...upDown('s' + x.id),
      { label: 'Delete series', danger: true, onClick: () => { ops.deleteSeries(x) } },
    ]
  }
  const folderMenu = (f: SeriesFolder): MenuItem[] => {
    const targets = moveTargets('f' + f.id)
    return [
      { label: `New series in ${f.name}`, onClick: () => { ops.newSeries(f) } },
      { label: `New folder in ${f.name}`, onClick: () => { ops.newFolder(f).then(id => { if (id != null) setOpen(o => (o.includes(f.id) ? o : [...o, f.id])) }) } },
      { label: 'Rename', onClick: () => ops.renameFolder(f) },
      ...(targets.length ? [{ label: 'Move to folder…', sub: targets }] : []),
      ...upDown('f' + f.id),
      { label: 'Delete folder', danger: true, onClick: () => ops.deleteFolder(f) },
    ]
  }

  const seriesRow = (x: SeriesInfo, inFolder: boolean) => {
    const t: Target = { kind: 'series', id: x.id }
    return (
      <div key={'s' + x.id} className={'st-row' + (inFolder ? ' in' : '') + (isOn(t) ? ' on' : '') + (over?.kind === 'series' && over.id === x.id && drag !== x.id ? (over.side === 'before' ? ' line-b' : over.side === 'after' ? ' line-a' : ' over') : '') + (drag === x.id ? ' dragging' : '')}
        draggable={!phone} onDragStart={e => { e.stopPropagation(); setDrag(x.id); e.dataTransfer.setData('text/plain', 's' + x.id); e.dataTransfer.effectAllowed = 'move' }}
        onDragEnd={() => { setDrag(null); setOver(null) }} {...dnd({ kind: 'series', id: x.id })}>
        <button type="button" className="st-main" onClick={() => go(t)} aria-current={isOn(t) ? 'true' : undefined} title={x.name}>
          <span className="st-chip" style={x.color ? { background: x.color } : undefined} />
          <span className="st-name">{x.name}</span>
          <span className="st-n">{x.count}</span>
        </button>
        <MenuButton label={`More for ${x.name}: rename, move to folder, delete`} items={seriesMenu(x)} className="smore st-more" />
      </div>
    )
  }
  const folderRow = (e: Extract<TreeEntry, { kind: 'folder' }>): React.ReactNode => {
    const { folder: f, kids } = e
    const t: Target = { kind: 'folder', id: f.id }
    const isOpen = open.includes(f.id)
    const overMe = over?.kind === 'folder' && over.id === f.id
    return (
      <div key={'f' + f.id} data-depth={e.depth} className={'st-folder' + (e.depth ? ' sub' : '') + (dragF === f.id ? ' dragging' : '') + (overMe && over!.side === 'after' ? ' line-a' : '')}>
        <div className={'st-row folder' + (isOn(t) ? ' on' : '') + (overMe && dragF !== f.id ? (over!.side === 'before' ? ' line-b' : over!.side === 'in' ? ' over' : '') : '')} {...dnd({ kind: 'folder', id: f.id })}
          draggable={!phone} onDragStart={ev => { ev.stopPropagation(); setDragF(f.id); ev.dataTransfer.setData('text/plain', 'f' + f.id); ev.dataTransfer.effectAllowed = 'move' }}
          onDragEnd={() => { setDragF(null); setOver(null) }}>
          <button type="button" className="st-chev" aria-expanded={isOpen} aria-label={`${isOpen ? 'Fold' : 'Unfold'} ${f.name}`}
            onClick={() => setOpen(o => (o.includes(f.id) ? o.filter(x => x !== f.id) : [...o, f.id]))}><Chevron open={isOpen} /></button>
          <button type="button" className="st-main" onClick={() => { go(t); setOpen(o => (o.includes(f.id) ? o : [...o, f.id])) }} aria-current={isOn(t) ? 'true' : undefined} title={f.name}>
            <span className="st-ficon" style={e.depth && f.color ? { color: f.color } : undefined}><FolderIcon /></span>
            <span className="st-fname">{f.name}</span>
            <span className="st-sub">{f.total_series ?? f.series_count} series</span>
            <span className="st-n">{f.total_count ?? f.count}</span>
          </button>
          <MenuButton label={`More for ${f.name}: new series, new folder, rename, move, delete folder`} items={folderMenu(f)} className="smore st-more" />
        </div>
        {isOpen && (
          <div className="st-kids">
            {kids.map(k => (k.kind === 'series' ? seriesRow(k.series, true) : folderRow(k)))}
            <button type="button" className="st-add" onClick={() => ops.newSeries(f)}>+ New series in {f.name}</button>
          </div>
        )}
      </div>
    )
  }

  return (
    <div className={'stree' + (phone ? ' phone' : '')} {...dnd({ kind: 'top' })}>
      {!phone && <span className="caps" style={{ padding: '0 8px 6px' }}>Series</span>}
      {tree.entries.map(e => (e.kind === 'series' ? seriesRow(e.series, false) : folderRow(e)))}
      {!tree.entries.length && <span className="small muted" style={{ padding: 8 }}>No series yet.</span>}
      <div className={'st-new' + (over?.kind === 'top' && ((dragged && dragged.folder_id != null) || dragF != null) ? ' over' : '')}>
        <button type="button" className={'btn dashed' + (phone ? ' big' : '')} onClick={() => ops.newSeries(null, phone ? [] : s.sel)}>+ New series{!phone && s.sel.length ? ` from ${s.sel.length} selected` : ''}</button>
        <button type="button" className={'btn dashed' + (phone ? ' big' : '')} onClick={() => ops.newFolder().then(id => { if (id != null) onOpened?.() })}>+ New folder</button>
        {!phone && <button type="button" className="btn dashed" onClick={ops.importMiro}>Import from Miro…</button>}
      </div>
      <div className="st-hint">{phone
        ? 'A folder holds several series, and folders. “⋯” next to a series or a folder moves it into a folder or out again.'
        : 'Drag a series or a folder onto a folder to put it inside, back to this list to take it out. Drag a folder or a series up or down to put it where you want it.'}</div>
      {ops.node}
    </div>
  )
}
