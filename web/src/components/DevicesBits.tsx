// SPDX-License-Identifier: GPL-3.0-or-later
// Small pieces the Devices page, the top bar's indicator and Settings > Devices & library share.
import { useEffect, useState } from 'react'

export const Chevron = ({ open }: { open: boolean }) => (
  <svg className={'chev' + (open ? ' open' : '')} width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M9 5l7 7-7 7" /></svg>
)
export const StopIcon = () => <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="1.5" fill="currentColor" /></svg>
export const PlayIcon = () => <svg width="12" height="12" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13l11-6.5z" fill="currentColor" /></svg>
export const CrossIcon = () => <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>

/** The overall bar of a batch. still: nothing moves (stopped, or the device is not reporting). */
export function Bar({ fraction, label, still, thin }: { fraction: number; label: string; still?: boolean; thin?: boolean }) {
  const pct = Math.round(Math.min(1, Math.max(0, fraction)) * 100)
  return (
    <span className={'xbar' + (still ? ' still' : '') + (thin ? ' thin' : '')} role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
      <i style={{ width: `${pct}%` }} />
    </span>
  )
}

// A section of the Devices page that folds (styles and presets, LUT files): the heading with a one-line summary is
// all that shows when folded. Folded by default; remembered on this browser. "Fold all" / "Unfold all" reach it too.
const FOLD_EVENT = 'lpl-devsections'
const foldKey = (id: string) => 'lpl.devsection.' + id
export function foldAllSections(folded: boolean) {
  try { for (const id of ['shared', 'luts']) localStorage.setItem(foldKey(id), folded ? '1' : '0') } catch { /* ignore */ }
  window.dispatchEvent(new CustomEvent(FOLD_EVENT, { detail: folded }))
}
export function useSectionFold(id: string): [boolean, (v: boolean) => void] {
  const read = () => { try { return localStorage.getItem(foldKey(id)) !== '0' } catch { return true } }
  const [folded, setF] = useState(read)
  useEffect(() => {
    const h = (e: Event) => setF(!!(e as CustomEvent).detail)
    window.addEventListener(FOLD_EVENT, h)
    return () => window.removeEventListener(FOLD_EVENT, h)
  }, [])
  const set = (v: boolean) => { setF(v); try { localStorage.setItem(foldKey(id), v ? '1' : '0') } catch { /* ignore */ } }
  return [folded, set]
}
export function FoldSection({ id, title, summary, warn, phone, children }: { id: string; title: string; summary: string; warn?: boolean; phone?: boolean; children: React.ReactNode }) {
  const [folded, setFolded] = useSectionFold(id)
  return (
    <section className={'col sharedsection devsection' + (folded ? '' : ' open')} style={{ gap: 10 }} aria-label={title}>
      <button className="devfold devsechead" aria-expanded={!folded} onClick={() => setFolded(!folded)}>
        <Chevron open={!folded} /><span className="display" style={{ fontSize: phone ? 18 : 20 }}>{title}</span>
        <span className={'small ' + (warn ? 'sharedwarn' : 'muted')}>{summary}</span>
      </button>
      {!folded && children}
    </section>
  )
}

/** The first `step` rows of a long list, and a "show all" line (folds back). */
export function useShowMore<T>(list: T[], step = 25): { shown: T[]; more: React.ReactNode } {
  const [all, setAll] = useState(false)
  if (list.length <= step + 5) return { shown: list, more: null }
  return {
    shown: all ? list : list.slice(0, step),
    more: <button className="devmore" aria-expanded={all} onClick={() => setAll(!all)}><Chevron open={all} />{all ? 'show fewer' : `${list.length - step} more · show them`}</button>,
  }
}
