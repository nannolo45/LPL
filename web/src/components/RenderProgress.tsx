// SPDX-License-Identifier: GPL-3.0-or-later
// Previews rendered with darktable on PCs (docs/rendered-previews.md):
// - the bar under the top bar: one line per render job the NAS knows (waiting, running, paused because its PC is
//   away, being stopped, just ended), with Stop, a × that clears it, the failures and where the time goes;
// - the dialog that asks which PCs render (the photos are split between them);
// - the refresh of the tiles as new previews arrive.
import { useEffect, useRef, useState } from 'react'
import { api, RenderJob, RenderScope } from '../api'
import { useStore } from '../store'
import { bumpSeries } from '../seriesops'
import { Bar } from './DevicesBits'

type Toast = (m: string) => void
const ASK = 'lpl-render-ask'
const REFRESH = 'lpl-render-refresh'

/** Open the dialog for `scope`. what: how the photos are named in it ("“Grecia”", "the folder 2023/Athens"). */
export function updatePreviews(scope: RenderScope, what: string, _toast?: Toast) {
  window.dispatchEvent(new CustomEvent(ASK, { detail: { scope, what } }))
}

/** The dialog: how many photos, which PCs (all that can render, ticked), render. Mounted once in the app. */
export function RenderDialog() {
  const s = useStore()
  const [ask, setAsk] = useState<{ scope: RenderScope; what: string } | null>(null)
  const [n, setN] = useState<{ stale: number; all: number } | null>(null)
  const [pcs, setPcs] = useState<{ id: string; name: string }[]>([])
  const [why, setWhy] = useState<string | undefined>()
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [everything, setEverything] = useState(false)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    const h = (e: Event) => {
      const d = (e as CustomEvent).detail
      setAsk(d); setN(null); setEverything(false); setBusy(false)
      const listing = d.scope.ids == null && !d.scope.stale
      Promise.all([api.previewsCount(d.scope), listing ? api.previewsCount({ ...d.scope, all: true }) : Promise.resolve(null), api.previewsStale()])
        .then(([a, b, st]) => {
          setN({ stale: a.count, all: b ? b.count : a.count })
          setPcs(st.agents); setWhy(st.why)
          let keep: string[] = []
          try { keep = JSON.parse(localStorage.getItem('lpl.renderpcs') || '[]') } catch { /* ignore */ }
          const want = st.agents.filter(p => keep.includes(p.id))
          setPicked(new Set((want.length ? want : st.agents).map(p => p.id)))
        }).catch(err => { s.toast(err.message); setAsk(null) })
    }
    window.addEventListener(ASK, h)
    return () => window.removeEventListener(ASK, h)
  }, [])
  if (!ask) return null
  const close = () => setAsk(null)
  const count = n ? (everything ? n.all : n.stale) : 0
  const go = () => {
    setBusy(true)
    const ids = [...picked]
    try { localStorage.setItem('lpl.renderpcs', JSON.stringify(ids)) } catch { /* ignore */ }
    api.previewsRender({ ...ask.scope, ...(everything ? { all: true } : {}), agent_ids: ids })
      .then(r => { s.toast(`Rendering ${r.count} preview${r.count === 1 ? '' : 's'} on ${r.agent_name}.`); window.dispatchEvent(new Event(REFRESH)); close() })
      .catch(e => { s.toast(e.message); setBusy(false) })
  }
  const big = s.isMobile ? ' big' : ''
  return (
    <>
      <div className="backdrop" onClick={close} />
      <div className={(s.isMobile ? 'sheet' : 'dialog') + ' renderdlg'} role="dialog" aria-label="Update previews from the darktable edits">
        {s.isMobile && <span className="grab" />}
        <span className="display" style={{ fontSize: 19, fontWeight: 500 }}>Update previews from the darktable edits</span>
        {!n ? <span className="soft">Counting…</span> : (
          <>
            <span className="soft" style={{ lineHeight: 1.5 }}>
              {n.stale
                ? <><b>{n.stale}</b> edited photo{n.stale === 1 ? '' : 's'} in {ask.what} {n.stale === 1 ? 'shows' : 'show'} a preview from before {n.stale === 1 ? 'its' : 'their'} edit.</>
                : n.all ? <>Every edited photo in {ask.what} already shows its edit.</> : <>No photo in {ask.what} has darktable edits.</>}
            </span>
            {n.all > n.stale && (
              <label className="row small" style={{ gap: 8 }}>
                <input type="checkbox" checked={everything} onChange={e => setEverything(e.target.checked)} />
                Render all {n.all} edited photos again
              </label>
            )}
            <div className="col" style={{ gap: 6 }}>
              <span className="small muted">Render on{pcs.length > 1 ? ' (the photos are split between the ticked PCs)' : ''}</span>
              {pcs.length ? pcs.map(p => (
                <label key={p.id} className="row small" style={{ gap: 8 }}>
                  <input type="checkbox" checked={picked.has(p.id)} onChange={e => {
                    const x = new Set(picked); if (e.target.checked) x.add(p.id); else x.delete(p.id); setPicked(x)
                  }} />
                  {p.name}
                </label>
              )) : <span className="small sharedwarn">{why || 'No PC that can render is online.'}</span>}
            </div>
          </>
        )}
        <div className="row" style={{ gap: 8, justifyContent: 'flex-end' }}>
          <button type="button" className={'btn' + big} onClick={close}>Cancel</button>
          <button type="button" className={'btn primary' + big} disabled={busy || !count || !picked.size} onClick={go}>
            Render {count || ''} preview{count === 1 ? '' : 's'}
          </button>
        </div>
      </div>
    </>
  )
}

const secs = (x: number) => (x >= 90 ? `${Math.round(x / 60)} min` : `${x < 10 ? x.toFixed(1) : Math.round(x)} s`)

/** "about 9 s per photo: download 6 s, darktable 2.5 s" from the agent's report (when it says). */
function pace(p: NonNullable<RenderJob['progress']>) {
  const n = p.done + p.failed
  if (!n || p.render_s == null) return ''
  const t = ((p.ended || Date.now() / 1000) - p.started) / n
  return ` · about ${secs(t)} per photo (download ${secs((p.download_s || 0) / n)}, darktable ${secs((p.render_s || 0) / n)})`
}

/** The bar under the top bar. Nothing when there is no render job to show. */
export function RenderProgress() {
  const s = useStore()
  const [jobs, setJobs] = useState<RenderJob[]>([])
  const [open, setOpen] = useState<number | null>(null)  // the job whose failures are listed
  useEffect(() => {
    let alive = true
    const load = () => api.previewsJobs().then(r => { if (alive) setJobs(r.jobs) }).catch(() => {})
    load()
    const t = window.setInterval(load, 3000)
    window.addEventListener(REFRESH, load)
    return () => { alive = false; window.clearInterval(t); window.removeEventListener(REFRESH, load) }
  }, [])
  // new previews: reload what is on screen (library tiles, board, strip) a moment after each one
  const done = jobs.reduce((n, j) => n + (j.progress?.done || 0), 0)
  const last = useRef(done)
  useEffect(() => {
    if (done === last.current) return
    last.current = done
    const t = window.setTimeout(() => { s.reload(); bumpSeries() }, 800)
    return () => window.clearTimeout(t)
  }, [done])
  if (!jobs.length) return null
  const act = (p: Promise<unknown>, msg?: string) => p.then(() => { if (msg) s.toast(msg); window.dispatchEvent(new Event(REFRESH)) }).catch(e => s.toast(e.message))
  return (
    <div className="renderbar" role="status">
      {jobs.map(j => {
        const p = j.progress
        const n = p ? p.done + p.failed : 0
        const active = j.status === 'queued' || j.status === 'taken' || j.status === 'stopping'
        const failed = p?.failed || 0
        let text: string
        if (j.status === 'queued') text = `Waiting for ${j.agent_name} to start rendering ${j.total} preview${j.total === 1 ? '' : 's'}${j.online ? '' : ` (${j.agent_name} is offline)`}`
        else if (j.status === 'stopping') text = `Stopping on ${j.agent_name} after the photos in hand${p ? ` (${n} of ${p.total})` : ''}…`
        else if (j.status === 'taken' && !j.online) text = `${j.agent_name} is away (asleep or off): rendering paused${p ? ` at ${n} of ${p.total}` : ''}. It carries on when it is back, or Stop it.`
        else if (j.status === 'taken') text = p ? `Rendering previews with darktable on ${j.agent_name}: ${n} of ${p.total}${p.current ? ` · ${p.current}` : ''}${pace(p)}` : `${j.agent_name} is starting to render ${j.total} previews…`
        else text = `${j.status === 'stopped' ? 'Stopped' : 'Previews updated'} on ${j.agent_name}${p ? `: ${p.done} rendered${pace(p)}` : ''}`
        return (
          <div key={j.id} className="col" style={{ gap: 4 }}>
            <div className="row renderline" style={{ gap: 10 }}>
              <span className="small grow ellipsis" title={text}>{text}</span>
              {failed > 0 && (
                <button className="devmore renderfail" aria-expanded={open === j.id} onClick={() => setOpen(open === j.id ? null : j.id)}>
                  {failed} failed · why?
                </button>
              )}
              {j.status === 'taken' && p && <span className="renderbarbar"><Bar fraction={p.total ? n / p.total : 0} label={`Rendering previews on ${j.agent_name}`} still={!j.online} /></span>}
              {active && j.status !== 'stopping' && (
                <button className="btn tiny" onClick={() => act(api.previewsStop(j.id), j.online && j.status === 'taken' ? 'Stopping after the photos in hand…' : undefined)}>Stop</button>
              )}
              <button className="btn tiny ghost" title={active ? 'Stop and clear' : 'Clear'} aria-label={active ? 'Stop and clear' : 'Clear'} onClick={() => act(api.previewsClear(j.id))}>×</button>
            </div>
            {open === j.id && p?.errors?.length ? (
              <div className="col renderwhy">
                {p.errors.map((e, i) => <span key={i} className="tiny"><b>{e.photo}</b>: {e.error}</span>)}
                {failed > p.errors.length && <span className="tiny muted">(the last {p.errors.length} shown; all are in the agent's log on {j.agent_name})</span>}
              </div>
            ) : null}
          </div>
        )
      })}
    </div>
  )
}
