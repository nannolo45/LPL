// SPDX-License-Identifier: GPL-3.0-or-later
// Tests for the Series view's plain logic (src/seriesnav.ts): npm test.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { buildTree, dropBefore, firstTarget, keyOf, movedLanes, num2, numAtSlot, renumber, resolveTarget, slotAt, targetName, targetOf } from '../src/seriesnav.ts'

const S = (id: number, name: string, folder_id: number | null = null, pos: number | null = null) => ({ id, name, count: 0, covers: [], folder_id, pos })
const F = (id: number, name: string, pos: number | null = null) => ({ id, name, pos, linked: true, count: 0, series_count: 0 })
const P = (id: number, extra: object = {}) => ({ id, filename: 'p' + id, ...extra }) as any
const L = (key: string, ids: number[], extra: object = {}) => ({ key, series: { id: +key.slice(1), name: key, color: '' }, label: key, color: '', maybe: 0, frame: true, photos: ids.map(i => P(i)), ...extra }) as any

test('what a tab shows: a series id, or minus a folder id (saved tabs from before folders keep working)', () => {
  assert.deepEqual(targetOf(7), { kind: 'series', id: 7 })
  assert.deepEqual(targetOf(-3), { kind: 'folder', id: 3 })
  assert.equal(targetOf(null), null)
  assert.equal(targetOf(0), null)
  assert.equal(keyOf({ kind: 'folder', id: 3 }), -3)
  assert.equal(keyOf({ kind: 'series', id: 7 }), 7)
})

test('tree: folders with their series in their order, then the top level', () => {
  const series = [S(1, 'Traghetti', 10, 4), S(2, 'Atene', 10, 1), S(3, 'Portraits'), S(4, 'Berlin'), S(5, 'Naxos', 10, 2), S(6, 'Costa', 11, 1), S(7, 'Lost', 99)]
  const t = buildTree(series, [F(11, 'Sardegna', 2), F(10, 'Grecia', 1)])
  assert.deepEqual(t.folders.map(f => [f.folder.name, f.series.map(x => x.name)]), [['Grecia', ['Atene', 'Naxos', 'Traghetti']], ['Sardegna', ['Costa']]])
  assert.deepEqual(t.top.map(x => x.name), ['Berlin', 'Lost', 'Portraits'])  // a series whose folder is unknown is not lost: top level
  assert.deepEqual(firstTarget(t), { kind: 'folder', id: 10 })
  assert.deepEqual(firstTarget(buildTree([S(3, 'Portraits')], [])), { kind: 'series', id: 3 })
  assert.equal(firstTarget(buildTree([], [])), null)
  assert.deepEqual(resolveTarget(-11, series, [F(11, 'Sardegna'), F(10, 'Grecia')]), { kind: 'folder', id: 11 })
  assert.deepEqual(resolveTarget(4, series, []), { kind: 'series', id: 4 })
  assert.deepEqual(resolveTarget(-5, series, [F(10, 'Grecia')]), { kind: 'folder', id: 10 })  // a folder that is gone: the first one
  assert.equal(targetName({ kind: 'folder', id: 10 }, series, [F(10, 'Grecia')]), 'Grecia')
  assert.equal(targetName({ kind: 'series', id: 2 }, series, []), 'Atene')
  assert.equal(num2(6), '06'); assert.equal(num2(14), '14'); assert.equal(num2(null), '')
})

test('strip: which slot the pointer is over', () => {
  assert.equal(slotAt(-30, 104, 9), 0)
  assert.equal(slotAt(40, 104, 9), 0)      // left half of the first photo: in front of it
  assert.equal(slotAt(60, 104, 9), 1)      // right half: after it
  assert.equal(slotAt(104 * 5 + 10, 104, 9), 5)
  assert.equal(slotAt(5000, 104, 9), 9)
  assert.equal(slotAt(10, 104, 0), 0)      // an empty lane
})

test('strip: what a drop means', () => {
  const lane = [1, 2, 3, 4, 5, 6]
  assert.deepEqual(dropBefore(lane, [6], 2, true), { before: 3, noop: false })     // 06 -> 03
  assert.deepEqual(dropBefore(lane, [3], 2, true), { before: 4, noop: true })      // onto its own left edge
  assert.deepEqual(dropBefore(lane, [3], 3, true), { before: 4, noop: true })      // onto its own right edge
  assert.deepEqual(dropBefore(lane, [3], 6, true), { before: null, noop: false })  // to the end
  assert.deepEqual(dropBefore(lane, [6], 6, true), { before: null, noop: true })
  assert.deepEqual(dropBefore(lane, [2, 5], 0, true), { before: 1, noop: false })  // a group to the front
  assert.deepEqual(dropBefore(lane, [2, 3], 1, true), { before: 4, noop: true })   // a group dropped where it stands
  assert.deepEqual(dropBefore(lane, [3, 2], 1, true), { before: 4, noop: false })  // ... in another order: a change
  assert.deepEqual(dropBefore(lane, [9], 0, false), { before: 1, noop: false })    // from another lane
  assert.deepEqual(dropBefore([], [9], 0, false), { before: null, noop: false })   // into an empty lane
})

test('strip: the lanes right after a drop, and their numbers', () => {
  const lanes = [L('s1', [1, 2, 3]), L('s2', [4, 5, 6])]
  let m = movedLanes(lanes, [6], 's2', 4)
  assert.deepEqual(m.map(l => l.photos.map((p: any) => p.id)), [[1, 2, 3], [6, 4, 5]])
  assert.equal(m[0], lanes[0])  // an untouched lane is the same object (it is not drawn again)
  m = movedLanes(lanes, [2, 5], 's1', null)
  assert.deepEqual(m.map(l => l.photos.map((p: any) => p.id)), [[1, 3, 2, 5], [4, 6]])
  assert.equal(m[0].photos[3].series_id, 1)  // moved to that series
  m = movedLanes(lanes, [1, 3], 's2', 5)
  assert.deepEqual(m.map(l => l.photos.map((p: any) => p.id)), [[2], [4, 1, 3, 5, 6]])
  const hid = [{ ...L('s1', [1, 2, 3]), photos: [P(1), P(2, { hidden: 1 }), P(3)] }, L('s2', [4]), L('s3', [5], { maybe: 1 })] as any
  assert.deepEqual(renumber(hid, true).map(l => l.photos.map((p: any) => p.num)), [[1, null, 2], [1], [null]])   // a folder: every lane from 1
  assert.deepEqual(renumber(hid, false).map(l => l.photos.map((p: any) => p.num)), [[1, null, 2], [3], [null]])  // one series: running on
  assert.equal(numAtSlot([P(1), P(2), P(3), P(4), P(5), P(6)], [6], 2), 3)  // "06 -> 03"
  assert.equal(numAtSlot([P(1), P(2, { hidden: 1 }), P(3)], [9], 3), 3)
  assert.equal(numAtSlot([P(1), P(2), P(3)], [1], 3), 3)                    // dragging the first to the end of three
})
