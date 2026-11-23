// SPDX-License-Identifier: GPL-3.0-or-later
import test from 'node:test'
import assert from 'node:assert/strict'
import { blurIsFocusHandBack, EDIT_GRACE_MS, gripDragStarts, GRIP_HOLD_MS, isNameTapRename } from '../src/stripTouch.ts'

test('a finger on a handle: a small wobble is not a drag until it has been held', () => {
  assert.equal(gripDragStarts(1, 1, 50, true), false)
  assert.equal(gripDragStarts(1, 1, GRIP_HOLD_MS, true), true)
})
test('a finger on a handle: moving a few pixels lifts the lane at once (no long press needed)', () => {
  assert.equal(gripDragStarts(0, 5, 30, true), true)
  assert.equal(gripDragStarts(-4, 0, 0, true), true)
})
test('a mouse never lifts by holding, only by moving', () => {
  assert.equal(gripDragStarts(0, 0, 5000, false), false)
  assert.equal(gripDragStarts(3, 4, 0, false), true)
  assert.equal(gripDragStarts(2, 2, 0, false), false)
})
test('a blur just after the name editor opened is ignored, a later one commits', () => {
  assert.equal(blurIsFocusHandBack(1000, 1000 + EDIT_GRACE_MS - 1), true)
  assert.equal(blurIsFocusHandBack(1000, 1000 + EDIT_GRACE_MS), false)
})
test('a tap on a name renames on the phone only, and only where renaming is allowed', () => {
  assert.equal(isNameTapRename(true, true), true)
  assert.equal(isNameTapRename(false, true), false)
  assert.equal(isNameTapRename(true, false), false)
})
