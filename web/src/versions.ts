// SPDX-License-Identifier: GPL-3.0-or-later
// Versions of a photo (R17, docs/versions-r17.md): what the tile, the strip and the edit calls need. Pure functions
// plus a tiny store for "which version is being looked at"; the components are in components/Versions.tsx.
import { useSyncExternalStore } from 'react'
import type { Photo, VersionInfo } from './api'

/** The number on a tile's badge: how many versions the photo has, when it has more than one. */
export const versionBadge = (p: Pick<Photo, 'versions'>): number | null => (p.versions ?? 1) > 1 ? (p.versions as number) : null

/** "v2" or "v2 · black and white". */
export const versionLabel = (v: Pick<VersionInfo, 'n' | 'name'>): string => `v${v.n}` + (v.name ? ` · ${v.name}` : '')

/** "edited" / "no edits" for a version, as the strip says it. */
export const versionState = (v: Pick<VersionInfo, 'edited'>): string => v.edited ? 'edited' : 'no edits'

/** The version being looked at in the panel: the one the user picked if it still exists, else the cover. */
export function viewedVersion(list: VersionInfo[] | null | undefined, picked: number | undefined, cover: number): number {
  if (!list || !list.length) return cover
  if (picked != null && list.some(v => v.n === picked)) return picked
  return list.some(v => v.n === cover) ? cover : list[0].n
}

/** The photo as it is in the viewed version: its rating, labels, tags and edited flag. The cover (and a photo without
 *  versions) is the photo itself. */
export function viewedPhoto(p: Photo, list: VersionInfo[] | null | undefined, n: number): Photo {
  if (!list || n === (p.cover ?? 0)) return p
  const v = list.find(x => x.n === n)
  return v ? { ...p, rating: v.rating, labels: v.labels, tags: v.tags, edited: v.edited } : p
}

/** The `version` to send with a rating / label / tag change: only when it is not the cover (the server's default). */
export const metaVersion = (p: Pick<Photo, 'cover'>, n: number): number | undefined => n === (p.cover ?? 0) ? undefined : n

/** Whether "use in series" makes sense for the viewed version: it is not what the entry shows already. */
export const canPin = (entry: Pick<Photo, 'cover' | 'pin' | 'show'> | null, n: number): boolean =>
  !!entry && (entry.show ?? entry.cover ?? 0) !== n

/** The version the viewed entry follows after "use in series": null = back to the cover. */
export const pinFor = (p: Pick<Photo, 'cover'>, n: number): number | null => n === (p.cover ?? 0) ? null : n

/** What the delete button asks first (version 0 is never deletable from LPL or the app). */
export const deleteText = (v: Pick<VersionInfo, 'n' | 'name'>): { title: string; text: string } => ({
  title: `Delete ${versionLabel(v)}?`,
  text: 'LPL moves its file to the backup folder, it does not erase it. The original and the other versions stay as they are.',
})

// ---------------------------------------------------------------- which version is looked at (per photo)
const picked = new Map<number, number>()
let rev = 0
const subs = new Set<() => void>()
const bump = () => { rev++; subs.forEach(f => f()) }
export const pickedVersion = (id: number): number | undefined => picked.get(id)
export function pickVersion(id: number, n: number) { if (picked.get(id) !== n) { picked.set(id, n); bump() } }
/** The versions of a photo changed (cover, name, delete, a rating of one version): lists load again. */
export const bumpVersions = bump
export const useVersionsRev = () => useSyncExternalStore(cb => { subs.add(cb); return () => { subs.delete(cb) } }, () => rev)
