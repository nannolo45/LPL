// SPDX-License-Identifier: GPL-3.0-or-later
// Who edited a photo last and what changed (R15), in words. Plain functions without React, so they can be
// tested with Node (web/tests/edits.test.ts). See docs/android-edit-record-progress.md.
import type { Agent, EditEvent, EditModule } from './api'
import { agentLabel } from './devices.ts'

const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const two = (n: number) => String(n).padStart(2, '0')

/** "today 14:05", "yesterday 09:30", "18 Nov 14:05", "18 Nov 2025" (local time). now in seconds. */
export function fmtWhen(ts: number, now: number = Date.now() / 1000): string {
  const d = new Date(ts * 1000), n = new Date(now * 1000)
  const hm = `${two(d.getHours())}:${two(d.getMinutes())}`
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const days = Math.round((day(n) - day(d)) / 86400000)
  if (days === 0) return `today ${hm}`
  if (days === 1) return `yesterday ${hm}`
  if (d.getFullYear() === n.getFullYear()) return `${d.getDate()} ${MON[d.getMonth()]} ${hm}`
  return `${d.getDate()} ${MON[d.getMonth()]} ${d.getFullYear()}`
}

/** Where the edit came from, to follow "edited": "on Desktop", "outside LightPhotoLibrary", … */
export function editWhere(e: Pick<EditEvent, 'source' | 'agent_id' | 'device' | 'device_os'>, agents: Pick<Agent, 'id' | 'name' | 'os'>[] = []): string {
  switch (e.source) {
    case 'outside': return 'outside LightPhotoLibrary'
    case 'import': return 'in a local library on a PC'
    case 'local': return 'in darktable on this PC'
  }
  if (e.device) {
    // the name it had then; told apart like the device list does when another device has the same name now
    const me = { id: e.agent_id || '', name: e.device, os: e.device_os || '' }
    return `on ${agentLabel(me, [...agents.filter(a => a.id !== me.id), me])}`
  }
  if (e.agent_id) {
    const a = agents.find(x => x.id === e.agent_id)
    return `on ${a ? agentLabel(a, agents) : e.agent_id}`
  }
  return 'on a device'
}

/** "last edited on Desktop · 18 Nov 14:05 · exposure, color balance rgb" */
export function lastEditLine(e: EditEvent, now?: number, agents: Pick<Agent, 'id' | 'name' | 'os'>[] = []): string {
  return `last edited ${editWhere(e, agents)} · ${fmtWhen(e.time, now)} · ${e.summary}`
}

const names = (ms: EditModule[]) => ms.map(m => m.label).join(', ')

/** When an entry was saved: "today 18:30", or for several saves of the day "today 10:02–18:30 · 5 saves". */
export function entryWhen(e: Pick<EditEvent, 'time' | 'first_time' | 'saves'>, now?: number): string {
  const n = e.saves || 1
  if (n < 2 || !e.first_time || e.first_time >= e.time) return fmtWhen(e.time, now) + (n > 1 ? ` · ${n} saves` : '')
  const d = new Date(e.time * 1000), from = fmtWhen(e.first_time, now), to = `${two(d.getHours())}:${two(d.getMinutes())}`
  return `${from.endsWith(to) ? fmtWhen(e.time, now) : `${from}–${to}`} · ${n} saves`  // all in one minute: once
}

/** One line per kind of change ("changed: exposure"), or, when there is nothing to compare with, the modules in
 *  use that darktable did not apply by itself ("darktable's default edit" when there are none). */
export function changeLines(e: Pick<EditEvent, 'changes' | 'modules'>): string[] {
  if (!e.changes) {
    if (!e.modules.length) return ['no darktable edit']
    const mine = e.modules.filter(m => !m.auto)
    const on = mine.filter(m => m.enabled), off = mine.filter(m => !m.enabled)
    if (!on.length && !off.length) return ["darktable's default edit"]
    const out = on.length ? [`modules in use: ${names(on)}`] : []
    if (off.length) out.push(`off: ${names(off)}`)
    return out
  }
  const c = e.changes
  const out: string[] = []
  if (c.changed.length) out.push(`changed: ${names(c.changed)}`)
  if (c.added.length) out.push(`added: ${names(c.added)}`)
  if (c.switched_on.length) out.push(`switched on: ${names(c.switched_on)}`)
  if (c.switched_off.length) out.push(`switched off: ${names(c.switched_off)}`)
  if (c.removed.length) out.push(`removed: ${names(c.removed)}`)
  return out.length ? out : ['no module changed']
}

/** Every module of an edit, darktable's automatic ones and switched-off ones marked: "exposure",
 *  "raw black/white point · automatic", "vignette · off". */
export function allModules(e: Pick<EditEvent, 'modules'>): string[] {
  return e.modules.map(m => m.label + (m.auto ? ' · automatic' : '') + (m.enabled ? '' : ' · off'))
}
