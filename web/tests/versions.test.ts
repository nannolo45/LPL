// SPDX-License-Identifier: GPL-3.0-or-later
// Tests for versions of a photo (R17, src/versions.ts and the pieces of devices.ts and api.ts that carry a version): npm test.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { bumpVersions, canPin, deleteText, metaVersion, pickedVersion, pickVersion, pinFor, versionBadge, versionLabel, versionState, viewedPhoto, viewedVersion } from '../src/versions.ts'
import { phoneEditLink, photoActivity, photoWhere } from '../src/devices.ts'
import { thumbUrl, versionUrl } from '../src/api.ts'

const photo: any = { id: 7, rating: 3, labels: [1], tags: ['a'], edited: 0, versions: 3, cover: 0, preview_ver: 4 }
const list: any[] = [
  { n: 0, name: '', rating: 3, labels: [1], tags: ['a'], edited: 0, cover: true, preview_ver: 0 },
  { n: 1, name: 'black and white', rating: 5, labels: [], tags: ['bw'], edited: 1, cover: false, preview_ver: 2 },
  { n: 2, name: '', rating: 1, labels: [2, 4], tags: [], edited: 1, cover: false, preview_ver: 0 },
]

test('badge: a count only when a photo has more than one version', () => {
  assert.equal(versionBadge({ versions: 3 }), 3)
  assert.equal(versionBadge({ versions: 2 }), 2)
  assert.equal(versionBadge({ versions: 1 }), null)
  assert.equal(versionBadge({}), null)                 // a server from before versions
})

test('strip: labels and states of the versions', () => {
  assert.equal(versionLabel(list[0]), 'v0')
  assert.equal(versionLabel(list[1]), 'v1 · black and white')
  assert.equal(versionState(list[0]), 'no edits')
  assert.equal(versionState(list[1]), 'edited')
})

test('strip: the viewed version is the picked one while it exists, else the cover', () => {
  assert.equal(viewedVersion(list, undefined, 0), 0)
  assert.equal(viewedVersion(list, 2, 0), 2)
  assert.equal(viewedVersion(list, 9, 1), 1)           // 9 was deleted: back to the cover
  assert.equal(viewedVersion(null, 2, 0), 0)           // no list (one version): the cover
  assert.equal(viewedVersion(list.slice(1), undefined, 0), 1)  // the cover is gone from the list: the first
})

test('strip: rating, labels and tags shown are those of the viewed version; edits for it carry its number', () => {
  assert.equal(viewedPhoto(photo, list, 0), photo)     // the cover is the photo itself
  const v = viewedPhoto(photo, list, 2)
  assert.deepEqual([v.rating, v.labels, v.tags, v.edited], [1, [2, 4], [], 1])
  assert.equal(viewedPhoto(photo, null, 2), photo)
  assert.equal(metaVersion(photo, 0), undefined)       // the cover: the server's default
  assert.equal(metaVersion(photo, 2), 2)
  assert.equal(metaVersion({ cover: 1 }, 1), undefined)
  assert.equal(metaVersion({ cover: 1 }, 0), 0)        // another version than the cover, even the original
})

test('cover: the tile shows the cover; a pinned entry asks for its own version', () => {
  assert.equal(thumbUrl({ id: 7, preview_ver: 4 }), '/api/preview/7?s=t&v=4')
  assert.equal(thumbUrl({ id: 7, preview_ver: 4, cover: 1, show: 1 }), '/api/preview/7?s=t&v=4')          // pin = cover: the same picture
  assert.equal(thumbUrl({ id: 7, preview_ver: 4, cover: 1, show: 2 }, 'l'), '/api/preview/7?s=l&version=2&v=4')
  assert.equal(thumbUrl({ id: 7, preview_ver: 4, cover: 0, show: 0 }), '/api/preview/7?s=t&v=4')
  assert.equal(versionUrl(7, 2, 5), '/api/preview/7?s=t&version=2&v=5')
})

test('pin: "use in series" only for a version the entry does not show, and null puts it back on the cover', () => {
  assert.equal(canPin({ cover: 0, show: 0 }, 2), true)
  assert.equal(canPin({ cover: 0, show: 2 }, 2), false)
  assert.equal(canPin({ cover: 1 }, 2), true)
  assert.equal(canPin(null, 2), false)
  assert.equal(pinFor({ cover: 0 }, 2), 2)
  assert.equal(pinFor({ cover: 0 }, 0), null)
  assert.equal(pinFor({ cover: 1 }, 1), null)
})

test('delete: says where the file goes', () => {
  const t = deleteText(list[1])
  assert.equal(t.title, 'Delete v1 · black and white?')
  assert.match(t.text, /backup folder/)
})

test('picked version: remembered per photo', () => {
  assert.equal(pickedVersion(41), undefined)
  pickVersion(41, 2)
  assert.equal(pickedVersion(41), 2)
  bumpVersions()
  assert.equal(pickedVersion(42), undefined)
})

test('link: the phone link carries the version only when one is given', () => {
  const base = { origin: 'https://lpl.example', ids: [1, 2], start: 2 }
  const plain = phoneEditLink(base)
  assert.ok(!plain.includes('version='))
  assert.ok(plain.includes('start=2'))
  const v = phoneEditLink({ ...base, version: 3, series: 5 })
  assert.ok(v.includes('start=2&version=3&series=5'), v)
  assert.ok(phoneEditLink({ ...base, version: 0 }).includes('&version=0'))
})

test('where: the line names the version that is checked out', () => {
  const phone: any = { id: 'p', name: 'Phone', os: 'android', online: true, last_seen: 1, state: { working_set: [{ id: 5, filename: 'x', status: 'edited', version: 2 }, { id: 6, filename: 'y', status: 'edited' }] } }
  assert.deepEqual(photoWhere([phone], 5), ['on Phone: version 2, edited · not synced yet'])
  assert.deepEqual(photoWhere([phone], 6), ['on Phone: edited · not synced yet'])
  assert.deepEqual(photoActivity([phone], 5), [{ text: 'on Phone: version 2, edited · not synced yet' }])
})
