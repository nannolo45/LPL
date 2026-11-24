// SPDX-License-Identifier: GPL-3.0-or-later
// Find a photo by name in the Series view (board and strip): the box is in the Series view's header, the views
// find the photos, go to them one by one and mark them.
import { useEffect, useMemo, useRef, useState } from 'react'

/** What the find box asks for: the text, and `n` goes up by one for every "next" (dir 1) or "previous" (dir -1). */
export interface FindReq { q: string; n: number; dir: 1 | -1 }
/** What the view found: `at` is 1-based (0: none), `hidden` = matches among photos hidden from the library (not shown). */
export interface FindCount { total: number; at: number; hidden: number }

/** Every word of the query must be in the file name (case and accents ignored), e.g. "0831 raf" finds DSCF0831.RAF. */
export function findWords(q: string): string[] {
  return norm(q).split(/\s+/).filter(Boolean)
}
const norm = (x: string) => x.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
export function nameMatches(filename: string, words: string[]): boolean {
  if (!words.length) return false
  const f = norm(filename)
  return words.every(w => f.includes(w))
}

/** The photos of `list` (in the view's order) that match, the current one, and going to it.
 *  `reveal` is called when the current match changes, when the query changes and when the view becomes active.
 *  Returns the ids of the matches, to mark them. */
export function useFind<T extends { id: number; filename: string }>(opts: {
  list: T[]; all?: T[]; req?: FindReq; active: boolean
  reveal: (p: T) => void; onCount?: (c: FindCount) => void
}): Set<number> {
  const { list, all, req, active } = opts
  const q = req?.q || ''
  const words = useMemo(() => findWords(q), [q])
  const matches = useMemo(() => (words.length ? list.filter(p => nameMatches(p.filename, words)) : []), [list, words])
  const hidden = useMemo(() => (words.length && all ? all.filter(p => nameMatches(p.filename, words)).length - matches.length : 0), [all, words, matches.length])
  const ids = useMemo(() => new Set(matches.map(p => p.id)), [matches])
  const [cur, setCur] = useState<number | null>(null)   // the id of the current match
  const reveal = useRef(opts.reveal); reveal.current = opts.reveal
  const onCount = useRef(opts.onCount); onCount.current = opts.onCount
  const seen = useRef({ q: '', active: false, n: req?.n ?? 0, had: false })

  // the query, the photos or the view changed: keep the current match if it is still there, else start from the first
  useEffect(() => {
    const was = seen.current
    seen.current = { ...was, q, active, had: matches.length > 0 }
    if (!matches.length) { setCur(null); return }
    const fresh = was.q !== q || !matches.some(p => p.id === cur)
    const next = fresh ? matches[0] : matches.find(p => p.id === cur)!
    setCur(next.id)
    if (active && (fresh || !was.active || !was.had)) reveal.current(next)
  }, [q, active, matches])

  // next / previous
  useEffect(() => {
    const n = req?.n ?? 0
    if (n === seen.current.n) return
    seen.current.n = n
    if (!active || !matches.length) return
    const k = matches.findIndex(p => p.id === cur)
    const dir = req?.dir ?? 1
    const next = matches[k < 0 ? (dir === 1 ? 0 : matches.length - 1) : (k + dir + matches.length) % matches.length]
    setCur(next.id)
    reveal.current(next)
  }, [req?.n])

  useEffect(() => {
    if (!active) return
    const k = matches.findIndex(p => p.id === cur)
    onCount.current?.({ total: matches.length, at: k + 1, hidden })
  }, [active, matches, cur, hidden])
  return ids
}
