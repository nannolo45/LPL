// SPDX-License-Identifier: GPL-3.0-or-later
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { conflictChoices, kindText, overviewItems, updateLabel, updateNote, updatePayload, waitingLine, whatText } from '../src/shared.ts'

const w = (id: number, what: 'new' | 'changed' | 'deleted') => ({ id, what })
test('device line: PCs count what waits, the phone updates from the app', () => {
  assert.equal(waitingLine({ phone: false, waiting: [w(1, 'new') as any, w(2, 'changed')], conflicts: [{} as any] }), '3 styles, presets, layouts waiting')
  assert.equal(waitingLine({ phone: false, waiting: [], conflicts: [] }), 'styles, presets, layouts up to date')
  assert.equal(waitingLine({ phone: true, waiting: [w(1, 'new') as any], conflicts: [] }), '1 waiting · updated from the app')
})
test('kind and what', () => {
  assert.equal(kindText({ kind: 'preset', operation: 'exposure' }), 'preset · exposure')
  assert.equal(kindText({ kind: 'layout', operation: 'modulegroups' }), 'module layout')
  assert.equal(whatText({ what: 'deleted', device_name: 'Laptop', device_id: 'x' }), 'deleted on Laptop')
  assert.equal(whatText({ what: 'changed', device_name: 'Laptop', device_id: 'x' }), 'changed')
})
test('update payload: ticked items; deletions deleted unless kept', () => {
  const p = updatePayload('pc', [w(1, 'new'), w(2, 'deleted'), w(3, 'deleted'), w(4, 'changed')], new Set([1, 2, 3]), { 3: 'keep' })
  assert.deepEqual(p, { agent: 'pc', ids: [1], delete: [2], keep: [3] })
  assert.equal(updateLabel('Desktop', 2), 'Update Desktop · 2')
})
test('conflict choices: keep both only when both sides have content; the copy name', () => {
  const c = { name: 'mono', deleted: false, device_name: 'Phone', device_id: 'p', candidate: { deleted: false, changed_at: 1, hash: 'h', base_rev: 1 } }
  const ch = conflictChoices('Desktop', c)
  assert.deepEqual(ch.map(x => x.label), ["Keep Desktop's", "Take Phone's", 'Keep both'])
  assert.match(ch[2].hint, /mono \(Phone\)/)
  assert.equal(conflictChoices('Desktop', { ...c, candidate: { ...c.candidate, deleted: true } })[2].ok, false)
})
test('note says darktable must be closed', () => {
  assert.match(updateNote({ name: 'Desktop', darktable_running: true, update: null, job: null, online: true }), /close darktable/)
  assert.match(updateNote({ name: 'Desktop', darktable_running: false, update: null, job: null, online: true }), /must be closed/)
  assert.match(updateNote({ name: 'Desktop', darktable_running: true, update: 'waiting_for_darktable', job: { id: 1, status: 'taken' }, online: true }), /when you close darktable/)
})
test('overview hides deletions nobody is offered any more', () => {
  const items = [{ id: 1, deleted: true, devices: { a: 'absent' } }, { id: 2, deleted: true, devices: { a: 'waiting' } }, { id: 3, deleted: false, devices: {} }] as any
  assert.deepEqual(overviewItems({ items }).map((i: any) => i.id), [2, 3])
})
