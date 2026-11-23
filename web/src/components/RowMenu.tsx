// SPDX-License-Identifier: GPL-3.0-or-later
// The ⋯ menu of a row on the Devices page (and in its full view): what a photo can do besides its main button.
import { useEffect, useRef, useState } from 'react'

export type MenuItem = { sep: true } | { label: string; hint?: string; title?: string; danger?: boolean; run: () => unknown; sep?: false }

export function RowMenu({ label, items, big }: { label: string; items: MenuItem[]; big?: boolean }) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent | TouchEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false) }
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); setOpen(false) } }
    document.addEventListener('mousedown', away); document.addEventListener('touchstart', away); document.addEventListener('keydown', esc, true)
    root.current?.querySelector<HTMLButtonElement>('[role=menuitem]')?.focus()
    return () => { document.removeEventListener('mousedown', away); document.removeEventListener('touchstart', away); document.removeEventListener('keydown', esc, true) }
  }, [open])
  const arrows = (e: React.KeyboardEvent) => {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
    e.preventDefault()
    const all = Array.from(root.current?.querySelectorAll<HTMLButtonElement>('[role=menuitem]') || [])
    const i = all.indexOf(document.activeElement as HTMLButtonElement)
    all[(i + (e.key === 'ArrowDown' ? 1 : all.length - 1)) % all.length]?.focus()
  }
  return (
    <div className="rowmenu" ref={root} onKeyDown={arrows}>
      <button type="button" className={'btn more' + (big ? ' big' : '')} aria-label={label} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>⋯</button>
      {open && (
        <div role="menu" aria-label={label} className="rowmenu-list">
          {items.map((it, i) => it.sep ? <div key={i} className="rowmenu-sep" role="separator" />
            : <button key={i} type="button" role="menuitem" className={it.danger ? 'danger' : undefined} title={it.title}
                onClick={() => { setOpen(false); it.run() }}><span>{it.label}</span>{it.hint && <span className="tiny muted">{it.hint}</span>}</button>)}
        </div>
      )}
    </div>
  )
}
