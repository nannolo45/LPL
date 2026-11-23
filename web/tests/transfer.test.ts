// SPDX-License-Identifier: GPL-3.0-or-later
// Tests for src/transfer.ts (the estimate, its wording, what the Devices page and the top bar say): npm test.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { deviceCounts, deviceRows, filterCounts, filterRows, ESTIMATE, estimate, estimateWords, etaWords, fmtBytes, isFolded, overall, rollingRate, stoppedLines, transferLines } from '../src/transfer.ts'
import type { StoppedRun, Transfer } from '../src/transfer.ts'
import { hasUnsavedEdits } from '../src/devices.ts'

const MB = 1024 * 1024, GB = 1024 * MB
const T0 = 1_800_000_000
/** One sample every `every` seconds at `rate` bytes/s, a photo every 30 MB; rates: a rate per sample instead. */
function samples(n: number, rate: number | ((i: number) => number), every = 2, start = T0): [number, number, number][] {
  const out: [number, number, number][] = []
  let b = 0
  for (let i = 0; i < n; i++) {
    if (i) b += (typeof rate === 'number' ? rate : rate(i)) * every
    out.push([start + i * every, b, Math.floor(b / (30 * MB))])
  }
  return out
}
const tr = (over: Partial<Transfer> = {}): Transfer => ({
  run: 1, state: 'running', photos_total: 120, photos_done: 38, failed: 0, bytes_total: 4.6 * GB, bytes_done: 1.4 * GB,
  current: null, waiting: [], arrived: [], started: T0, samples: [], can_stop: true, can_skip: true, known: true, ...over,
})
const last = (s: [number, number, number][]) => s[s.length - 1][0]

test('estimate: "estimating…" until there is enough to go on', () => {
  assert.equal(rollingRate([], T0), null)
  assert.equal(rollingRate(samples(1, 5 * MB), T0), null)                      // one report: no rate yet
  const few = samples(4, 5 * MB)                                               // 6 s of reports, no photo done
  assert.equal(rollingRate(few, last(few)), null)
  assert.deepEqual(estimate(tr({ samples: few }), last(few)), { kind: 'estimating' })
  assert.equal(estimateWords(estimate(tr({ samples: few }), last(few))), 'estimating…')
  const enough = samples(5, 5 * MB)                                            // 8 s: several seconds
  assert.equal(rollingRate(enough, last(enough)), 5 * MB)
  // or a few files: small photos on a fast line are enough after 3 of them, even within 4 s
  const quick = samples(3, 50 * MB)                                            // 4 s, 200 MB = 6 photos
  assert.ok(quick[2][2] - quick[0][2] >= ESTIMATE.minPhotos)
  assert.equal(rollingRate(quick, last(quick)), 50 * MB)
  const instant: [number, number, number][] = [[T0, 0, 0], [T0 + 1, 300 * MB, 10]]   // but not from one second
  assert.equal(rollingRate(instant, T0 + 1), null)
})

test('estimate: what is left divided by the rolling rate', () => {
  const s = samples(31, 8 * MB)                                                // a minute at 8 MB/s
  const e = estimate(tr({ samples: s, bytes_total: 4.6 * GB, bytes_done: 1.4 * GB }), last(s))
  assert.equal(e.kind, 'eta')
  if (e.kind === 'eta') { assert.equal(e.rate, 8 * MB); assert.ok(Math.abs(e.seconds - 3.2 * GB / (8 * MB)) < 1e-6) }
  assert.equal(estimateWords(e), 'about 7 min left')
})

test('estimate: smoothed, a hiccup or a burst does not make it jump', () => {
  const steady = samples(31, 8 * MB)
  const base = rollingRate(steady, last(steady))!
  // one report in which nothing moved (the NAS disk spun up)
  const hiccup = samples(31, i => (i === 30 ? 0 : 8 * MB))
  const r1 = rollingRate(hiccup, last(hiccup))!
  assert.ok(Math.abs(r1 - base) / base < 0.05, `a stall of one report moved the rate by ${Math.round(Math.abs(r1 - base) / base * 100)}%`)
  // one report three times as fast
  const burst = samples(31, i => (i === 30 ? 24 * MB : 8 * MB))
  const r2 = rollingRate(burst, last(burst))!
  assert.ok(Math.abs(r2 - base) / base < 0.08)
  // a rate that swings between 4 and 12 MB/s from report to report reads the same from one report to the next
  const swing = samples(40, i => (i % 2 ? 4 * MB : 12 * MB))
  const words = new Set<string>()
  for (let k = 31; k <= 40; k++) {
    const part = swing.slice(0, k)
    const done = part[k - 1][1]
    const e = estimate(tr({ samples: part, bytes_total: done + 3 * GB, bytes_done: done }), last(part))   // 3 GB always left
    words.add(estimateWords(e))
  }
  assert.deepEqual([...words], ['about 6 min left'])
  // only the last minute counts: a slow start is forgotten
  const slowStart = samples(61, i => (i <= 30 ? 1 * MB : 8 * MB))
  assert.equal(rollingRate(slowStart, last(slowStart)), 8 * MB)
})

test('estimate: no fresh reports, no countdown', () => {
  const s = samples(31, 8 * MB)
  assert.notEqual(rollingRate(s, last(s) + ESTIMATE.stale), null)
  assert.equal(rollingRate(s, last(s) + ESTIMATE.stale + 1), null)             // the device went quiet
  const stalled = samples(31, 0)                                               // reports, but nothing moves
  assert.deepEqual(estimate(tr({ samples: stalled }), last(stalled)), { kind: 'estimating' })
  // it ran well, then the line hangs: after ten seconds without a byte no number is shown; before that it is
  const hang = (n: number) => samples(31 + n, i => (i <= 30 ? 8 * MB : 0))
  assert.notEqual(rollingRate(hang(4), last(hang(4))), null)                   // 8 s
  assert.equal(rollingRate(hang(5), last(hang(5))), null)                      // 10 s
  const again = samples(40, i => (i <= 30 || i > 36 ? 8 * MB : 0))             // and it comes back when bytes do
  assert.notEqual(rollingRate(again, last(again)), null)
})

test('estimate: by photos when no sizes are known', () => {
  const s: [number, number, number][] = Array.from({ length: 11 }, (_, i) => [T0 + i * 2, 0, i] as [number, number, number])   // a photo every 2 s
  const e = estimate(tr({ samples: s, bytes_total: 0, bytes_done: 0, photos_total: 130, photos_done: 10 }), last(s))
  assert.equal(e.kind === 'eta' && Math.round(e.seconds), 240)
})

test('estimate: rough words', () => {
  assert.equal(etaWords(0), 'less than a minute left')
  assert.equal(etaWords(49), 'less than a minute left')
  assert.equal(etaWords(50), 'about a minute left')
  assert.equal(etaWords(89), 'about a minute left')
  assert.equal(etaWords(90), 'about 2 min left')
  assert.equal(etaWords(6 * 60 + 10), 'about 6 min left')
  assert.equal(etaWords(9 * 60 + 20), 'about 9 min left')
  assert.equal(etaWords(9.5 * 60), 'about 10 min left')                        // from here in steps of five
  assert.equal(etaWords(12 * 60), 'about 10 min left')
  assert.equal(etaWords(13 * 60), 'about 15 min left')
  assert.equal(etaWords(57 * 60), 'about 55 min left')
  assert.equal(etaWords(58 * 60), 'about an hour left')
  assert.equal(etaWords(89 * 60), 'about an hour left')
  assert.equal(etaWords(90 * 60), 'about 1.5 hours left')
  assert.equal(etaWords(125 * 60), 'about 2 hours left')
  assert.equal(etaWords(5 * 3600), 'about 5 hours left')
  assert.equal(etaWords(6 * 60, true), 'about 6 minutes left')
  assert.equal(etaWords(30, true), 'less than a minute left')
})

test('sizes in words', () => {
  assert.equal(fmtBytes(1.4 * GB), '1.4 GB')
  assert.equal(fmtBytes(850 * MB), '850 MB')
  assert.equal(fmtBytes(3.2 * MB), '3.2 MB')
  assert.equal(fmtBytes(12 * 1024), '12 KB')
  assert.equal(fmtBytes(0), '0 KB')
})

const dev = (over: any = {}) => ({ id: 'pc-1', name: 'Laptop', os: 'linux', online: true, last_seen: T0, state: { working_set: [] }, ...over })

test('the bar of a device that receives', () => {
  const s = samples(31, 8 * MB)
  const now = last(s)
  const a = dev({ last_seen: now })
  const l = transferLines(tr({ samples: s }), 'Laptop', a, now)
  assert.deepEqual([l.title, l.detail, l.eta, l.moving], ['Sending 38 of 120 photos to Laptop', '1.4 GB of 4.6 GB', 'about 7 min left', true])
  assert.ok(Math.abs(l.fraction - 1.4 / 4.6) < 1e-9)
  assert.equal(transferLines(tr(), 'Laptop', a, now).eta, 'estimating…')
  assert.equal(transferLines(tr({ state: 'stopping', samples: s }), 'Laptop', a, now).title, 'Stopping · 38 of 120 photos on Laptop')
  // the device went quiet: the bar must not claim progress (by the flag, or by the clock when the page gets no answers)
  for (const off of [dev({ online: false, last_seen: now }), dev({ last_seen: now - 15 })]) {
    const o = transferLines(tr({ samples: s }), 'Laptop', off, now)
    assert.equal(o.title, 'Laptop is offline · 38 of 120 photos arrived')
    assert.equal(o.eta, null); assert.equal(o.moving, false)
  }
  // not taken yet
  const q = tr({ state: 'queued', photos_total: 5, photos_done: 0 })
  assert.equal(transferLines(q, 'Telefono', dev({ os: 'android', online: false }), now).title, '5 photos waiting to be sent to Telefono')
  assert.match(transferLines(q, 'Telefono', dev({ os: 'android', online: false }), now).detail, /next time the darktable app is opened on Telefono/)
  assert.match(transferLines(q, 'Desktop', dev({ os: 'windows', online: false }), now).detail, /as soon as Desktop is online/)
})

const stopped = (over: Partial<StoppedRun> = {}): StoppedRun => ({
  run: 1, reason: 'user', time: T0, photos_total: 120, photos_done: 38, failed: 0, bytes_done: 1.4 * GB, bytes_total: 4.6 * GB,
  unsent: Array.from({ length: 82 }, (_, i) => ({ id: 100 + i, filename: `F${i}.RAF`, job: 1 })), arrived: [], ...over,
})

test('the stopped state', () => {
  const l = stoppedLines(stopped(), 'Laptop')
  assert.equal(l.title, 'Stopped · 38 of 120 on Laptop')
  assert.equal(l.detail, '1.4 GB copied. The other 82 photos were not sent; nothing was lost.')
  assert.equal(stoppedLines(stopped({ reason: 'interrupted' }), 'Laptop').detail, 'Laptop was restarted while it was receiving. The other 82 photos were not sent; nothing was lost.')
  assert.match(stoppedLines(stopped({ unsent: [{ id: 1, filename: 'a', job: 1 }] }), 'Laptop').detail, /The other 1 photo was not sent/)
})

const w = (id: number, status: string, over: any = {}) => ({ id, filename: `DSCF${id}.RAF`, status, checkout_ts: T0 + id, ...over })

test('every status that means unsaved edits counts', () => {
  for (const st of ['dirty', 'editing', 'edited', 'Edited', 'modified', 'waiting to sync']) assert.equal(hasUnsavedEdits({ status: st }), true, st)
  assert.equal(hasUnsavedEdits({ status: 'error', error: 'sync failed: timed out' }), true)   // the edit is still only on the device
  for (const st of ['synced', 'ready', 'downloading', 'opening', 'syncing']) assert.equal(hasUnsavedEdits({ status: st }), false, st)
  assert.equal(hasUnsavedEdits({ status: 'error', error: 'HTTP Error 404' }), false)
})

test('the line next to a device name', () => {
  const ws = [w(1, 'dirty'), w(2, 'edited'), w(3, 'editing'), ...[4, 5, 6, 7, 8, 9, 10, 11, 12].map(i => w(i, 'synced'))]
  assert.equal(deviceCounts(dev({ state: { working_set: ws } })), '12 photos · 3 edited, not saved yet')
  const arrived = Array.from({ length: 38 }, (_, i) => w(i, 'ready'))
  assert.equal(deviceCounts(dev({ state: { working_set: [...arrived, w(99, 'downloading')] }, transfer: tr() })), '38 photos · receiving 82 more')
  assert.equal(deviceCounts(dev({ state: { working_set: arrived }, stopped: stopped() })), '38 photos · sending stopped')
  assert.equal(deviceCounts(dev({ state: { working_set: [1, 2, 3, 4].map(i => w(i, 'synced')) } })), '4 photos · all saved to the NAS')
  assert.equal(deviceCounts(dev()), 'no photos')
  assert.equal(deviceCounts(dev({ state: { working_set: [w(1, 'ready')] } })), '1 photo')
  assert.equal(deviceCounts(dev({ transfer: tr({ state: 'queued', photos_total: 5, photos_done: 0 }) })), 'no photos · 5 waiting to be sent')
})

test('folded: his choice, else by what the device is doing', () => {
  assert.equal(isFolded(dev({ online: false, state: { working_set: [w(1, 'synced')] } })), true)   // offline
  assert.equal(isFolded(dev()), true)                                                             // nothing on it
  assert.equal(isFolded(dev({ transfer: tr() })), false)                                          // receiving
  assert.equal(isFolded(dev({ state: { working_set: [w(1, 'dirty')] } })), false)
  assert.equal(isFolded(dev({ stopped: stopped() })), false)
  assert.equal(isFolded(dev({ online: false, transfer: tr() })), false)                           // went offline while receiving: stays open
  assert.equal(isFolded(dev({ transfer: tr(), folded: true })), true)                             // he folded it: it stays folded
  assert.equal(isFolded(dev({ online: false, folded: false })), false)
})

test('rows of a device: copying, waiting, arrived, not sent', () => {
  const waiting = [201, 202, 203].map(id => ({ id, filename: `DSCF${id}.RAF`, job: 7 }))
  const ws = [w(1, 'ready'), w(2, 'dirty'), w(3, 'synced'), w(4, 'error', { error: 'x' }), w(200, 'downloading', { step: 'downloading', progress: .62 })]
  const r = deviceRows(dev({ state: { working_set: ws }, transfer: tr({ waiting: [...waiting, { id: 3, filename: 'again', job: 7 }], current: { id: 200, filename: 'DSCF200.RAF', bytes: 6, size: 10 } }) }))
  assert.deepEqual(r.copying.map(x => x.id), [200])                    // once, though the server names it too
  assert.deepEqual(r.waiting.map(x => x.id), [201, 202, 203])          // not one that is on the device already
  assert.deepEqual(r.arrived.map(x => x.id), [2, 3, 1])                // the unsaved edit, then the latest first
  assert.deepEqual(r.failed.map(x => [x.id, x.error]), [[4, 'x']])     // a copy that failed is not "on the device"
  assert.deepEqual(r.unsent, [])
  // the device has not listed the photo it copies yet: the server's word is shown
  const early = deviceRows(dev({ transfer: tr({ current: { id: 200, filename: 'DSCF200.RAF', bytes: 5, size: 10 } }) }))
  assert.deepEqual(early.copying.map(x => [x.id, x.status, x.progress]), [[200, 'downloading', .5]])
  assert.equal(deviceRows(dev({ stopped: stopped() })).unsent.length, 82)
})

test('top bar: what the indicator says', () => {
  const s = samples(31, 8 * MB), now = last(s)
  const desktop = dev({ id: 'pc-0', name: 'Desktop', os: 'windows', last_seen: now })
  const phone = dev({ id: 'ph', name: 'Telefono', os: 'android', online: false })
  const laptop = dev({ last_seen: now, transfer: tr({ samples: s }) })
  const o = overall([desktop, laptop, phone], now)
  assert.deepEqual([o.kind, o.text, o.sub], ['running', '38 / 120', 'about 7 min left'])
  assert.equal(o.label, 'Devices: sending 38 of 120 photos to Laptop, about 7 minutes left')
  assert.ok(Math.abs(o.fraction! - 1.4 / 4.6) < 1e-9)
  assert.equal(overall([desktop, dev({ last_seen: now, transfer: tr() }), phone], now).sub, 'estimating…')
  assert.deepEqual(pick(overall([desktop, dev({ last_seen: now, transfer: tr({ state: 'stopping' }) })], now)), ['stopping', '38 / 120', 'stopping…'])
  // two devices receiving: added up, the longer time left
  const both = overall([dev({ id: 'pc-0', name: 'Desktop', last_seen: now, transfer: tr({ samples: s, photos_total: 10, photos_done: 4, bytes_total: 1 * GB, bytes_done: 0.9 * GB }) }), laptop], now)
  assert.deepEqual(pick(both), ['running', '42 / 130', 'about 7 min left'])
  assert.match(both.label, /to 2 devices/)
  // the device went offline in the middle
  assert.deepEqual(pick(overall([desktop, dev({ online: false, transfer: tr() })], now)), ['offline', '38 / 120', 'Laptop offline'])
  // stopped; then nothing: who is online
  const st = overall([desktop, dev({ last_seen: now, stopped: stopped() }), phone], now)
  assert.deepEqual(pick(st), ['stopped', '38 / 120', 'stopped'])
  assert.equal(st.label, 'Devices: stopped, 38 of 120 photos on Laptop')
  assert.deepEqual(pick(overall([desktop, phone, dev({ transfer: tr({ state: 'queued', photos_total: 5, photos_done: 0 }), last_seen: now })], now)), ['waiting', '5 waiting', 'for Laptop'])
  const idle = overall([desktop, dev({ last_seen: now }), phone], now)
  assert.deepEqual([idle.kind, idle.text, idle.sub, idle.fraction], ['idle', '2 of 3', 'online', null])
  assert.deepEqual(pick(overall([desktop], now)), ['idle', 'Desktop', 'online'])
  assert.deepEqual(pick(overall([], now)), ['idle', 'no device', ''])
})
const pick = (o: { kind: string; text: string; sub: string }) => [o.kind, o.text, o.sub]

test('photos that could not be copied: the device\'s word and the server\'s, each once; never one with edits', () => {
  const ws = [w(1, 'ready'), w(2, 'error', { error: 'timed out', copy_failed: true }), w(3, 'error', { error: 'sync failed: x' })]
  const r = deviceRows(dev({ state: { working_set: ws }, failed: [{ id: 2, filename: 'b', job: 4 }, { id: 9, filename: 'DSCF9.RAF', job: 4 }, { id: 1, filename: 'a', job: 3 }] }))
  assert.deepEqual(r.failed.map(x => x.id), [2, 9])                     // 1 arrived since: not failed any more
  assert.deepEqual(r.arrived.map(x => x.id), [3, 1])                    // a failed sync keeps its edits: it is on the device
  assert.equal(isFolded(dev({ online: true, failed: [{ id: 9, filename: 'x', job: 1 }] })), false)
})

test('filter chips: counts and rows', () => {
  const ws = [w(1, 'ready'), w(2, 'dirty'), w(3, 'synced'), w(4, 'error', { error: 'x' }), w(200, 'downloading')]
  const r = deviceRows(dev({ state: { working_set: ws }, transfer: tr({ waiting: [{ id: 201, filename: 'w', job: 7 }] }) }))
  assert.deepEqual(filterCounts(r), { all: 6, failed: 1, sending: 2, ready: 1, unsaved: 1, saved: 1, unsent: 0 })
  assert.deepEqual(filterRows(r, 'unsaved').arrived.map(x => x.id), [2])
  assert.deepEqual(filterRows(r, 'sending').waiting.map(x => x.id), [201])
  assert.equal(filterRows(r, 'failed').arrived.length, 0)
  assert.equal(filterRows(r, 'all'), r)
})
