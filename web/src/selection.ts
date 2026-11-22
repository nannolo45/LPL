// SPDX-License-Identifier: GPL-3.0-or-later
// Multi-select on the Devices page: pure logic (ranges, select all, what a selection can do). The page keeps one
// selection, of one device; `order` is every photo row of that device in the order shown, also the rows hidden
// behind "N more · show them".

export type RowKind = 'copying' | 'waiting' | 'arrived' | 'unsent'
export interface SelRow { id: number; kind: RowKind; /** can be left out of the batch right now */ droppable?: boolean; unsaved?: boolean }
export interface Selection { agent: string; ids: number[]; anchor: number | null }

/** Click: toggles one photo and makes it the anchor of a later Shift-click. */
export function toggleId(ids: number[], id: number): number[] {
  return ids.includes(id) ? ids.filter(x => x !== id) : [...ids, id]
}

/** Shift-click: every row between the anchor and the clicked one (both included, hidden rows too) joins the
 *  selection. No anchor, or one that is gone: just the clicked row. */
export function rangeIds(order: number[], ids: number[], anchor: number | null, id: number): number[] {
  const a = anchor == null ? -1 : order.indexOf(anchor), b = order.indexOf(id)
  if (a < 0 || b < 0) return ids.includes(id) ? ids : [...ids, id]
  const [lo, hi] = a < b ? [a, b] : [b, a]
  const add = order.slice(lo, hi + 1)
  return [...ids, ...add.filter(x => !ids.includes(x))]
}

/** Header checkbox: none, some or all of the rows. */
export function boxState(order: number[], ids: number[]): 'none' | 'some' | 'all' {
  const n = order.filter(id => ids.includes(id)).length
  return n === 0 ? 'none' : n === order.length ? 'all' : 'some'
}

/** A group's "Select all" adds the whole group (hidden rows included); when it is all selected already it takes it out. */
export function toggleGroup(ids: number[], group: number[]): number[] {
  const all = group.length > 0 && group.every(id => ids.includes(id))
  return all ? ids.filter(id => !group.includes(id)) : [...ids, ...group.filter(id => !ids.includes(id))]
}

/** Photos that left the list leave the selection; same array when nothing changed. */
export function prune(ids: number[], order: number[]): number[] {
  const here = new Set(order)
  return ids.every(id => here.has(id)) ? ids : ids.filter(id => here.has(id))
}

export interface Bulk {
  /** the selected photos "Don't send" can really take out, and how many selected ones it cannot */
  drop: number[]; stuck: number
  open: number[]; save: number[]; done: number[]
  /** Show in library: every selected photo */
  show: number[]
}

/** What the selection bar offers: each action with the photos it applies to (in display order). */
export function bulkActions(rows: SelRow[], ids: number[]): Bulk {
  const chosen = new Set(ids)
  const sel = rows.filter(r => chosen.has(r.id))
  const queued = sel.filter(r => r.kind !== 'arrived')
  const on = sel.filter(r => r.kind === 'arrived')
  return {
    drop: queued.filter(r => r.droppable).map(r => r.id),
    stuck: queued.filter(r => !r.droppable).length,
    open: on.map(r => r.id),
    save: on.filter(r => r.unsaved).map(r => r.id),
    done: on.map(r => r.id),
    show: sel.map(r => r.id),
  }
}
