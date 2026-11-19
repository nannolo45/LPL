// SPDX-License-Identifier: GPL-3.0-or-later
// The devices LPL hands photos to (its agents: the PC agent, the darktable phone app) and the link that
// opens the darktable phone app. Plain functions without React, so they can be tested with Node
// (web/tests/devices.test.ts). See docs/android-handoff.md.
import type { Agent } from './api'

/** What each working-set status is called, with its colours (background, text). */
export const STATUS: Record<string, [string, string, string]> = {
  downloading: ['copying from the NAS', '#2a2a2e', '#c8c8cc'],
  opening: ['opening in darktable', '#3a2f1c', '#E8A33D'],
  editing: ['open in darktable', '#3a2f1c', '#E8A33D'],
  dirty: ['edited · not synced yet', '#1c2a3a', '#8cb8e6'],
  syncing: ['syncing', '#1c2a3a', '#8cb8e6'],
  synced: ['synced to NAS', '#1d2e1d', '#8fd08f'],
  error: ['error', '#3a1c1c', '#ff8b8e'],
}

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
 *  otherwise a PC agent (one that is online first), otherwise the first one. */
export function defaultAgent<A extends Pick<Agent, 'id' | 'os' | 'online'>>(agents: A[], last: string | null): A | null {
  return agents.find(a => a.id === last) || agents.find(a => !isPhoneAgent(a) && a.online) || agents.find(a => !isPhoneAgent(a)) || agents[0] || null
}

/** "Edit in darktable" asks which device when there is a choice, or when the only device is a phone
 *  (a phone is never chosen without asking). With one PC agent nothing is asked, as before. */
export const needsDeviceChoice = (agents: Pick<Agent, 'os'>[]) => agents.length > 1 || (agents.length === 1 && isPhoneAgent(agents[0]))

/** Where a photo is checked out: one line per device that has it in its working set, e.g. "on Phone: edited · not synced yet". */
export function photoWhere(agents: Pick<Agent, 'id' | 'name' | 'os' | 'state'>[], id: number): string[] {
  const out: string[] = []
  for (const a of agents) {
    const w = (a.state?.working_set || []).find(x => x.id === id)
    if (w) out.push(`on ${agentLabel(a, agents)}: ${(STATUS[w.status] || [w.status])[0]}`)
  }
  return out
}

export const PHONE_LINK_MAX = 200

/** The link that opens the darktable phone app on these photos ("Edit on this phone").
 *  ids: the list in display order; start: the photo to open first. More than PHONE_LINK_MAX photos:
 *  the ones around start. Never carries the password or the login cookie. */
export function phoneEditLink(o: { origin: string; ids: number[]; start: number; series?: number | null; title?: string | null }): string {
  let ids = o.ids.includes(o.start) ? o.ids : [o.start, ...o.ids]
  if (ids.length > PHONE_LINK_MAX) {
    const k = ids.indexOf(o.start)
    const from = Math.max(0, Math.min(k - PHONE_LINK_MAX / 2, ids.length - PHONE_LINK_MAX))
    ids = ids.slice(from, from + PHONE_LINK_MAX)
  }
  let q = `server=${encodeURIComponent(o.origin)}&ids=${ids.join(',')}&start=${o.start}`
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
