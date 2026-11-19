// SPDX-License-Identifier: GPL-3.0-or-later
// Tests for the last-edit line, the list of edits (src/edits.ts) and the progress bar's logic (src/devices.ts):
// npm test (Node 22.6 or newer). Times are checked in UTC.
process.env.TZ = 'UTC'
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { allModules, changeLines, editWhere, entryWhen, fmtWhen, lastEditLine } from '../src/edits.ts'
import { itemProgress, photoActivity, serverNow } from '../src/devices.ts'
import type { EditEvent, EditModule } from '../src/api.ts'

const T = Date.UTC(2026, 10, 18, 14, 5) / 1000            // Wed 18 Nov 2026 14:05 UTC
const mod = (operation: string, name = operation, instance = '', enabled = true): EditModule =>
  ({ operation, name, instance, priority: instance ? 1 : 0, enabled, label: instance ? `${name} ${instance}` : name })
const ev = (o: Partial<EditEvent>): EditEvent => ({
  id: 1, photo_id: 3, time: T, source: 'agent', agent_id: 'pc-1', device: 'Desktop', device_os: 'windows',
  modules: [mod('exposure'), mod('colorbalancergb', 'color balance rgb')], changes: null, summary: 'exposure, color balance rgb', ...o,
})
const none = { added: [], removed: [], switched_on: [], switched_off: [], changed: [] }

test('time: today, yesterday, this year, other years', () => {
  assert.equal(fmtWhen(T, T + 3600), 'today 14:05')
  assert.equal(fmtWhen(T, T + 86400), 'yesterday 14:05')
  assert.equal(fmtWhen(T, T + 20 * 86400), '18 Nov 14:05')
  assert.equal(fmtWhen(T, T + 400 * 86400), '18 Nov 2026')
})

test('last-edit line: device, time, modules', () => {
  assert.equal(lastEditLine(ev({}), T + 20 * 86400), 'last edited on Desktop · 18 Nov 14:05 · exposure, color balance rgb')
  assert.equal(lastEditLine(ev({ device: 'Phone', device_os: 'android', agent_id: 'android-1', summary: 'exposure' }), T + 60),
    'last edited on Phone · today 14:05 · exposure')
})

test('last-edit line: where the edit came from', () => {
  assert.equal(editWhere(ev({ source: 'outside', device: null, agent_id: null })), 'outside LightPhotoLibrary')
  assert.equal(editWhere(ev({ source: 'import', device: null, agent_id: null })), 'in a local library on a PC')
  assert.equal(editWhere(ev({ source: 'local', device: null, agent_id: null })), 'in darktable on this PC')
  assert.equal(editWhere(ev({ device: null, agent_id: null })), 'on a device')  // an old agent, not known which
  // a device LPL never saw: its id; one it knows now: its current name
  assert.equal(editWhere(ev({ device: null, agent_id: 'android-x' })), 'on android-x')
  assert.equal(editWhere(ev({ device: null, agent_id: 'pc-1' }), [{ id: 'pc-1', name: 'Laptop', os: 'linux' }]), 'on Laptop')
  // the name it had then, told apart from another device with that name
  assert.equal(editWhere(ev({}), [{ id: 'pc-2', name: 'Desktop', os: 'linux' }]), 'on Desktop (windows)')
})

test('list of edits: one line per kind of change', () => {
  const c = { ...none, changed: [mod('exposure')], added: [mod('exposure', 'exposure', '1')], switched_off: [mod('vignette')], removed: [mod('grain')] }
  assert.deepEqual(changeLines(ev({ changes: c })), ['changed: exposure', 'added: exposure 1', 'switched off: vignette', 'removed: grain'])
  assert.deepEqual(changeLines(ev({ changes: { ...none, switched_on: [mod('flip')] } })), ['switched on: flip'])
  assert.deepEqual(changeLines(ev({ changes: none })), ['no module changed'])
})

test('list of edits: nothing to compare with, or no history', () => {
  assert.deepEqual(changeLines(ev({ modules: [mod('exposure'), mod('vignette', 'vignette', '', false)] })),
    ['modules in use: exposure', 'off: vignette'])
  assert.deepEqual(changeLines(ev({ modules: [] })), ['no darktable edit'])
})

const auto = (m: EditModule): EditModule => ({ ...m, auto: true })

test('list of edits: what darktable applied by itself is left out, and stays under "all modules"', () => {
  const opened = [auto(mod('rawprepare', 'raw black/white point')), auto(mod('exposure', 'exposure', 'scene-referred default')),
    auto(mod('mask_manager', 'mask_manager', '', false))]
  assert.deepEqual(changeLines(ev({ modules: opened })), ["darktable's default edit"])
  assert.deepEqual(changeLines(ev({ modules: [...opened, mod('colorbalancergb', 'color balance rgb')] })), ['modules in use: color balance rgb'])
  assert.deepEqual(allModules(ev({ modules: [...opened, mod('vignette', 'vignette', '', false)] })),
    ['raw black/white point · automatic', 'exposure scene-referred default · automatic', 'mask_manager · automatic · off', 'vignette · off'])
  // the server's summary is shown as it comes
  assert.equal(lastEditLine(ev({ modules: opened, summary: "darktable's default edit" }), T + 60),
    "last edited on Desktop · today 14:05 · darktable's default edit")
})

test('list of edits: several saves of one day are one entry', () => {
  assert.equal(entryWhen({ time: T }, T + 60), 'today 14:05')
  assert.equal(entryWhen({ time: T, first_time: T - 3 * 3600 - 300, saves: 5 }, T + 60), 'today 11:00–14:05 · 5 saves')
  assert.equal(entryWhen({ time: T, first_time: T - 600, saves: 2 }, T + 20 * 86400), '18 Nov 13:55–14:05 · 2 saves')
  assert.equal(entryWhen({ time: T, saves: 1, first_time: T }, T + 60), 'today 14:05')
  assert.equal(entryWhen({ time: T + 20, first_time: T, saves: 3 }, T + 60), 'today 14:05 · 3 saves')  // all in one minute  // a server that sends them for one save
})

// ---------------------------------------------------------------- progress bar (R16)
const phone = (ws: any[], o: any = {}) => ({ id: 'android-1', name: 'Phone', os: 'android', online: true, last_seen: T, state: { working_set: ws }, ...o })
const it = (o: any = {}) => ({ id: 7, filename: 'A.RAF', status: 'downloading', ...o })

test('progress: shown while a device downloads or syncs', () => {
  assert.deepEqual(itemProgress(phone([]), it({ step: 'downloading', progress: 0.42 }), T + 2), { step: 'downloading', fraction: 0.42 })
  assert.deepEqual(itemProgress(phone([]), it({ step: 'sending edit' }), T + 2), { step: 'sending the edit', fraction: null })
  assert.deepEqual(itemProgress(phone([]), it({ step: 'rendering preview', progress: 3 })), { step: 'rendering the preview', fraction: 1 })
  assert.deepEqual(photoActivity([phone([it({ step: 'sending preview', progress: 0.5 })])], 7, T + 2),
    [{ text: 'on Phone: sending the preview', progress: 0.5 }])
})

test('progress: goes away when the step ends', () => {
  assert.equal(itemProgress(phone([]), it({ status: 'opening' }), T + 2), null)
  assert.deepEqual(photoActivity([phone([it({ status: 'synced' })])], 7, T + 2), [{ text: 'on Phone: synced to NAS' }])
})

test('progress: goes away when the device stops reporting', () => {
  const w = it({ step: 'downloading', progress: 0.3 })
  assert.equal(itemProgress(phone([], { online: false }), w, T + 2), null)       // the server says offline
  assert.equal(itemProgress(phone([]), w, T + 15), null)                        // no heartbeat for 15 s by the server's clock
  assert.notEqual(itemProgress(phone([]), w, T + 14), null)
  // the page stopped getting answers: the server's clock is followed from the last answer
  const status = { server_time: T, received_at: 1000 }
  assert.equal(serverNow(status, 1020), T + 20)
  assert.equal(itemProgress(phone([]), w, serverNow(status, 1020)), null)
  assert.deepEqual(photoActivity([phone([w])], 7, serverNow(status, 1020)), [{ text: 'on Phone: copying from the NAS' }])
})

test('progress: agents that do not send it', () => {
  assert.equal(itemProgress(phone([]), it({ status: 'syncing' }), T), null)
  assert.deepEqual(photoActivity([phone([it({ status: 'dirty' })])], 7, T), [{ text: 'on Phone: edited · not synced yet' }])
})
