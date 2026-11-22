// SPDX-License-Identifier: GPL-3.0-or-later
// What can be done to series and their folders (new, rename, move, delete), shared by the tree, the Series
// view's header and the strip's lanes; and a small popup menu.
import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react'
import { api, SeriesFolder, SeriesInfo, Target } from './api'
import { useStore } from './store'
import { useAsk } from './components/Ask'
import { keyOf } from './seriesnav'

// lane and frame colours (the board's frame colours, in the order new series get them)
export const SERIES_COLORS: [string, string][] = [['#5b9be6', 'Blue'], ['#e5c547', 'Yellow'], ['#f0a35a', 'Orange'], ['#5cc08a', 'Green'], ['#e07bb0', 'Pink'], ['#a07be0', 'Purple'], ['#e5636a', 'Red'], ['#8a8a92', 'Grey']]

// something about the series changed (from the tree, a menu, another part of the page): the open board or strip loads again
let rev = 0
const subs = new Set<() => void>()
export function bumpSeries() { rev++; subs.forEach(f => f()) }
export const seriesRev = () => rev
/** The server's undo token of the last "Take the order from the board": the board, when it loads again, makes it one undo step. */
let pendingUndo: { key: string; token: number } | null = null
export const setPendingUndo = (t: { kind: string; id: number }, token: number) => { pendingUndo = { key: t.kind + t.id, token } }
export const takePendingUndo = (t: { kind: string; id: number }) => { const p = pendingUndo && pendingUndo.key === t.kind + t.id ? pendingUndo.token : undefined; pendingUndo = null; return p }
export const useSeriesRev = () => useSyncExternalStore(cb => { subs.add(cb); return () => { subs.delete(cb) } }, () => rev)

export function useSeriesOps() {
  const s = useStore()
  const [node, ask] = useAsk()
  const done = () => { s.reloadSeries(); s.reloadTags(); bumpSeries() }
  const fail = (e: any) => { s.toast(e?.message || String(e)) }
  const open = (t: Target) => { s.setBoardSeries(keyOf(t)); if (s.view !== 'board') s.setView('board') }
  const folderOf = (x: SeriesInfo) => s.seriesFolders.find(f => f.id === x.folder_id)

  const newSeries = async (folder: SeriesFolder | null, ids: number[] = [], show = true) => {
    const name = await ask({ title: folder ? `New series in ${folder.name}` : 'New series', field: 'Name of the new series', ok: 'Create series',
      text: ids.length ? `It starts with the ${ids.length} selected photo${ids.length > 1 ? 's' : ''}.` : undefined })
    if (!name) return null
    try {
      const r = await api.seriesCreate(name, ids, folder?.id)
      done()
      if (show && !folder) open({ kind: 'series', id: r.id })
      return r.id
    } catch (e) { fail(e); return null }
  }
  const newFolder = async () => {
    const name = await ask({ title: 'New folder', field: 'Name of the folder', ok: 'Create folder',
      text: 'A folder holds several series and has one board for all of them.' })
    if (!name) return null
    try { const r = await api.folderCreate(name); done(); open({ kind: 'folder', id: r.id }); return r.id } catch (e) { fail(e); return null }
  }
  const renameSeries = async (x: SeriesInfo) => {
    const name = await ask({ title: `Rename “${x.name}”`, field: 'Name of the series', initial: x.name, ok: 'Rename', text: 'Its tag on the photos is renamed too.' })
    if (!name || name === x.name) return
    try { await api.seriesRename(x.id, name); done() } catch (e) { fail(e) }
  }
  const renameFolder = async (f: SeriesFolder) => {
    const name = await ask({ title: `Rename “${f.name}”`, field: 'Name of the folder', initial: f.name, ok: 'Rename',
      text: `The tags of its ${f.count} photo${f.count === 1 ? '' : 's'} are renamed too (series|${f.name}|… becomes series|new name|…).` })
    if (!name || name === f.name) return
    try { await api.folderRename(f.id, name); done() } catch (e) { fail(e) }
  }
  const deleteSeries = async (x: SeriesInfo) => {
    const f = folderOf(x)
    const yes = await ask({ title: `Delete the series “${x.name}”?`, ok: 'Delete series', danger: true,
      text: `Its ${x.count} photo${x.count === 1 ? ' is' : 's are'} not deleted: ${x.count === 1 ? 'it loses' : 'they lose'} the series tag${f ? ` and leave${x.count === 1 ? 's' : ''} the board of ${f.name}` : ''}.` })
    if (!yes) return false
    try {
      await api.seriesDelete(x.id)
      if (s.boardSeries === x.id) s.setBoardSeries(f ? -f.id : null)
      done(); return true
    } catch (e) { fail(e); return false }
  }
  const deleteFolder = async (f: SeriesFolder) => {
    const yes = await ask({ title: `Delete the folder “${f.name}”?`, ok: 'Delete folder', danger: true,
      text: `Its ${f.series_count} series move to the top level with their photos and their order; no series and no photo is deleted. The folder's shared board (its layout and notes) goes away with it.` })
    if (!yes) return
    try { await api.folderDelete(f.id); if (s.boardSeries === -f.id) s.setBoardSeries(null); done() } catch (e) { fail(e) }
  }
  const moveSeries = async (x: SeriesInfo, folderId: number | null, before?: number | null) => {
    try {
      if ((x.folder_id ?? null) === folderId && before === undefined) return
      await api.seriesMove(x.id, folderId, before)
      done()
    } catch (e) { fail(e) }
  }
  const colorSeries = async (x: SeriesInfo, color: string) => {
    try { await api.seriesColor(x.id, color); done() } catch (e) { fail(e) }
  }
  return { node, ask, open, newSeries, newFolder, renameSeries, renameFolder, deleteSeries, deleteFolder, moveSeries, colorSeries, done, fail }
}
export type SeriesOps = ReturnType<typeof useSeriesOps>

export interface MenuItem { label: string; onClick?: () => void; danger?: boolean; sub?: MenuItem[]; swatches?: { value: string; pick: (c: string) => void }; disabled?: boolean }

/** A "⋯" button with a small menu under it. An entry with `sub` opens a second list in place. */
export function MenuButton({ label, items, className = 'smore', children }: { label: string; items: MenuItem[]; className?: string; children?: React.ReactNode }) {
  const [open, setOpen] = useState(false)
  const [sub, setSub] = useState<MenuItem | null>(null)
  const btn = useRef<HTMLButtonElement>(null)
  const box = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null)
  useLayoutEffect(() => {
    if (!open || !btn.current || !box.current) return
    const r = btn.current.getBoundingClientRect(), m = box.current.getBoundingClientRect()
    setPos({ left: Math.max(8, Math.min(window.innerWidth - m.width - 8, r.right - m.width)), top: r.bottom + m.height + 6 > window.innerHeight ? Math.max(8, r.top - m.height - 4) : r.bottom + 4 })
  }, [open, sub])
  useEffect(() => {
    if (!open) return
    const down = (e: Event) => { if (!box.current?.contains(e.target as Node) && !btn.current?.contains(e.target as Node)) setOpen(false) }
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('pointerdown', down, true); window.addEventListener('keydown', key)
    return () => { window.removeEventListener('pointerdown', down, true); window.removeEventListener('keydown', key) }
  }, [open])
  const list = sub?.sub || items
  return (
    <>
      <button ref={btn} type="button" className={className} aria-label={label} title={label} aria-haspopup="menu" aria-expanded={open}
        onPointerDown={e => e.stopPropagation()} onClick={e => { e.stopPropagation(); setSub(null); setOpen(o => !o) }}>
        {children || <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.7" /><circle cx="12" cy="12" r="1.7" /><circle cx="19" cy="12" r="1.7" /></svg>}
      </button>
      {open && (
        <div ref={box} className="smenu" role="menu" style={{ left: pos?.left ?? -9999, top: pos?.top ?? 0 }} onPointerDown={e => e.stopPropagation()} onClick={e => e.stopPropagation()}>
          {sub && <button type="button" className="smenu-back" onClick={() => setSub(null)}>‹ {sub.label.replace('…', '')}</button>}
          {list.map(it => it.swatches
            ? <div key={it.label} className="smenu-sw" role="group" aria-label={it.label}>
                <span className="small muted">{it.label}</span>
                {SERIES_COLORS.map(([c, n]) => <button key={c} type="button" className={'swatch' + (it.swatches!.value === c ? ' on' : '')} style={{ background: c }} title={n} aria-label={`${it.label}: ${n}`} onClick={() => { setOpen(false); it.swatches!.pick(c) }} />)}
              </div>
            : <button key={it.label} type="button" role="menuitem" className={it.danger ? 'danger' : ''} disabled={it.disabled}
                onClick={() => { if (it.sub) { setSub(it); return } setOpen(false); it.onClick?.() }}>{it.label}{it.sub ? ' ›' : ''}</button>)}
        </div>
      )}
    </>
  )
}
