// SPDX-License-Identifier: GPL-3.0-or-later
// The bar at the top's "where the time goes" for a PC rendering previews (components/RenderProgress.tsx).
import type { RenderProgress } from './api'

const secs = (x: number) => (x >= 90 ? `${Math.round(x / 60)} min` : `${x < 10 ? x.toFixed(1) : Math.round(x)} s`)

/** Where the time goes, from the agent's report: download per photo downloaded (with its speed, which says whether
 * the network is the limit), darktable per photo rendered, and the overall pace once a few photos are done (the
 * agent downloads ahead and renders in batches, so the first photos say little). */
export function pace(p: RenderProgress, now = Date.now() / 1000) {
  const parts: string[] = []
  if (p.fetched && p.download_s) {
    const mbs = (p.bytes || 0) / 1e6 / p.download_s
    parts.push(`download ${secs(p.download_s / p.fetched)}${mbs ? ` at ${mbs < 10 ? mbs.toFixed(1) : Math.round(mbs)} MB/s` : ''}`)
  }
  if (p.rendered && p.render_s) parts.push(`darktable ${secs(p.render_s / p.rendered)}`)
  const n = p.done + p.failed
  const overall = n >= 6 ? `about ${secs(((p.ended || now) - p.started) / n)} per photo` : ''
  if (!parts.length && !overall) return ''
  return ' · ' + (overall ? overall + (parts.length ? ` (${parts.join(', ')})` : '') : parts.join(' · ') + ' per photo')
}

