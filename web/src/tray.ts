// SPDX-License-Identifier: GPL-3.0-or-later
// The tray (on the phone): photos collected while browsing any tab, to place into a series' strip later.
// Kept on this device only (localStorage), in the order they were added.
import { useEffect, useState } from 'react'

const KEY = 'lpl.tray'
const subs = new Set<() => void>()
let ids: number[] = (() => { try { const v = JSON.parse(localStorage.getItem(KEY) || '[]'); return Array.isArray(v) ? v.filter(x => typeof x === 'number') : [] } catch { return [] } })()

function set(next: number[]) {
  ids = next
  try { localStorage.setItem(KEY, JSON.stringify(ids)) } catch { /* ignore */ }
  subs.forEach(f => f())
}

export const tray = {
  get: () => ids,
  /** add photos (those already in it keep their place); returns how many were new */
  add(more: number[]): number { const have = new Set(ids); const n = more.filter(i => !have.has(i)); set([...ids, ...n]); return n.length },
  remove(gone: number[]) { const g = new Set(gone); set(ids.filter(i => !g.has(i))) },
  clear() { set([]) },
}

export function useTray(): number[] {
  const [, bump] = useState(0)
  useEffect(() => { const f = () => bump(x => x + 1); subs.add(f); return () => { subs.delete(f) } }, [])
  return ids
}

/** Photos grouped by capture day (YYYY-MM-DD; '' = no date), days in order, photos in the order given. */
export function byDay(photos: { id: number; taken: string | null }[]): [string, number[]][] {
  const m = new Map<string, number[]>()
  for (const p of photos) { const d = (p.taken || '').slice(0, 10); m.set(d, [...(m.get(d) || []), p.id]) }
  return Array.from(m.entries()).sort((a, b) => (a[0] || '~').localeCompare(b[0] || '~'))
}
