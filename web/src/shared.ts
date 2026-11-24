// SPDX-License-Identifier: GPL-3.0-or-later
// Shared darktable styles, presets and module layouts (R11, docs/shared-r11.md; server/lpl/shared.py): what the Devices
// page shows for them and what it asks of the server. Plain functions, so they can be tested without a browser.
export type SharedKind = 'style' | 'preset' | 'layout'
export interface SharedItem {
  id: number; kind: SharedKind; operation: string; op_version: number; name: string; rev: number; seq: number; hash: string
  changed_at: number | null; device_id: string | null; device_name: string | null; deleted: boolean; size: number
}
export interface SharedWaiting extends SharedItem { what: 'new' | 'changed' | 'deleted' }
export interface SharedConflict extends SharedItem { candidate: { changed_at: number | null; deleted: boolean; hash: string | null; base_rev: number | null } }
export type SharedDot = 'newest' | 'waiting' | 'conflict' | 'kept' | 'unusable' | 'absent'
export interface SharedDevice {
  id: string; name: string; os: string | null; phone: boolean; online: boolean; policy: 'ask' | 'newest'
  update: 'waiting_for_darktable' | 'applying' | null; darktable_running: boolean
  job: { id: number; status: string } | null; waiting: SharedWaiting[]; conflicts: SharedConflict[]
}
export interface SharedOverview { policy: 'ask' | 'newest'; devices: SharedDevice[]; items: (SharedItem & { devices: Record<string, SharedDot> })[] }
export type Choice = 'mine' | 'theirs' | 'both'
export type DeleteChoice = 'delete' | 'keep'

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

/** The line under a device's name: "3 styles, presets, layouts waiting" / "… up to date"; the phone updates itself. */
export function waitingLine(d: Pick<SharedDevice, 'phone' | 'waiting' | 'conflicts'>): string {
  const n = d.waiting.length + d.conflicts.length
  if (d.phone) return n ? `${n} waiting · updated from the app` : 'up to date · updated from the app'
  return n ? `${n} styles, presets, layouts waiting` : 'styles, presets, layouts up to date'
}

/** "style", "preset · exposure", "module layout". */
export function kindText(i: Pick<SharedItem, 'kind' | 'operation'>): string {
  return i.kind === 'style' ? 'style' : i.kind === 'layout' ? 'module layout' : `preset · ${i.operation}`
}

/** What happened to a waiting item, from this device's point of view. */
export function whatText(w: Pick<SharedWaiting, 'what' | 'device_name' | 'device_id'>): string {
  return w.what === 'deleted' ? `deleted on ${w.device_name || w.device_id || 'another device'}` : w.what
}

/** The three choices for a PC's conflict: Keep <PC>'s / Take <other>'s / Keep both (not when one side is a deletion). */
export function conflictChoices(deviceName: string, c: Pick<SharedConflict, 'deleted' | 'device_name' | 'device_id' | 'name' | 'candidate'>): { choice: Choice; label: string; hint: string; ok: boolean }[] {
  const other = c.device_name || c.device_id || 'the other device'
  const both = !c.deleted && !c.candidate.deleted
  return [
    { choice: 'mine', label: `Keep ${deviceName}'s`, hint: c.candidate.deleted ? 'deleted everywhere' : 'the other devices are offered this one', ok: true },
    { choice: 'theirs', label: `Take ${other}'s`, hint: c.deleted ? `deleted here too` : `replaces ${deviceName}'s at the next update`, ok: true },
    { choice: 'both', label: 'Keep both', hint: both ? `${other}'s arrives as “${c.name} (${other})”` : 'not possible: one side deleted it', ok: both },
  ]
}

/** What "Update <device>" sends: ticked items; a ticked deletion is deleted or kept as chosen (default: delete here). */
export function updatePayload(agent: string, waiting: Pick<SharedWaiting, 'id' | 'what'>[], ticked: Set<number>, deletes: Record<number, DeleteChoice>) {
  const out = { agent, ids: [] as number[], delete: [] as number[], keep: [] as number[] }
  for (const w of waiting) {
    if (!ticked.has(w.id)) continue
    if (w.what !== 'deleted') out.ids.push(w.id)
    else (deletes[w.id] === 'keep' ? out.keep : out.delete).push(w.id)
  }
  return out
}

export const updateLabel = (name: string, n: number) => `Update ${name}${n ? ` · ${n}` : ''}`

/** The note beside the Update button. */
export function updateNote(d: Pick<SharedDevice, 'name' | 'darktable_running' | 'update' | 'job' | 'online'>): string {
  if (d.update === 'applying') return `Updating darktable's styles and presets on ${d.name}…`
  if (d.job && d.update === 'waiting_for_darktable') return `Update sent: ${d.name}'s agent applies it when you close darktable.`
  if (d.job) return d.online ? `Update sent to ${d.name}.` : `Update sent: it is applied when ${d.name} is back online.`
  return d.darktable_running
    ? `darktable is open on ${d.name}: the agent applies the update when you close darktable (presets and layouts live in its database, which must not be written while it runs).`
    : `darktable must be closed on ${d.name} for the update to be applied; the agent waits for it if it is open.`
}

/** The dot per device in "Shared through LPL": symbol and words. */
export const DOTS: Record<SharedDot, [string, string]> = {
  newest: ['●', 'has the newest'], waiting: ['○', 'waiting'], conflict: ['!', 'changed on two devices'],
  kept: ['●', 'kept here after a deletion elsewhere'], unusable: ['–', 'needs a newer darktable'], absent: ['–', 'not there'],
}

/** The overview lists live items, and a deletion only while some device is still offered it. */
export const overviewItems = (o: Pick<SharedOverview, 'items'>) => o.items.filter(i => !i.deleted || Object.values(i.devices).some(d => d === 'waiting' || d === 'conflict' || d === 'kept'))

/** "Desktop · 23 Nov 14:05" */
export function lastChange(i: Pick<SharedItem, 'device_name' | 'device_id' | 'changed_at'>, when: (t: number) => string): string {
  return [i.device_name || i.device_id || '', i.changed_at ? when(i.changed_at) : ''].filter(Boolean).join(' · ')
}

export const countLabel = (n: number) => plural(n, 'item', 'items')
