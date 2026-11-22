// SPDX-License-Identifier: GPL-3.0-or-later
import { useCallback, useEffect, useRef, useState } from 'react'
import { useStore } from '../store'
import { useBackLayer } from '../back'

/** A question asked in the page: a name to type, or a yes/no with its consequences spelled out. A dialog on the
 *  desktop, a sheet on the phone (window.prompt and window.confirm are unreliable in an Android web view);
 *  Escape, the backdrop and the phone's Back cancel. */
export interface AskOpts {
  title: string
  /** plain words under the title: what will happen */
  text?: string
  /** ask for a name: the field's label; without it this is a yes/no question */
  field?: string
  initial?: string
  ok: string
  danger?: boolean
}
type Open = AskOpts & { done: (v: string | null) => void }

function AskBox({ q }: { q: Open }) {
  const s = useStore()
  const [v, setV] = useState(q.initial || '')
  const inp = useRef<HTMLInputElement>(null)
  useBackLayer(true, () => q.done(null))
  useEffect(() => { inp.current?.focus(); inp.current?.select() }, [])
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); q.done(null) } }
    window.addEventListener('keydown', h, true)
    return () => window.removeEventListener('keydown', h, true)
  }, [q])
  const can = !q.field || !!v.trim()
  const big = s.isMobile ? ' big' : ''
  return (
    <>
      <div className="backdrop" onClick={() => q.done(null)} />
      <form className={s.isMobile ? 'sheet' : 'dialog'} role="dialog" aria-label={q.title} onSubmit={e => { e.preventDefault(); if (can) q.done(q.field ? v.trim() : 'yes') }}>
        {s.isMobile && <span className="grab" />}
        <span className="display" style={{ fontSize: 19, fontWeight: 500 }}>{q.title}</span>
        {q.text && <span className="soft" style={{ lineHeight: 1.5 }}>{q.text}</span>}
        {q.field && <input ref={inp} className="input" style={s.isMobile ? { height: 44, fontSize: 16 } : undefined} placeholder={q.field} aria-label={q.field} value={v} onChange={e => setV(e.target.value)} />}
        <div className="row" style={{ justifyContent: 'flex-end' }}>
          <button type="button" className={'btn' + big} onClick={() => q.done(null)}>Cancel</button>
          <button type="submit" className={'btn ' + (q.danger ? 'danger' : 'primary') + big} disabled={!can} autoFocus={!q.field}>{q.ok}</button>
        </div>
      </form>
    </>
  )
}

/** `const [node, ask] = useAsk()`: render `node`, and `await ask({...})` gives the typed name, 'yes', or null when cancelled. */
export function useAsk(): [React.ReactNode, (o: AskOpts) => Promise<string | null>] {
  const [q, setQ] = useState<Open | null>(null)
  const ask = useCallback((o: AskOpts) => new Promise<string | null>(resolve => {
    setQ({ ...o, done: v => { setQ(null); resolve(v) } })
  }), [])
  return [q ? <AskBox key={q.title} q={q} /> : null, ask]
}
