// SPDX-License-Identifier: GPL-3.0-or-later
// Import from Miro: going through the picture matches full screen, one at a time, with the keyboard.
// Miro's picture on the left, the library photo on the right; the next closest photos below to pick instead.
import { useEffect, useMemo, useState } from 'react'
import { MiroPreview } from '../api'
import { useBackLayer } from '../back'

type Item = MiroPreview['check'][number]
type Show = 'review' | 'sure' | 'all'
const KIND: Record<Item['kind'], string> = { check: 'probably right', guess: 'unsure: not taken unless you say so', sure: 'sure' }

export function MiroReview({ items, pkey, choice, setChoice, onClose }: {
  items: Item[]; pkey: string; choice: Record<string, number | null>; setChoice: (mid: string, pid: number | null) => void; onClose: () => void
}) {
  const [show, setShow] = useState<Show>(items.some(i => i.kind !== 'sure') ? 'review' : 'all')
  const list = useMemo(() => items.filter(i => show === 'all' || (show === 'sure' ? i.kind === 'sure' : i.kind !== 'sure')), [items, show])
  const [at, setAt] = useState(0)
  const [cand, setCand] = useState<number | null>(null)  // the photo shown on the right, if not the chosen one
  const [flip, setFlip] = useState(false)               // Space held: Miro's picture in the photo's place
  useBackLayer(true, onClose)
  const i = Math.min(at, Math.max(0, list.length - 1))
  const it = list[i]
  const chosen = it ? choice[it.miro_id] : null
  const right = it ? (cand ?? chosen ?? it.photo) : null
  const go = (d: number) => { setCand(null); setAt(Math.max(0, Math.min(list.length - 1, i + d))) }
  const decide = (pid: number | null) => { if (!it) return; setChoice(it.miro_id, pid); go(1) }

  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement) return
      const k = e.key.toLowerCase()
      let used = true
      if (k === 'escape') onClose()
      else if (k === 'arrowright' || k === 'arrowdown') go(1)
      else if (k === 'arrowleft' || k === 'arrowup') go(-1)
      else if (k === 'y' || k === 'enter') decide(right)
      else if (k === 'n' || k === 'x' || k === 'delete' || k === 'backspace') decide(null)
      else if (/^[1-5]$/.test(k) && it?.alts[+k - 1] != null) { setCand(it.alts[+k - 1]); }
      else if (k === ' ') setFlip(true)
      else used = false
      if (used) { e.preventDefault(); e.stopPropagation() }
    }
    const up = (e: KeyboardEvent) => { if (e.key === ' ') setFlip(false) }
    window.addEventListener('keydown', down, true)
    window.addEventListener('keyup', up, true)
    return () => { window.removeEventListener('keydown', down, true); window.removeEventListener('keyup', up, true) }
  })

  const counts = { review: items.filter(x => x.kind !== 'sure').length, sure: items.filter(x => x.kind === 'sure').length, all: items.length }
  const taken = list.filter(x => choice[x.miro_id] != null).length
  return (
    <div className="mreview" role="dialog" aria-label="Check the picture matches">
      <div className="mreview-top">
        <div className="row" style={{ gap: 6 }}>
          {(['review', 'sure', 'all'] as Show[]).map(k => counts[k] > 0 || k === 'all'
            ? <button key={k} className={'chip' + (show === k ? ' on' : '')} onClick={() => { setShow(k); setAt(0); setCand(null) }}>{k === 'review' ? 'To check' : k === 'sure' ? 'Sure' : 'All'} {counts[k]}</button> : null)}
        </div>
        <span className="small muted grow" style={{ textAlign: 'center' }}>{list.length ? `${i + 1} of ${list.length} · ${taken} taken` : 'Nothing here'}</span>
        <button className="btn" onClick={onClose}>Done</button>
      </div>
      {it ? <>
        <div className="mreview-main">
          <figure><img src={`/api/miro/pic/${pkey}/${it.miro_id}`} alt="Miro picture" /><figcaption>Miro</figcaption></figure>
          <figure className={chosen == null && cand == null ? 'out' : ''}>
            {flip ? <img src={`/api/miro/pic/${pkey}/${it.miro_id}`} alt="Miro picture" /> : right != null && <img src={`/api/preview/${right}?s=l`} alt="Library photo" />}
            <figcaption>{flip ? 'Miro (Space held)' : cand != null && cand !== chosen ? 'Candidate: Y takes it'
              : chosen != null ? 'Taken' : 'Left out: Y takes it'} · {KIND[it.kind]}</figcaption>
          </figure>
        </div>
        <div className="mreview-bottom">
          <div className="mreview-alts">
            {it.alts.map((a, n) => (
              <button key={a} className={'malt' + (a === right ? ' on' : '') + (a === chosen ? ' chosen' : '')} onClick={() => setCand(a)} title={`Show this one (${n + 1})`}>
                <img src={`/api/preview/${a}?s=t`} alt="" /><span>{n + 1}</span>
              </button>
            ))}
          </div>
          <div className="row" style={{ gap: 6 }}>
            <button className="btn" onClick={() => go(-1)} disabled={i === 0}>← Back</button>
            <button className="btn danger" onClick={() => decide(null)}>Leave out (N)</button>
            <button className="btn primary" onClick={() => decide(right)}>Take this one (Y)</button>
            <button className="btn" onClick={() => go(1)} disabled={i >= list.length - 1}>Next →</button>
          </div>
          <span className="tiny muted">Y / Enter take · N / X leave out · 1–5 show another photo · ← → move · hold Space to see Miro’s picture in the photo’s place · Esc back to the summary</span>
        </div>
      </> : <div className="center muted">No matches in this list.</div>}
    </div>
  )
}
