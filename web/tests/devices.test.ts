// SPDX-License-Identifier: GPL-3.0-or-later
// Tests for src/devices.ts with Node's own test runner, no extra packages: npm test (Node 22.6 or newer).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { agentLabel, defaultAgent, devicePills, inShell, isAndroidBrowser, needsDeviceChoice, parseDeepLink, phoneEditLink, photoWhere, selectionIds } from '../src/devices.ts'

const origin = 'http://192.0.2.10:8080'  // documentation address (RFC 5737)
const tail = '#Intent;scheme=lpldt;package=app.dtandroid;S.browser_fallback_url=http%3A%2F%2F192.0.2.10%3A8080%2Fandroid-app.html;end'

test('link: the exact format', () => {
  assert.equal(phoneEditLink({ origin, ids: [12, 7, 31], start: 7, series: 4, title: 'Lake & hills/2024' }),
    'intent://edit?server=http%3A%2F%2F192.0.2.10%3A8080&ids=12,7,31&start=7&series=4&title=Lake%20%26%20hills%2F2024' + tail)
})

test('link: series and title only when known', () => {
  assert.equal(phoneEditLink({ origin, ids: [1, 2], start: 1 }), 'intent://edit?server=http%3A%2F%2F192.0.2.10%3A8080&ids=1,2&start=1' + tail)
  assert.equal(phoneEditLink({ origin, ids: [1], start: 1, series: null, title: '' }), 'intent://edit?server=http%3A%2F%2F192.0.2.10%3A8080&ids=1&start=1' + tail)
})

const idsOf = (link: string) => new URLSearchParams(link.slice('intent://edit?'.length, link.indexOf('#'))).get('ids')!.split(',').map(Number)

test('link: at most 200 photos, the ones around start', () => {
  const all = Array.from({ length: 1000 }, (_, i) => i + 1)
  const mid = idsOf(phoneEditLink({ origin, ids: all, start: 500 }))
  assert.equal(mid.length, 200); assert.equal(mid[0], 400); assert.equal(mid[199], 599)
  const first = idsOf(phoneEditLink({ origin, ids: all, start: 3 }))
  assert.equal(first.length, 200); assert.equal(first[0], 1)
  const last = idsOf(phoneEditLink({ origin, ids: all, start: 999 }))
  assert.equal(last.length, 200); assert.equal(last[199], 1000); assert.ok(last.includes(999))
  assert.deepEqual(idsOf(phoneEditLink({ origin, ids: all.slice(0, 200), start: 5 })), all.slice(0, 200))
})

test('link: start not in the list is put first', () => {
  assert.deepEqual(idsOf(phoneEditLink({ origin, ids: [1, 2], start: 9 })), [9, 1, 2])
})

test('link: no password or cookie', () => {
  const l = phoneEditLink({ origin, ids: [1], start: 1 })
  assert.ok(!/password|token|cookie/i.test(l))
})

test('selection: only the selected photos, in display order', () => {
  assert.deepEqual(selectionIds([5, 3, 9, 1, 7], [1, 3]), [3, 1])
  assert.deepEqual(selectionIds([5, 3, 9], [9, 42, 5, 17]), [5, 9, 42, 17])   // not in the loaded list: after the known ones
  assert.deepEqual(selectionIds([5, 3], [3, 3]), [3])
  assert.deepEqual(selectionIds([], []), [])
  const all = Array.from({ length: 1000 }, (_, i) => i + 1)
  const sel = selectionIds(all, all.slice(0, 300).reverse())
  assert.equal(sel.length, 300); assert.equal(sel[0], 1)
  const ids = idsOf(phoneEditLink({ origin, ids: sel, start: sel[0] }))       // more than 200: the existing rule
  assert.equal(ids.length, 200); assert.equal(ids[0], 1); assert.equal(ids[199], 200)
})

test('Android browsers', () => {
  assert.ok(isAndroidBrowser('Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36'))
  assert.ok(isAndroidBrowser('Mozilla/5.0 (Android 14; Mobile; rv:132.0) Gecko/132.0 Firefox/132.0'))
  assert.ok(!isAndroidBrowser('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'))
  const wv = 'Mozilla/5.0 (Linux; Android 14; Pixel 8; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/130.0 Mobile Safari/537.36'
  assert.ok(isAndroidBrowser(wv))
  assert.ok(isAndroidBrowser(wv + ' LPLShell/0.1.0'))
  assert.ok(!isAndroidBrowser('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148'))
})

const desk = { id: 'pc-aaaa1111', name: 'Desktop', os: 'windows', online: true }
const lap = { id: 'pc-bbbb2222', name: 'Laptop', os: 'linux', online: false }
const phone = { id: 'android-cccc3333', name: 'Phone', os: 'android', online: true }

test('inside the LPL shell', () => {
  const wv = 'Mozilla/5.0 (Linux; Android 14; Pixel 8; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/130.0 Mobile Safari/537.36'
  assert.ok(inShell(wv + ' LPLShell/0.1.0'))
  assert.ok(!inShell(wv))
  assert.ok(!inShell('Mozilla/5.0 (Android 14; Mobile; rv:132.0) Gecko/132.0 Firefox/132.0'))
})

test('device names: as set on the device, told apart when equal', () => {
  assert.equal(agentLabel(desk, [desk, lap, phone]), 'Desktop')
  const p2 = { id: 'android-dddd4444', name: 'Desktop', os: 'android' }
  assert.equal(agentLabel(desk, [desk, p2]), 'Desktop (windows)')
  assert.equal(agentLabel(p2, [desk, p2]), 'Desktop (android)')
  const d2 = { id: 'pc-eeee5555', name: 'Desktop', os: 'windows' }
  assert.equal(agentLabel(desk, [desk, d2]), 'Desktop (windows, pc-aaaa1)')
  assert.equal(agentLabel(d2, [desk, d2]), 'Desktop (windows, pc-eeee5)')
  assert.equal(agentLabel({ id: 'x1', name: '', os: 'linux' }, []), 'x1')
})

test('which device is suggested', () => {
  assert.equal(defaultAgent([phone, lap, desk], null)?.id, desk.id)            // an online PC
  assert.equal(defaultAgent([phone, lap], null)?.id, lap.id)                   // an offline PC before a phone
  assert.equal(defaultAgent([phone, lap, desk], phone.id)?.id, phone.id)       // the last choice
  assert.equal(defaultAgent([lap, desk], 'gone')?.id, desk.id)                 // last choice no longer known
  assert.equal(defaultAgent([phone], null)?.id, phone.id)
  assert.equal(defaultAgent([], null), null)
})

test('when to ask for the device', () => {
  assert.equal(needsDeviceChoice([]), false)
  assert.equal(needsDeviceChoice([desk]), false)        // one PC: as before
  assert.equal(needsDeviceChoice([phone]), true)        // never pick a phone without asking
  assert.equal(needsDeviceChoice([desk, lap]), true)
})

test('where a photo is', () => {
  const ag = [
    { ...desk, state: { working_set: [{ id: 5, filename: 'a.raf', status: 'editing' }] } },
    { ...phone, state: { working_set: [{ id: 5, filename: 'a.raf', status: 'dirty' }, { id: 6, filename: 'b.raf', status: 'downloading' }] } },
    { ...lap, state: {} },
  ]
  assert.deepEqual(photoWhere(ag, 5), ['on Desktop: open in darktable', 'on Phone: edited · not synced yet'])
  assert.deepEqual(photoWhere(ag, 6), ['on Phone: copying from the NAS'])
  assert.deepEqual(photoWhere(ag, 7), [])
  assert.deepEqual(photoWhere([{ ...phone, state: { working_set: [{ id: 1, filename: 'x', status: 'new-one' }] } }], 1), ['on Phone: new-one'])
})

test('deep links', () => {
  assert.deepEqual(parseDeepLink('?photo=42'), { photo: 42, series: undefined })
  assert.deepEqual(parseDeepLink('?series=3&x=1'), { photo: undefined, series: 3 })
  assert.deepEqual(parseDeepLink('?photo=abc'), { photo: 0, series: undefined })
  assert.equal(parseDeepLink(''), null)
  assert.equal(parseDeepLink('?x=1'), null)
})

test('default device: recency decides among equals, not the order of the list', () => {
  const pc = (id: string, online: boolean, last_seen: number) => ({ id, os: 'windows', online, last_seen })
  const older = pc('pc-a', false, 100), newer = pc('pc-b', false, 200)
  assert.equal(defaultAgent([older, newer], null)?.id, 'pc-b')   // the PC heard from last, wherever it is in the list
  assert.equal(defaultAgent([newer, older], null)?.id, 'pc-b')
  const on1 = pc('pc-c', true, 300), on2 = pc('pc-d', true, 310)
  assert.equal(defaultAgent([on1, on2], null)?.id, 'pc-d')
  assert.equal(defaultAgent([on2, on1], null)?.id, 'pc-d')
})

test('device card: a phone is described as a phone', () => {
  const phone = (online: boolean, darkroom: boolean) => ({ os: 'android', online, state: { darktable_running: darkroom, bridge_alive: true } })
  assert.deepEqual(devicePills(phone(true, false), '1 min ago'), [[true, 'online · app open'], [false, 'no photo open']])
  assert.deepEqual(devicePills(phone(true, true), '1 min ago'), [[true, 'online · app open'], [true, 'photo open in the darkroom']])
  assert.deepEqual(devicePills(phone(false, true), '3 min ago'), [[false, 'app not open · seen 3 min ago']])
  assert.ok(!devicePills(phone(true, true), '').some(([, t]) => /bridge|darktable running/.test(t)))
})

test('device card: a PC as before', () => {
  const pc = (online: boolean, dt: boolean, bridge: boolean) => ({ os: 'windows', online, state: { darktable_running: dt, bridge_alive: bridge } })
  assert.deepEqual(devicePills(pc(true, true, true), ''), [[true, 'online'], [true, 'darktable running'], [true, 'bridge script active']])
  assert.deepEqual(devicePills(pc(true, false, false), ''), [[true, 'online'], [false, 'darktable not running']])
  assert.deepEqual(devicePills(pc(true, true, false), ''), [[true, 'online'], [true, 'darktable running'], [false, 'bridge script not seen']])
  assert.deepEqual(devicePills(pc(false, true, true), '5s ago'), [[false, 'offline · seen 5s ago']])
})
