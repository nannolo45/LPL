// SPDX-License-Identifier: GPL-3.0-or-later
// The devices LPL hands photos to (its agents: the PC agent, the darktable phone app) and the link that
// opens the darktable phone app. Plain functions without React, so they can be tested with Node
// (web/tests/devices.test.ts). See docs/android-handoff.md.
import type { Agent, WorkItem } from './api'

/** What each working-set status is called, with its colours (background, text). */
export const STATUS: Record<string, [string, string, string]> = {
  downloading: ['copying from the NAS', '#2a2a2e', '#c8c8cc'],
  opening: ['opening in darktable', '#3a2f1c', '#E8A33D'],
  editing: ['open in darktable', '#3a2f1c', '#E8A33D'],
  dirty: ['edited · not synced yet', '#1c2a3a', '#8cb8e6'],
  syncing: ['syncing', '#1c2a3a', '#8cb8e6'],
  synced: ['synced to NAS', '#1d2e1d', '#8fd08f'],
  error: ['error', '#3a1c1c', '#ff8b8e'],
  // on the device, RAW and XMP both there, not opened yet (the Devices page says "on Laptop · ready to edit")
  ready: ['ready to edit', '#1d2e1d', '#8fd08f'],
  // other devices' words for "has edits that are not on the NAS yet"
  edited: ['edited · not synced yet', '#1c2a3a', '#8cb8e6'],
}

/** Every status that means "this copy has edits that are not saved to the NAS yet" (or may have: a photo open
 *  in darktable). dirty and editing are the PC agent's; edited, modified, "waiting to sync" are other devices'
 *  words for dirty; a sync that failed left its edits unsaved too. */
const UNSAVED = new Set(['dirty', 'editing', 'edited', 'modified', 'unsynced', 'waiting to sync', 'waiting_to_sync'])
export const hasUnsavedEdits = (w: Pick<WorkItem, 'status' | 'error'>) =>
  UNSAVED.has((w.status || '').toLowerCase()) || (w.status === 'error' && /sync/i.test(w.error || ''))

/** The darktable phone app reports os "android"; it only gets jobs addressed to it. */
export const isPhoneAgent = (a: Pick<Agent, 'os'>) => a.os === 'android'

const userAgent = () => (typeof navigator === 'undefined' ? '' : navigator.userAgent)
/** Chrome, Firefox and web views on Android (a web view's user agent has "Android" and "; wv"). */
export const isAndroidBrowser = (ua: string = userAgent()) => /\bAndroid\b/i.test(ua)
/** Inside the LPL shell app for Android (a full-screen web view that adds " LPLShell/<version>" to the user agent). */
export const inShell = (ua: string = userAgent()) => ua.includes('LPLShell/')

/** A device's name as its user set it on the device; when two have the same name, the system is added,
 *  and if that is the same too, the start of the id. */
export function agentLabel(a: Pick<Agent, 'id' | 'name' | 'os'>, all: Pick<Agent, 'id' | 'name' | 'os'>[]): string {
  const name = a.name || a.id
  const same = all.filter(b => b.id !== a.id && (b.name || b.id) === name)
  if (!same.length) return name
  const os = a.os || '?'
  if (!same.some(b => (b.os || '?') === os)) return `${name} (${os})`
  return `${name} (${os}, ${a.id.slice(0, 8)})`
}

/** The device "Edit in darktable" suggests: the one used last on this browser if it still exists,
 *  otherwise a PC agent (one that is online first), otherwise any device; among equals the one heard from last.
 *  (The list itself comes in a stable order by name, so recency is applied here.) */
export function defaultAgent<A extends Pick<Agent, 'id' | 'os' | 'online'> & { last_seen?: number }>(agents: A[], last: string | null): A | null {
  const recent = [...agents].sort((a, b) => (b.last_seen || 0) - (a.last_seen || 0))
  return recent.find(a => a.id === last) || recent.find(a => !isPhoneAgent(a) && a.online) || recent.find(a => !isPhoneAgent(a)) || recent[0] || null
}

/** The state shown on a device's card, as pills ([ok, words]). A PC agent reports its darktable and the Lua
 *  bridge; the darktable phone app heartbeats only while it is open on the screen, reports in darktable_running
 *  whether a photo is open in its darkroom, and always sends bridge_alive true (it has no bridge), so its card
 *  says that instead. seen: how long ago, in words, for offline devices. */
export function devicePills(a: Pick<Agent, 'os' | 'online' | 'state'>, seen: string): [boolean, string][] {
  if (isPhoneAgent(a)) {
    if (!a.online) return [[false, `app not open · seen ${seen}`]]
    return [[true, 'online · app open'], [!!a.state?.darktable_running, a.state?.darktable_running ? 'photo open in the darkroom' : 'no photo open']]
  }
  if (!a.online) return [[false, `offline · seen ${seen}`]]
  const out: [boolean, string][] = [[true, 'online'], [!!a.state?.darktable_running, `darktable ${a.state?.darktable_running ? 'running' : 'not running'}`]]
  if (a.state?.darktable_running) out.push([!!a.state?.bridge_alive, `bridge script ${a.state?.bridge_alive ? 'active' : 'not seen'}`])
  return out
}

/** "Edit in darktable" asks which device when there is a choice, or when the only device is a phone
 *  (a phone is never chosen without asking). With one PC agent nothing is asked, as before. */
export const needsDeviceChoice = (agents: Pick<Agent, 'os'>[]) => agents.length > 1 || (agents.length === 1 && isPhoneAgent(agents[0]))

/** Where a photo is checked out: one line per device that has it in its working set, e.g. "on Phone: edited · not synced yet". */
export function photoWhere(agents: Pick<Agent, 'id' | 'name' | 'os' | 'state'>[], id: number): string[] {
  const out: string[] = []
  for (const a of agents) {
    const w = (a.state?.working_set || []).find(x => x.id === id)
    if (w) out.push(`on ${agentLabel(a, agents)}: ${w.version ? `version ${w.version}, ` : ''}${(STATUS[w.status] || [w.status])[0]}`)
  }
  return out
}

/** The steps a device may report while it copies or syncs a photo (R16), in plain words. */
export const STEP_WORDS: Record<string, string> = {
  downloading: 'downloading',
  'sending edit': 'sending the edit',
  'rendering preview': 'rendering the preview',
  'sending preview': 'sending the preview',
  exporting: 'exporting',
}
/** As on the server (api.py): a device not heard from for this long is offline. */
export const AGENT_ONLINE_SECONDS = 15

/** The server's clock now, from the last status answer (server_time, and when it arrived here). */
export function serverNow(status: { server_time?: number; received_at?: number } | null | undefined, local: number = Date.now() / 1000): number {
  if (!status?.server_time) return local
  return status.server_time + Math.max(0, local - (status.received_at ?? local))
}

/** What a device is doing with a working-set item right now: the step in words and how far (0..1, or null
 *  when it does not say). null when there is no step, or the device stopped reporting: then the item's plain
 *  state is shown. now = the server's clock (serverNow), so a bar goes away even when this page stops
 *  getting answers. */
export function itemProgress(a: Pick<Agent, 'online' | 'last_seen'>, w: Pick<WorkItem, 'step' | 'progress'>, now?: number): { step: string; fraction: number | null } | null {
  if (!w.step || !a.online) return null
  if (now != null && now - (a.last_seen || 0) >= AGENT_ONLINE_SECONDS) return null
  const p = w.progress
  const fraction = typeof p === 'number' && Number.isFinite(p) ? Math.min(1, Math.max(0, p)) : null
  return { step: STEP_WORDS[w.step] || w.step, fraction }
}

/** photoWhere with progress: one entry per device that has the photo; progress is set (a fraction or null
 *  for "busy, amount unknown") only while that device downloads or syncs it. */
export function photoActivity(agents: Pick<Agent, 'id' | 'name' | 'os' | 'state' | 'online' | 'last_seen'>[], id: number, now?: number):
  { text: string; progress?: number | null }[] {
  const out: { text: string; progress?: number | null }[] = []
  for (const a of agents) {
    const w = (a.state?.working_set || []).find(x => x.id === id)
    if (!w) continue
    const pr = itemProgress(a, w, now)
    const ver = w.version ? `version ${w.version}, ` : ''  // R17: the version that is checked out
    out.push(pr ? { text: `on ${agentLabel(a, agents)}: ${ver}${pr.step}`, progress: pr.fraction }
      : { text: `on ${agentLabel(a, agents)}: ${ver}${(STATUS[w.status] || [w.status])[0]}` })
  }
  return out
}

export const PHONE_LINK_MAX = 200

/** The photos a selection bar hands to the phone app: the selected ones in display order, then any selected
 *  ones not in the shown list (in the order they were selected). The first of them is the one to open. */
export function selectionIds(list: number[], sel: number[]): number[] {
  const chosen = new Set(sel)
  const shown = list.filter(id => chosen.has(id))
  const known = new Set(shown)
  return [...shown, ...sel.filter((id, i) => !known.has(id) && sel.indexOf(id) === i)]
}

/** The link that opens the darktable phone app on these photos ("Edit on this phone").
 *  ids: the list in display order; start: the photo to open first. More than PHONE_LINK_MAX photos:
 *  the ones around start. Never carries the password or the login cookie. */
export function phoneEditLink(o: { origin: string; ids: number[]; start: number; series?: number | null; title?: string | null; version?: number | null }): string {
  let ids = o.ids.includes(o.start) ? o.ids : [o.start, ...o.ids]
  if (ids.length > PHONE_LINK_MAX) {
    const k = ids.indexOf(o.start)
    const from = Math.max(0, Math.min(k - PHONE_LINK_MAX / 2, ids.length - PHONE_LINK_MAX))
    ids = ids.slice(from, from + PHONE_LINK_MAX)
  }
  let q = `server=${encodeURIComponent(o.origin)}&ids=${ids.join(',')}&start=${o.start}`
  if (o.version != null) q += `&version=${o.version}`  // R17: the version to open first (default: the cover)
  if (o.series != null) q += `&series=${o.series}`
  if (o.title) q += `&title=${encodeURIComponent(o.title)}`
  const fallback = encodeURIComponent(o.origin + '/android-app.html')
  return `intent://edit?${q}#Intent;scheme=lpldt;package=app.dtandroid;S.browser_fallback_url=${fallback};end`
}

/** Deep links: <origin>/?photo=<id> and <origin>/?series=<id>. */
export function parseDeepLink(search: string): { photo?: number; series?: number } | null {
  const p = new URLSearchParams(search)
  // a malformed id becomes 0, which no photo or series has, so it gets the usual "not found"
  const num = (k: string) => { const v = p.get(k); return v == null ? undefined : /^\d+$/.test(v) ? +v : 0 }
  const photo = num('photo'), series = num('series')
  return photo != null || series != null ? { photo, series } : null
}

let pendingLink: ReturnType<typeof parseDeepLink> | undefined
/** The deep link this page was opened with, once (later calls get null). The parameters are taken out of
 *  the address bar straight away, so a reload doesn't repeat them; other parameters stay. */
export function takeDeepLink() {
  if (pendingLink === undefined) readDeepLink()
  const d = pendingLink
  pendingLink = null
  return d
}
function readDeepLink() {
  pendingLink = typeof location === 'undefined' ? null : parseDeepLink(location.search)
  if (!pendingLink) return
  const u = new URL(location.href)
  u.searchParams.delete('photo'); u.searchParams.delete('series')
  history.replaceState(history.state, '', u.pathname + u.search + u.hash)
}
if (typeof location !== 'undefined') readDeepLink()  // at start-up, also when the login page comes first
