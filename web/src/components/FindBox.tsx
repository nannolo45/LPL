// SPDX-License-Identifier: GPL-3.0-or-later
import { useEffect, useRef } from 'react'
import { useStore } from '../store'
import { getFindState, setFindState, useFindState } from '../find'
import { actionFor, keysOf, typingIn } from '../shortcuts'

export const SearchIcon = ({ size = 14 }: { size?: number }) => <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" /><path d="M15.5 15.5L21 21" /></svg>

/** Find a photo by file name in the open series or folder (board and strip).
 *  variant "top": the top bar's search box while the Series view is open (desktop; Ctrl+F); "phone": a row in the Series header.
 *  Enter / ↓ next, Shift+Enter / ↑ previous, Esc clears (a second Esc leaves the box). */
export function FindBox({ variant, onClose }: { variant: 'top' | 'phone'; onClose?: () => void }) {
  const s = useStore()
  const { req, count, where } = useFindState()
  const setReq = (r: typeof req) => setFindState({ req: r })
  const inp = useRef<HTMLInputElement>(null)
  const phone = variant === 'phone'
  useEffect(() => {
    if (phone) { inp.current?.focus(); return }
    const h = (e: KeyboardEvent) => {
      if (s.viewer != null || s.fullscreen || typingIn(e.target)) return
      if (actionFor(e, ['board']) === 'findPhoto') { e.preventDefault(); inp.current?.focus(); inp.current?.select() }
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [phone, s.viewer, s.fullscreen])
  const step = (dir: 1 | -1) => { const r = getFindState().req; setReq({ ...r, n: r.n + 1, dir }) }
  const clear = () => setReq({ q: '', n: getFindState().req.n, dir: 1 })
  const has = !!req.q.trim()
  const none = has && count && !count.total
  const tally = !has || !count ? '' : count.total ? `${count.at}/${count.total}` : count.hidden ? `0 · ${count.hidden} hidden` : '0'
  const keys = keysOf('findPhoto')
  const placeholder = phone ? 'Find a photo by name' : where ? `Find a photo in ${where} by file name…` : 'Find a photo by file name…'
  return (
    <label className={(phone ? 'sv-find phone' : 'search sv-find-top') + (none ? ' none' : '')} role="search">
      <SearchIcon size={phone ? 14 : 15} />
      <input ref={inp} value={req.q} placeholder={placeholder} aria-label="Find a photo by file name"
        title={`Find a photo by file name${keys.length ? ` (${keys.join(', ')})` : ''}. Enter: next, Shift+Enter: previous, Esc: clear`}
        enterKeyHint="search" autoComplete="off" spellCheck={false}
        onChange={e => setReq({ q: e.target.value, n: req.n, dir: 1 })}
        onKeyDown={e => {
          if (e.key === 'Enter' || e.key === 'ArrowDown') { e.preventDefault(); if (has) step(e.shiftKey ? -1 : 1) }
          else if (e.key === 'ArrowUp') { e.preventDefault(); if (has) step(-1) }
          else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); if (req.q) clear(); else { inp.current?.blur(); onClose?.() } }
        }} />
      {tally && <span className="sv-find-n" role="status" title={count?.hidden ? `${count.hidden} matching photo${count.hidden === 1 ? ' is' : 's are'} hidden from the library` : undefined}>{tally}</span>}
      {has && !!count?.total && count.total > 1 && <>
        <button type="button" className="sv-find-b" aria-label="Previous match" title="Previous (Shift+Enter)" onClick={() => step(-1)}>↑</button>
        <button type="button" className="sv-find-b" aria-label="Next match" title="Next (Enter)" onClick={() => step(1)}>↓</button>
      </>}
      {(has || onClose) && <button type="button" className="sv-find-b" aria-label={has ? 'Clear' : 'Close'} title={has ? 'Clear (Esc)' : 'Close'} onClick={e => { e.preventDefault(); if (has) { clear(); inp.current?.focus() } else onClose?.() }}>×</button>}
    </label>
  )
}
