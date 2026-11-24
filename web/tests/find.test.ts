// SPDX-License-Identifier: GPL-3.0-or-later
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { findWords, nameMatches } from '../src/find.ts'

test('find: part of the name, case ignored', () => {
  assert.equal(nameMatches('DSCF0831.RAF', findWords('0831')), true)
  assert.equal(nameMatches('DSCF0831.RAF', findWords('dscf0831')), true)
  assert.equal(nameMatches('DSCF0832.RAF', findWords('0831')), false)
})
test('find: every word must be in the name, in any order', () => {
  assert.equal(nameMatches('DSCF0831.RAF', findWords('raf 0831')), true)
  assert.equal(nameMatches('DSCF0831.JPG', findWords('raf 0831')), false)
})
test('find: accents ignored, empty finds nothing', () => {
  assert.equal(nameMatches('Città_01.jpg', findWords('citta')), true)
  assert.equal(nameMatches('anything.jpg', findWords('   ')), false)
})
