// SPDX-License-Identifier: GPL-3.0-or-later
// The Series view's plain logic: what is open (a series or a folder of series), the tree of folders and
// series, and what a drop in the strip means. No React, no DOM: tested in tests/series.test.ts.
import type { Lane, Photo, SeriesFolder, SeriesInfo, Target } from './api'

/** A tab remembers what its Series view shows as one number (TabState.boardSeries, as before folders existed):
 *  a series' id, or minus a folder's id. */
export const targetOf = (key: number | null | undefined): Target | null =>
  (key == null || !key ? null : key < 0 ? { kind: 'folder', id: -key } : { kind: 'series', id: key })
export const keyOf = (t: Target): number => (t.kind === 'folder' ? -t.id : t.id)

const byPos = <T extends { pos?: number | null; name: string }>(a: T, b: T) =>
  (a.pos ?? 1e9) - (b.pos ?? 1e9) || a.name.localeCompare(b.name, undefined, { sensitivity: 'base', numeric: true })

export type TreeEntry = { kind: 'folder'; folder: SeriesFolder; series: SeriesInfo[]; kids: TreeEntry[]; depth: number } | { kind: 'series'; series: SeriesInfo }
export interface FlatFolder { folder: SeriesFolder; series: SeriesInfo[]; depth: number; path: string[] }
export interface Tree { folders: FlatFolder[]; top: SeriesInfo[]; entries: TreeEntry[] }
const posOf = (e: TreeEntry) => (e.kind === 'folder' ? e.folder.pos : e.series.pos) ?? 1e9
/** The tree of folders (to any depth) and series. `entries`: the top level; a folder entry has its `kids` (subfolders and
 *  series in one order: the server's pos, a folder first on a tie) and `series` (those directly in it). `folders`:
 *  every folder in tree order, with its depth and the names from the top-level folder down to it. */
export function buildTree(series: SeriesInfo[], folders: SeriesFolder[]): Tree {
  const known = new Set(folders.map(f => f.id))
  const flat: FlatFolder[] = []
  const seen = new Set<number>()
  const level = (parent: number | null, depth: number, path: string[]): TreeEntry[] => {
    const out: TreeEntry[] = []
    for (const folder of folders.filter(f => (f.parent_id != null && known.has(f.parent_id) ? f.parent_id : null) === parent).sort(byPos)) {
      if (seen.has(folder.id)) continue
      seen.add(folder.id)
      const own = series.filter(x => x.folder_id === folder.id).sort(byPos)
      const e = { kind: 'folder' as const, folder, series: own, kids: [] as TreeEntry[], depth }
      flat.push({ folder, series: own, depth, path: [...path, folder.name] })
      e.kids = level(folder.id, depth + 1, [...path, folder.name])
      out.push(e)
    }
    for (const x of series.filter(x => (x.folder_id != null && known.has(x.folder_id) ? x.folder_id : null) === parent).sort(byPos)) out.push({ kind: 'series', series: x })
    return out.sort((a, b) => posOf(a) - posOf(b) || (a.kind === b.kind ? 0 : a.kind === 'folder' ? -1 : 1))
  }
  const entries = level(null, 0, [])
  const top = series.filter(x => x.folder_id == null || !known.has(x.folder_id)).sort(byPos)
  return { folders: flat, top, entries }
}

/** A folder and every folder below it. */
export function folderSubtree(folders: SeriesFolder[], id: number): Set<number> {
  const out = new Set<number>([id])
  for (let grew = true; grew;) { grew = false; for (const f of folders) if (f.parent_id != null && out.has(f.parent_id) && !out.has(f.id)) { out.add(f.id); grew = true } }
  return out
}
/** The names from the top-level folder down to this one. */
export function folderPath(folders: SeriesFolder[], id: number | null | undefined): string[] {
  const out: string[] = []
  for (let f = folders.find(x => x.id === id), n = 0; f && n < 50; f = folders.find(x => x.id === f!.parent_id), n++) out.unshift(f.name)
  return out
}

/** What to show when a tab has nothing (valid) chosen: the first folder, else the first series. */
export function firstTarget(tree: Tree): Target | null {
  const e = tree.entries[0]
  return !e ? null : e.kind === 'folder' ? { kind: 'folder', id: e.folder.id } : { kind: 'series', id: e.series.id }
}

/** The chosen target if it still exists, else the first one. */
export function resolveTarget(key: number | null | undefined, series: SeriesInfo[], folders: SeriesFolder[]): Target | null {
  const t = targetOf(key)
  if (t && (t.kind === 'folder' ? folders.some(f => f.id === t.id) : series.some(x => x.id === t.id))) return t
  return firstTarget(buildTree(series, folders))
}

export function targetName(t: Target | null, series: SeriesInfo[], folders: SeriesFolder[]): string {
  if (!t) return ''
  return (t.kind === 'folder' ? folders.find(f => f.id === t.id)?.name : series.find(x => x.id === t.id)?.name) || ''
}

export const num2 = (n: number | null | undefined) => (n ? String(n).padStart(2, '0') : '')

/** Which slot of a lane the pointer is over: 0 = in front of the first photo, count = after the last.
 *  x is measured from the left edge of the first cell; cells are `cell` wide (photo plus gap). */
export const slotAt = (x: number, cell: number, count: number) => Math.max(0, Math.min(count, Math.round(x / cell)))

/** A drop into `slot` of a lane showing `shown` (photo ids as displayed, dragged ones included where they
 *  still stand): the photo to put the dragged ones in front of (null: at the end), and whether anything
 *  changes at all (dropping a photo where it already is changes nothing). */
export function dropBefore(shown: number[], dragged: number[], slot: number, sameLane: boolean): { before: number | null; noop: boolean } {
  const drag = new Set(dragged)
  const before = shown.slice(slot).find(id => !drag.has(id)) ?? null
  if (!sameLane) return { before, noop: false }
  const rest = shown.filter(id => !drag.has(id))
  const k = before == null ? rest.length : rest.indexOf(before)
  const next = [...rest.slice(0, k), ...dragged, ...rest.slice(k)]
  return { before, noop: next.length === shown.length && next.every((id, i) => id === shown[i]) }
}

/** The lanes as they will look after a move, before the server answers (it then sends the real thing):
 *  the dragged photos leave their lanes and stand in `to` in front of `before`. */
export function movedLanes(lanes: Lane[], dragged: number[], to: string, before: number | null): Lane[] {
  const drag = new Set(dragged)
  const by = new Map<number, Photo>()
  for (const l of lanes) for (const p of l.photos) if (drag.has(p.id)) by.set(p.id, p)
  const moved = dragged.map(id => by.get(id)).filter(Boolean) as Photo[]
  return lanes.map(l => {
    const rest = l.photos.filter(p => !drag.has(p.id))
    if (l.key !== to) return rest.length === l.photos.length ? l : { ...l, photos: rest }
    const i = before == null ? -1 : rest.findIndex(p => p.id === before)
    const k = i < 0 ? rest.length : i
    const sid = l.series?.id
    return { ...l, photos: [...rest.slice(0, k), ...moved.map(p => (sid ? { ...p, series_id: sid } : p)), ...rest.slice(k)] }
  })
}

/** The numbers the lanes show, worked out here only to bridge the moment until the server's answer: hidden
 *  photos and "maybe" lanes have none; in a folder every lane starts at 1, in a single series they run on. */
export function renumber(lanes: Lane[], perLane: boolean): Lane[] {
  let n = 0
  return lanes.map(l => {
    if (perLane) n = 0
    return { ...l, photos: l.photos.map(p => ({ ...p, num: l.maybe || p.hidden ? null : ++n })) }
  })
}

/** The number the first dragged photo gets when dropped into `slot` (for the note next to the pointer). */
export function numAtSlot(shown: Photo[], dragged: number[], slot: number, offset = 0): number {
  const drag = new Set(dragged)
  return offset + shown.slice(0, slot).filter(p => !drag.has(p.id) && !p.hidden).length + 1
}
