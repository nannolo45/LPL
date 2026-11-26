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
import { pace } from '../renderpace'

type Toast = (m: string) => void
const ASK = 'lpl-render-ask'
const REFRESH = 'lpl-render-refresh'

/** Open the dialog for `scope`. what: how the photos are named in it ("“Grecia”", "the folder 2023/Athens"). */
export function updatePreviews(scope: RenderScope, what: string, _toast?: Toast, notOn?: string) {
  window.dispatchEvent(new CustomEvent(ASK, { detail: { scope, what, notOn } }))
}

/** The dialog: how many photos, which PCs (all that can render, ticked), render. Mounted once in the app. */
export function RenderDialog() {
  const s = useStore()
  const [ask, setAsk] = useState<{ scope: RenderScope; what: string; notOn?: string } | null>(null)
  const [n, setN] = useState<{ stale: number; all: number } | null>(null)
  const [pcs, setPcs] = useState<{ id: string; name: string; darktable?: string | null }[]>([])
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
          const others = st.agents.filter(p => p.id !== d.notOn)  // notOn: the PC that could not render them
          const want = others.filter(p => keep.includes(p.id))
          setPicked(new Set((d.notOn ? others : want.length ? want : st.agents).map(p => p.id)))
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
              {new Set(pcs.map(p => p.darktable).filter(Boolean)).size > 1 && (
                <span className="tiny sharedwarn">These PCs have different darktable versions: an edit made with a module one of them lacks is left for the other.</span>
              )}
              {pcs.length ? pcs.map(p => (
                <label key={p.id} className="row small" style={{ gap: 8 }}>
                  <input type="checkbox" checked={picked.has(p.id)} onChange={e => {
                    const x = new Set(picked); if (e.target.checked) x.add(p.id); else x.delete(p.id); setPicked(x)
                  }} />
                  {p.name}{p.darktable && <span className="muted"> · {p.darktable}</span>}
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
    <div className={'renderbar' + (s.isMobile ? ' phone' : '')} role="status">
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
            {p?.lacking?.length ? (
              <div className="row renderline" style={{ gap: 10 }}>
                <span className="small grow">
                  {p.lacking.length} photo{p.lacking.length === 1 ? '' : 's'} use{p.lacking.length === 1 ? 's' : ''} {(p.lacking_modules || []).join(', ') || 'a darktable module'}, which darktable on {j.agent_name} does not have.
                </span>
                <button className="btn tiny" onClick={() => updatePreviews({ ids: p.lacking }, `the ${p.lacking!.length} photo${p.lacking!.length === 1 ? '' : 's'} ${j.agent_name} could not render`, undefined, j.agent_id)}>
                  Render {p.lacking.length === 1 ? 'it' : 'them'} on another PC…
                </button>
              </div>
            ) : null}
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
