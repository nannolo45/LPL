import { test } from 'node:test'
import assert from 'node:assert/strict'
import { pace } from '../src/renderpace.ts'
import type { RenderProgress } from '../src/api.ts'

const base = { job: 1, total: 262, failed: 0, current: null, ended: null, stopped: false } as const

test('early in a run: download per photo downloaded, with its speed; no overall pace yet', () => {
  // 26 Nov: 2 of 262 done, 10 RAWs downloaded ahead (32 s, 500 MB), 2 rendered in 15.4 s
  const p: RenderProgress = { ...base, done: 2, started: 1000, download_s: 32, fetched: 10, bytes: 500e6, render_s: 15.4, rendered: 2 }
  assert.equal(pace(p, 1038), ' · download 3.2 s at 16 MB/s · darktable 7.7 s per photo')
})

test('later: the overall pace with the parts', () => {
  const p: RenderProgress = { ...base, done: 60, started: 1000, download_s: 192, fetched: 68, bytes: 3.4e9, render_s: 462, rendered: 60 }
  assert.equal(pace(p, 1000 + 480), ' · about 8.0 s per photo (download 2.8 s at 18 MB/s, darktable 7.7 s)')
})

test('nothing measured yet: nothing said', () => {
  assert.equal(pace({ ...base, done: 0, started: 1000 }, 1001), '')
})
