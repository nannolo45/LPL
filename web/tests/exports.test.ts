// SPDX-License-Identifier: GPL-3.0-or-later
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { deleteExportText, exportLine } from '../src/exports.ts'
import { STEP_WORDS } from '../src/devices.ts'

const now = Date.UTC(2026, 10, 23, 15, 0) / 1000
test('line: size, device, date; version only when not 0', () => {
  const l = exportLine({ size: 2 * 1024 * 1024, device: 'Phone', agent: 'a', created: now - 60, version: 0 }, now)
  assert.match(l, /Phone/); assert.match(l, /MB/); assert.doesNotMatch(l, /v0/)
  assert.match(exportLine({ size: 10, device: null, agent: 'a1', created: now, version: 2 }, now), /v2 · a1/)
})
test('delete text says it goes to the backup folder', () => {
  assert.match(deleteExportText({ name: 'x.jpg' }).text, /backup folder/)
})
test('step exporting has words', () => { assert.equal(STEP_WORDS.exporting, 'exporting') })
