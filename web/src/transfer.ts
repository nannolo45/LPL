// SPDX-License-Identifier: GPL-3.0-or-later
// Photos on their way to a device, as the server reports them with each device (GET /api/status, /api/agents):
// how far a batch is, how long it will take, what the Devices page and the top bar say about it.
// Plain functions without React, tested with Node (web/tests/transfer.test.ts). See docs/devices-stop-estimate.md.
import type { Agent, WorkItem } from './api'
import { AGENT_ONLINE_SECONDS, agentLabel, hasUnsavedEdits, isPhoneAgent } from './devices.ts'

export interface TransferItem { id: number; filename: string; job: number }
/** What is being sent to a device right now, all its batches added up. state: queued = the device has not
 *  taken it yet; stopping = Stop was pressed and the device has not confirmed. samples: [server time, bytes
 *  done, photos done] of the last minute and a half, for the estimate. known false: the device says nothing
 *  about batches (only what waits is known). */
export interface Transfer {
  run: number; state: 'queued' | 'running' | 'stopping'
  photos_total: number; photos_done: number; failed: number; bytes_total: number; bytes_done: number
  current: { id: number; filename: string; bytes: number; size: number } | null
  waiting: TransferItem[]; arrived: number[]; started: number; samples: [number, number, number][]
  can_stop: boolean; can_skip: boolean; known: boolean
}
/** A batch that was stopped (reason user) or that the device lost (interrupted, e.g. it restarted). */
export interface StoppedRun {
  run: number; reason: 'user' | 'interrupted' | string; time: number
  photos_total: number; photos_done: number; failed: number; bytes_done: number; bytes_total: number
  unsent: TransferItem[]; arrived: number[]
}

type Dev = Pick<Agent, 'id' | 'name' | 'os' | 'online' | 'last_seen' | 'state'> & { transfer?: Transfer | null; stopped?: StoppedRun | null; folded?: boolean | null }

// ---------------------------------------------------------------- how long it will take
/** window: the rate is the average over this many seconds (what keeps it from jumping around);
 *  minSeconds / minPhotos: until there is this much to go on it says "estimating…";
 *  stale: no report for this long and it says "estimating…" again rather than counting down by itself;
 *  stall: reports keep coming but nothing has moved for this long (the line hangs): "estimating…" too. */
export const ESTIMATE = { window: 60, minSeconds: 8, minPhotos: 3, stale: 12, stall: 10 }

export type Estimate = { kind: 'estimating' } | { kind: 'eta'; seconds: number; rate: number }

/** The rolling rate (bytes per second, or photos per second when no sizes are known) from the samples of
 *  the last ESTIMATE.window seconds; null while there is not enough to go on. */
export function rollingRate(samples: [number, number, number][], now: number, unit: 'bytes' | 'photos' = 'bytes'): number | null {
  const pts = samples.filter(p => p[0] >= now - ESTIMATE.window && p[0] <= now + 1).sort((a, b) => a[0] - b[0])
  if (pts.length < 2) return null
  const first = pts[0], last = pts[pts.length - 1]
  if (now - last[0] > ESTIMATE.stale) return null
  const span = last[0] - first[0], photos = last[2] - first[2]
  if (!(span >= ESTIMATE.minSeconds || (photos >= ESTIMATE.minPhotos && span >= 2))) return null
  const k = unit === 'bytes' ? 1 : 2
  if (unit === 'bytes') {  // nothing at all in the last ESTIMATE.stall seconds: no number is better than a wrong one
    const before = [...pts].reverse().find(p => p[0] <= last[0] - ESTIMATE.stall)
    if (before && last[1] - before[1] <= 0) return null
  }
  const moved = last[k] - first[k]
  return moved > 0 ? moved / span : null
}

/** How long the rest will take: what is left, divided by the rolling rate. */
export function estimate(t: Pick<Transfer, 'samples' | 'bytes_total' | 'bytes_done' | 'photos_total' | 'photos_done'>, now: number): Estimate {
  const bytes = t.bytes_total > 0
  const rate = rollingRate(t.samples || [], now, bytes ? 'bytes' : 'photos')
  if (rate == null) return { kind: 'estimating' }
  const left = bytes ? t.bytes_total - t.bytes_done : t.photos_total - t.photos_done
  return { kind: 'eta', seconds: Math.max(0, left) / rate, rate }
}

/** The time left in rough words: "less than a minute left", "about 6 min left", "about 25 min left",
 *  "about an hour left", "about 2.5 hours left". long: "minutes" written out (for screen readers). */
export function etaWords(seconds: number, long = false): string {
  const min = seconds / 60, m = long ? 'minutes' : 'min'
  if (seconds < 50) return 'less than a minute left'
  if (seconds < 90) return 'about a minute left'
  if (min < 9.5) return `about ${Math.round(min)} ${m} left`
  if (min < 57.5) return `about ${5 * Math.round(min / 5)} ${m} left`
  if (min < 90) return 'about an hour left'
  return `about ${Math.round(min / 30) / 2} hours left`
}

export const estimateWords = (e: Estimate, long = false) => (e.kind === 'eta' ? etaWords(e.seconds, long) : 'estimating…')

/** "1.4 GB", "850 MB", "3.2 MB", "12 KB". */
export function fmtBytes(n: number): string {
  const kb = 1024, mb = kb * 1024, gb = mb * 1024
  if (n >= gb) return `${(n / gb).toFixed(1)} GB`
  if (n >= 10 * mb) return `${Math.round(n / mb)} MB`
  if (n >= mb) return `${(n / mb).toFixed(1)} MB`
  return `${Math.max(0, Math.round(n / kb))} KB`
}

/** "just now", "20s ago", "3 min ago", "2 h ago" */
export const ago = (t: number, now: number) => {
  const s = Math.max(0, Math.round(now - t))
  return s < 5 ? 'just now' : s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`
}

const photos = (n: number) => `${n} photo${n === 1 ? '' : 's'}`

// ---------------------------------------------------------------- what a device's bar says
/** Is this device heard from (by the server's clock)? A bar must not claim progress when it is not. */
export const reporting = (a: Pick<Agent, 'online' | 'last_seen'>, now?: number) => a.online && (now == null || now - (a.last_seen || 0) < AGENT_ONLINE_SECONDS)

/** The device's bar: title, the line under it, how far (0..1), and whether anything moves. */
export function transferLines(t: Transfer, name: string, a: Pick<Agent, 'os' | 'online' | 'last_seen'>, now: number):
  { title: string; detail: string; eta: string | null; fraction: number; moving: boolean } {
  const fraction = t.bytes_total > 0 ? Math.min(1, t.bytes_done / t.bytes_total) : t.photos_total ? t.photos_done / t.photos_total : 0
  const size = t.bytes_total > 0 ? `${fmtBytes(t.bytes_done)} of ${fmtBytes(t.bytes_total)}` : ''
  if (t.state === 'queued') {
    const when = reporting(a, now) ? 'It starts in a moment.'
      : isPhoneAgent(a) ? `They are sent the next time the darktable app is opened on ${name}.` : `They are sent as soon as ${name} is online.`
    return { title: `${photos(t.photos_total - t.photos_done)} waiting to be sent to ${name}`, detail: when, eta: null, fraction, moving: false }
  }
  if (!reporting(a, now))
    return { title: `${name} is offline · ${t.photos_done} of ${t.photos_total} photos arrived`, detail: `${size ? size + '. ' : ''}It carries on when ${name} is back.`, eta: null, fraction, moving: false }
  if (t.state === 'stopping')
    return { title: `Stopping · ${t.photos_done} of ${t.photos_total} photos on ${name}`, detail: size, eta: null, fraction, moving: false }
  return { title: `Sending ${t.photos_done} of ${t.photos_total} photos to ${name}`, detail: size, eta: estimateWords(estimate(t, now)), fraction, moving: true }
}

export function stoppedLines(s: StoppedRun, name: string): { title: string; detail: string; fraction: number } {
  const n = s.unsent.length
  const rest = n ? `The other ${photos(n)} ${n === 1 ? 'was' : 'were'} not sent; nothing was lost.` : 'Nothing was lost.'
  return {
    title: `Stopped · ${s.photos_done} of ${s.photos_total} on ${name}`,
    detail: s.reason === 'interrupted' ? `${name} was restarted while it was receiving. ${rest}` : `${fmtBytes(s.bytes_done)} copied. ${rest}`,
    fraction: s.bytes_total > 0 ? Math.min(1, s.bytes_done / s.bytes_total) : s.photos_total ? s.photos_done / s.photos_total : 0,
  }
}

/** The line next to a device's name: "12 photos · 3 edited, not saved yet", "38 photos · receiving 82 more",
 *  "4 photos · all saved to the NAS", "38 photos · sending stopped". */
export function deviceCounts(a: Dev): string {
  const ws = (a.state?.working_set || []).filter(w => w.status !== 'downloading')
  const parts = [ws.length ? photos(ws.length) : 'no photos']
  const unsaved = ws.filter(hasUnsavedEdits).length
  if (unsaved) parts.push(`${unsaved} edited, not saved yet`)
  const t = a.transfer
  if (t && t.state === 'queued') parts.push(`${t.photos_total - t.photos_done} waiting to be sent`)
  else if (t) parts.push(`receiving ${t.photos_total - t.photos_done - t.failed} more`)
  else if (a.stopped) parts.push('sending stopped')
  else if (!unsaved && ws.length && ws.every(w => w.status === 'synced')) parts.push('all saved to the NAS')
  return parts.join(' · ')
}

/** Folded as the user left it. Never chosen: a device that is being sent photos (or was, until a stop) is
 *  unfolded, also when it goes offline in the middle; otherwise offline devices and devices with nothing on
 *  them are folded. */
export function isFolded(a: Dev): boolean {
  if (a.folded != null) return a.folded
  if (a.transfer || a.stopped) return false
  return !a.online || !(a.state?.working_set || []).length
}

/** The rows of a device's table, in the order they are shown: what is being copied, what waits, what is on
 *  the device (errors and unsaved edits first, then the latest), and what a stop left unsent. */
export function deviceRows(a: Dev): { copying: WorkItem[]; waiting: TransferItem[]; arrived: WorkItem[]; unsent: TransferItem[] } {
  const ws = a.state?.working_set || []
  const here = new Set(ws.map(w => w.id))
  const rank = (w: WorkItem) => (w.status === 'error' ? 0 : hasUnsavedEdits(w) ? 1 : 2)
  const arrived = ws.filter(w => w.status !== 'downloading')
    .map((w, i) => ({ w, i })).sort((x, y) => rank(x.w) - rank(y.w) || (y.w.checkout_ts || 0) - (x.w.checkout_ts || 0) || x.i - y.i).map(x => x.w)
  const copying = ws.filter(w => w.status === 'downloading')
  const cur = a.transfer?.current
  if (cur && !here.has(cur.id) && a.online)  // reported as under way, not in the working set yet
    copying.push({ id: cur.id, filename: cur.filename, status: 'downloading', step: 'downloading', progress: cur.size ? cur.bytes / cur.size : undefined })
  return {
    copying, arrived,
    waiting: (a.transfer?.waiting || []).filter(w => !here.has(w.id)),
    unsent: a.transfer ? [] : (a.stopped?.unsent || []),
  }
}

// ---------------------------------------------------------------- the top bar
export interface Overall {
  kind: 'running' | 'stopping' | 'offline' | 'stopped' | 'waiting' | 'idle'
  /** bold text ("38 / 120", "2 of 3 online") and the words after it ("about 6 min left", "stopped") */
  text: string; sub: string
  /** how full the ring is (0..1), or null for no ring */
  fraction: number | null
  /** for screen readers: "Devices: sending 38 of 120 photos to Laptop, about 6 minutes left" */
  label: string
}

/** What the top bar's indicator says: a transfer that runs, else one that was stopped, else what waits,
 *  else how many devices are online. Several devices receiving at once: added up, with the longest time left. */
export function overall(agents: Dev[], now: number): Overall {
  const label = (a: Dev) => agentLabel(a, agents)
  const names = (as: Dev[]) => (as.length === 1 ? label(as[0]) : `${as.length} devices`)
  const sum = (as: Dev[], f: (t: Transfer) => number) => as.reduce((n, a) => n + f(a.transfer!), 0)
  const frac = (as: Dev[]) => { const tot = sum(as, t => t.bytes_total); return tot > 0 ? Math.min(1, sum(as, t => t.bytes_done) / tot) : (sum(as, t => t.photos_total) ? sum(as, t => t.photos_done) / sum(as, t => t.photos_total) : 0) }
  const busy = agents.filter(a => a.transfer && a.transfer.state !== 'queued')
  const live = busy.filter(a => reporting(a, now))
  if (live.length) {
    const done = sum(live, t => t.photos_done), total = sum(live, t => t.photos_total)
    if (live.every(a => a.transfer!.state === 'stopping'))
      return { kind: 'stopping', text: `${done} / ${total}`, sub: 'stopping…', fraction: frac(live), label: `Devices: stopping, ${done} of ${total} photos on ${names(live)}` }
    const es = live.filter(a => a.transfer!.state === 'running').map(a => estimate(a.transfer!, now))
    const secs = es.every(e => e.kind === 'eta') ? Math.max(...es.map(e => (e as { seconds: number }).seconds)) : null
    return {
      kind: 'running', text: `${done} / ${total}`, sub: secs == null ? 'estimating…' : etaWords(secs), fraction: frac(live),
      label: `Devices: sending ${done} of ${total} photos to ${names(live)}, ${secs == null ? 'estimating the time left' : etaWords(secs, true)}`,
    }
  }
  if (busy.length) {
    const done = sum(busy, t => t.photos_done), total = sum(busy, t => t.photos_total)
    return { kind: 'offline', text: `${done} / ${total}`, sub: `${names(busy)} offline`, fraction: frac(busy), label: `Devices: ${names(busy)} went offline with ${done} of ${total} photos arrived` }
  }
  const stopped = agents.filter(a => a.stopped)
  if (stopped.length) {
    const done = stopped.reduce((n, a) => n + a.stopped!.photos_done, 0), total = stopped.reduce((n, a) => n + a.stopped!.photos_total, 0)
    const f = stopped.length === 1 ? stoppedLines(stopped[0].stopped!, '').fraction : total ? done / total : 0
    return { kind: 'stopped', text: `${done} / ${total}`, sub: 'stopped', fraction: f, label: `Devices: stopped, ${done} of ${total} photos on ${names(stopped)}` }
  }
  const queued = agents.filter(a => a.transfer)
  if (queued.length) {
    const n = sum(queued, t => t.photos_total - t.photos_done)
    return { kind: 'waiting', text: `${n} waiting`, sub: `for ${names(queued)}`, fraction: 0, label: `Devices: ${photos(n)} waiting to be sent to ${names(queued)}` }
  }
  const on = agents.filter(a => a.online).length
  if (!agents.length) return { kind: 'idle', text: 'no device', sub: '', fraction: null, label: 'Devices: none yet' }
  if (agents.length === 1) return { kind: 'idle', text: label(agents[0]), sub: on ? 'online' : 'offline', fraction: null, label: `Devices: ${label(agents[0])} is ${on ? 'online' : 'offline'}` }
  return { kind: 'idle', text: `${on} of ${agents.length}`, sub: 'online', fraction: null, label: `Devices: ${on} of ${agents.length} online` }
}

/** "2 of 3 online" */
export const onlineCount = (agents: Pick<Agent, 'online'>[]) => `${agents.filter(a => a.online).length} of ${agents.length} online`
