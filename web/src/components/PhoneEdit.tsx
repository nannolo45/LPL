// SPDX-License-Identifier: GPL-3.0-or-later
import { useEffect, useRef, useState } from 'react'
import { useStore } from '../store'
import { isAndroidBrowser, phoneEditLink } from '../devices'

/** The current list's name for the phone app: the series, the folder or the tag it shows. */
export function useListTitle(): { series: number | null; title: string | null } {
  const s = useStore()
  const sid = s.filters.series ?? null
  if (sid != null) return { series: sid, title: s.series.find(x => x.id === sid)?.name ?? null }
  if (s.filters.folder) return { series: null, title: s.filters.folder.split('/').pop() || null }
  return { series: null, title: s.filters.tag ?? null }
}

/** "Edit on this phone": only in a browser on Android and not in local mode. A plain link the user taps (Android
 *  opens apps only on a tap), so no job is made. Chrome goes to android-app.html by itself when the app is
 *  missing; Firefox doesn't reliably, so if the page is still showing a moment after the tap, a note says so. */
export function EditOnPhone({ ids, start, series, title, className = 'btn big', label = 'Edit on this phone' }: {
  ids: number[]; start: number; series?: number | null; title?: string | null; className?: string; label?: string
}) {
  const s = useStore()
  const [missing, setMissing] = useState(false)
  const timer = useRef<number>()
  useEffect(() => {
    const h = () => { if (document.hidden) { window.clearTimeout(timer.current); setMissing(false) } }
    document.addEventListener('visibilitychange', h)
    return () => { document.removeEventListener('visibilitychange', h); window.clearTimeout(timer.current) }
  }, [])
  if (!isAndroidBrowser() || !s.status || s.isLocal) return null
  const href = phoneEditLink({ origin: location.origin, ids, start, series, title })
  const tapped = () => {
    setMissing(false)
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => { if (!document.hidden) setMissing(true) }, 1800)
  }
  return (
    <>
      <a className={className} href={href} onClick={tapped}
        style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', textDecoration: 'none' }}>{label}</a>
      {missing && (
        <div className="toast row" role="status" style={{ gap: 10, bottom: 'calc(80px + env(safe-area-inset-bottom))' }}>
          <span className="grow">The darktable app didn't open. Not on this phone yet? <a href="/android-app.html" style={{ color: 'inherit', fontWeight: 600 }}>How to get it</a></span>
          <button className="btn ghost tiny" style={{ color: 'inherit' }} aria-label="Close" onClick={() => setMissing(false)}>×</button>
        </div>
      )}
    </>
  )
}
