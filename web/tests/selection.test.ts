import { test } from 'node:test'
import assert from 'node:assert/strict'
import { boxState, bulkActions, prune, rangeIds, toggleGroup, toggleId } from '../src/selection.ts'
import type { SelRow } from '../src/selection.ts'

const order = [1, 2, 3, 4, 5, 6, 7, 8]
test('toggle', () => {
  assert.deepEqual(toggleId([1], 2), [1, 2])
  assert.deepEqual(toggleId([1, 2], 1), [2])
})
test('range goes across hidden rows, both directions, keeps earlier picks', () => {
  assert.deepEqual(rangeIds(order, [2], 2, 6), [2, 3, 4, 5, 6])
  assert.deepEqual(rangeIds(order, [6], 6, 3).sort(), [3, 4, 5, 6])
  assert.deepEqual(rangeIds(order, [1, 7], 7, 5).sort(), [1, 5, 6, 7])
})
test('range without a usable anchor selects the clicked row only', () => {
  assert.deepEqual(rangeIds(order, [], null, 4), [4])
  assert.deepEqual(rangeIds(order, [], 99, 4), [4])
})
test('header box states', () => {
  assert.equal(boxState(order, []), 'none')
  assert.equal(boxState(order, [3, 99]), 'some')
  assert.equal(boxState(order, order), 'all')
  assert.equal(boxState([], []), 'none')
})
test('group select all and back', () => {
  assert.deepEqual(toggleGroup([1], [4, 5, 6]), [1, 4, 5, 6])
  assert.deepEqual(toggleGroup([1, 4, 5, 6], [4, 5, 6]), [1])
  assert.deepEqual(toggleGroup([4], [4, 5]), [4, 5])
})
test('prune drops what left, keeps identity when nothing did', () => {
  const s = [1, 2]
  assert.equal(prune(s, order), s)
  assert.deepEqual(prune([1, 20], order), [1])
})
const rows: SelRow[] = [
  { id: 1, kind: 'copying', droppable: false }, { id: 2, kind: 'waiting', droppable: true }, { id: 3, kind: 'waiting', droppable: false },
  { id: 4, kind: 'arrived', unsaved: true }, { id: 5, kind: 'arrived' },
]
test('actions: each counts only what it applies to', () => {
  const b = bulkActions(rows, [1, 2, 3, 4, 5])
  assert.deepEqual(b.drop, [2]); assert.equal(b.stuck, 2)
  assert.deepEqual(b.open, [4, 5]); assert.deepEqual(b.save, [4]); assert.deepEqual(b.done, [4, 5])
  assert.equal(b.show.length, 5)
})
test('actions: only waiting selected offers no device actions', () => {
  const b = bulkActions(rows, [2])
  assert.deepEqual(b.drop, [2]); assert.equal(b.open.length + b.save.length + b.done.length, 0)
})
test('actions: copying photo counts when the device can give it up', () => {
  const b = bulkActions([{ id: 1, kind: 'copying', droppable: true }], [1])
  assert.deepEqual(b.drop, [1])
})
test('actions: on the device with nothing unsaved has no Save', () => {
  const b = bulkActions(rows, [5])
  assert.equal(b.save.length, 0); assert.deepEqual(b.done, [5])
})
