// SPDX-License-Identifier: GPL-3.0-or-later
// Tests for the phone app behaviour (src/back.ts, src/install.ts): npm test.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { depthOf, layersToClose } from '../src/back.ts'
import { installHint } from '../src/install.ts'

test('back: which layers a history entry stands for', () => {
  assert.equal(depthOf({ lplDepth: 2 }, 3), 2)
  assert.equal(depthOf(null, 3), 0)                      // the first entry: nothing open
  assert.equal(depthOf({ other: 1 }, 3), 0)
  assert.equal(depthOf({ lplFull: 1 }, 2), 2)            // the full-screen photo's own entry closes none of ours
  assert.deepEqual(layersToClose(3, 2), [2])             // Back closes the top one
  assert.deepEqual(layersToClose(3, 0), [2, 1, 0])       // back to the first entry: everything, top first
  assert.deepEqual(layersToClose(1, 1), [])              // the app closed it already
  assert.deepEqual(layersToClose(0, 2), [])              // after a reload nothing is open
})

const base = { shell: false, standalone: false, installed: false, secure: true, canPrompt: false }
test('install: what Settings offers', () => {
  assert.equal(installHint({ ...base, canPrompt: true }), 'button')             // Chrome over https
  assert.equal(installHint(base), 'menu')                                       // Firefox over https: no prompt event
  assert.equal(installHint({ ...base, secure: false }), 'https')                // plain http
  assert.equal(installHint({ ...base, secure: false, canPrompt: true }), 'button')
  assert.equal(installHint({ ...base, standalone: true, canPrompt: true }), 'none')
  assert.equal(installHint({ ...base, installed: true }), 'none')
  assert.equal(installHint({ ...base, shell: true, canPrompt: true }), 'none')  // inside the LPL shell: nothing
  assert.equal(installHint({ ...base, shell: true, secure: false }), 'none')
})
