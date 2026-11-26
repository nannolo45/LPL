// SPDX-License-Identifier: GPL-3.0-or-later
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { fmtBytes, folderLine, lutCount, lutDot, lutGroups, removeFolderQuestion, removeQuestion } from '../src/luts.ts'
import type { LutFile } from '../src/luts.ts'

const f = { sha256: 'aaa', devices: [
  { agent_id: 'pc', name: 'Desktop', os: 'windows', sha256: 'aaa', state: 'ok' as const },
  { agent_id: 'lap', name: 'Laptop', os: 'linux', sha256: 'bbb', state: 'different' as const },
  { agent_id: 'ph', name: 'Phone', os: 'android', sha256: null, state: 'removed' as const },
  { agent_id: 'old', name: 'Old', os: 'linux', sha256: 'ccc', state: 'ok' as const },
] }
test('a dot per device', () => {
  assert.equal(lutDot(f, 'pc'), 'newest')
  assert.equal(lutDot(f, 'lap'), 'other')
  assert.equal(lutDot(f, 'ph'), 'removed')
  assert.equal(lutDot(f, 'old'), 'other')  // reported ok, but with another content than LPL's newest
  assert.equal(lutDot(f, 'none'), 'absent')
})
test('sizes and lines', () => {
  assert.equal(fmtBytes(12), '12 bytes')
  assert.equal(fmtBytes(840_000), '840 kB')
  assert.equal(fmtBytes(1_250_000), '1.3 MB')
  assert.equal(folderLine({ folder: null, reason: 'darktable has no LUT folder set', files: 0, os: 'windows' }), 'darktable has no LUT folder set')
  assert.equal(folderLine({ folder: 'on this phone', reason: null, files: 2, os: 'android' }), '2 files on the phone')
  assert.equal(folderLine({ folder: 'C:/luts', reason: null, files: 1, os: 'windows' }), '1 file · C:/luts')
  assert.equal(lutCount({ files: [{ deleted: false }, { deleted: true }] as any, bytes_current: 2000, bytes_stored: 2000 }), '1 file · 2 kB')
  assert.match(lutCount({ files: [] as any, bytes_current: 0, bytes_stored: 5000 }), /stored with earlier versions/)
  assert.match(removeQuestion('film/a.cube'), /No device loses its copy/)
})

test('LUT files are grouped by their top folder, the top level first', () => {
  const f = (path: string) => ({ path } as LutFile)
  const g = lutGroups([f('b/x.cube'), f('top.cube'), f('A/y.cube'), f('.cache/icons/i.png'), f('b/z.cube')])
  assert.deepEqual(g.map(([k, v]) => [k, v.length]), [['', 1], ['.cache', 1], ['A', 1], ['b', 2]])
  assert.match(removeFolderQuestion('.cache', 16660), /16660 files.*deletes their stored copies/)
})
