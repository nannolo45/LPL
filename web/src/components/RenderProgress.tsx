// SPDX-License-Identifier: GPL-3.0-or-later
// Previews rendered with darktable on a PC (docs/rendered-previews.md): the strip under the top bar while a PC renders
// (count, the photo in hand, Stop), the shared "update previews" action for a series, series folder, library folder or
// selection, and the refresh of the tiles as each new preview arrives.
import { useEffect, useRef } from 'react'
import { api, RenderProgress as Progress, RenderScope } from '../api'
import { useStore } from '../store'
import { bumpSeries } from '../seriesops'
import { Bar } from './DevicesBits'

type Toast = (m: string) => void

/** Ask how many photos `scope` covers, confirm, and hand the job to a PC. what: "Grecia", "the folder 2023/Athens". */
export async function updatePreviews(scope: RenderScope, what: string, toast: Toast) {
  try {
    let n = (await api.previewsCount(scope)).count
    let body: RenderScope = scope
    if (!n && scope.ids == null && !scope.stale) {
      const all = (await api.previewsCount({ ...scope, all: true })).count
      if (!all) { toast(`No photo in ${what} has darktable edits.`); return }
      if (!window.confirm(`Every edited photo in ${what} already shows its edit. Render all ${all} again anyway?`)) return
      body = { ...scope, all: true }; n = all
    } else if (!n) { toast(`Nothing to render in ${what}.`); return }
    else if (n > 1 && !window.confirm(`Render ${n} preview${n === 1 ? '' : 's'} in ${what} with darktable, so they show the edits? A PC with darktable does it; you can stop it from the bar at the top.`)) return
    const r = await api.previewsRender(body)
    toast(`Rendering ${r.count} preview${r.count === 1 ? '' : 's'} on ${r.agent_name || 'the PC'}.`)
    window.dispatchEvent(new Event('lpl-refresh-status'))
  } catch (e: any) { toast(e.message || String(e)) }
}

const SHOW_ENDED = 20  // seconds the strip stays after a job ended ("Previews updated: 40")

/** The strip under the top bar, one line per PC that renders (or just finished). Nothing otherwise. */
export function RenderProgress() {
  const s = useStore()
  const now = Date.now() / 1000
  const jobs = s.agents.filter(a => a.online || a.state?.render?.ended == null)
    .map(a => ({ a, p: a.state?.render as Progress | undefined }))
    .filter((x): x is { a: typeof x.a; p: Progress } => !!x.p && (x.p.ended == null || now - x.p.ended < SHOW_ENDED))
  // new previews: reload what is on screen (the library's tiles, the board and strip) a moment after each one
  const done = s.agents.reduce((n, a) => n + (a.state?.render?.done || 0) * 1000 + (a.state?.render?.job || 0), 0)
  const last = useRef(done)
  useEffect(() => {
    if (done === last.current) return
    last.current = done
    const t = window.setTimeout(() => { s.reload(); bumpSeries() }, 800)
    return () => window.clearTimeout(t)
  }, [done])
  if (!jobs.length) return null
  return (
    <div className="renderbar" role="status">
      {jobs.map(({ a, p }) => {
        const ended = p.ended != null
        const n = p.done + p.failed
        const text = ended
          ? `${p.stopped ? 'Stopped' : 'Previews updated'} on ${a.name}: ${p.done} rendered${p.failed ? `, ${p.failed} failed (see the agent's log)` : ''}`
          : !a.online ? `${a.name} stopped reporting while rendering previews (${n} of ${p.total})`
          : `Rendering previews with darktable on ${a.name}: ${n} of ${p.total}${p.current ? ` · ${p.current}` : ''}${p.failed ? ` · ${p.failed} failed` : ''}`
        return (
          <div key={a.id} className="row renderline" style={{ gap: 10 }}>
            <span className="small grow ellipsis">{text}</span>
            {!ended && <span className="renderbarbar"><Bar fraction={p.total ? n / p.total : 0} label={`Rendering previews on ${a.name}`} still={!a.online} /></span>}
            {!ended && a.online && <button className="btn tiny" onClick={() => api.previewsStop(p.job).then(() => s.toast('Stopping after the photo in hand…')).catch(e => s.toast(e.message))}>Stop</button>}
          </div>
        )
      })}
    </div>
  )
}
