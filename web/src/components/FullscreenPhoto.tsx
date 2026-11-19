import { useEffect, useRef, useState } from 'react'
import { Photo, thumbUrl } from '../api'
import { useStore } from '../store'
import { exifLine, fmtDay, stars } from '../fields'
import { actionFor } from '../shortcuts'

// Full screen photo: the whole screen (the browser's real full screen where allowed), black, nothing else.
// Phone: swipe left/right for the next/previous, swipe down or Back to close, pinch or double-tap to zoom,
// tap to show/hide the caption. Desktop: arrows, wheel or double-click to zoom, drag to pan, Esc to close.
export function FullscreenPhoto() {
  const s = useStore()
  const fs = s.fullscreen!
  const [i, setI] = useState(fs.index)
  const [chrome, setChrome] = useState(true)
  const [z, setZ] = useState({ k: 1, x: 0, y: 0 })   // zoom and pan (in screen pixels)
  const [swipe, setSwipe] = useState({ dx: 0, dy: 0 })
  const root = useRef<HTMLDivElement>(null)
  const ptrs = useRef(new Map<number, { x: number; y: number }>())
  const g = useRef<{ sx: number; sy: number; moved: boolean; pinch?: { d: number; k: number; cx: number; cy: number; x: number; y: number }; pan?: { x: number; y: number } }>({ sx: 0, sy: 0, moved: false })
  const lastTap = useRef(0)
  const tapTimer = useRef<number>()
  const wentFull = useRef(false)
  const p: Photo | undefined = fs.list[i]

  const close = () => {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {})
    if (history.state?.lplFull) history.back()
    s.setFullscreen(null)
  }
  const go = (d: number) => {
    const n = i + d
    if (n < 0 || n >= fs.list.length) return
    setI(n); setZ({ k: 1, x: 0, y: 0 })
    s.setSel([fs.list[n].id])
  }

  // real full screen + the phone's Back button closes it
  useEffect(() => {
    const el = root.current
    el?.requestFullscreen?.().then(() => { wentFull.current = true }).catch(() => {})
    history.pushState({ lplFull: 1 }, '')
    const onPop = () => { if (document.fullscreenElement) document.exitFullscreen().catch(() => {}); s.setFullscreen(null) }
    const onFs = () => { if (!document.fullscreenElement && wentFull.current) close() }
    window.addEventListener('popstate', onPop)
    document.addEventListener('fullscreenchange', onFs)
    return () => { window.removeEventListener('popstate', onPop); document.removeEventListener('fullscreenchange', onFs) }
  }, [])

  // keys come here first while it's open
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      e.stopPropagation()
      const a = actionFor(e, ['viewer'])
      if (a === 'close' || e.key === 'Escape') { e.preventDefault(); close() }
      else if (a === 'next') { e.preventDefault(); go(1) }
      else if (a === 'prev') { e.preventDefault(); go(-1) }
      else if (actionFor(e, ['photos']) === 'fullscreen') { e.preventDefault(); close() }
    }
    window.addEventListener('keydown', h, true)
    return () => window.removeEventListener('keydown', h, true)
  })

  const clampPan = (k: number, x: number, y: number) => {
    const w = window.innerWidth, h = window.innerHeight
    const mx = (w * (k - 1)) / 2, my = (h * (k - 1)) / 2
    return { k, x: Math.max(-mx, Math.min(mx, x)), y: Math.max(-my, Math.min(my, y)) }
  }
  const zoomAt = (k: number, cx: number, cy: number, base = z) => {
    const nk = Math.max(1, Math.min(6, k))
    // keep the point under (cx, cy) where it is
    const ox = cx - window.innerWidth / 2, oy = cy - window.innerHeight / 2
    const x = ox - ((ox - base.x) * nk) / base.k, y = oy - ((oy - base.y) * nk) / base.k
    return nk === 1 ? { k: 1, x: 0, y: 0 } : clampPan(nk, x, y)
  }

  const down = (e: React.PointerEvent) => {
    (e.currentTarget as Element).setPointerCapture?.(e.pointerId)
    ptrs.current.set(e.pointerId, { x: e.clientX, y: e.clientY })
    const pts = [...ptrs.current.values()]
    if (pts.length === 2) {
      const [a, b] = pts
      g.current.pinch = { d: Math.hypot(a.x - b.x, a.y - b.y), k: z.k, cx: (a.x + b.x) / 2, cy: (a.y + b.y) / 2, x: z.x, y: z.y }
      g.current.moved = true
      setSwipe({ dx: 0, dy: 0 })
    } else if (pts.length === 1) {
      g.current = { sx: e.clientX, sy: e.clientY, moved: false, pan: z.k > 1 ? { x: z.x, y: z.y } : undefined }
    }
  }
  const move = (e: React.PointerEvent) => {
    if (!ptrs.current.has(e.pointerId)) return
    ptrs.current.set(e.pointerId, { x: e.clientX, y: e.clientY })
    const pts = [...ptrs.current.values()]
    const st = g.current
    if (pts.length >= 2 && st.pinch) {
      const [a, b] = pts
      const k = st.pinch.k * Math.hypot(a.x - b.x, a.y - b.y) / st.pinch.d
      setZ(zoomAt(k, st.pinch.cx, st.pinch.cy, { k: st.pinch.k, x: st.pinch.x, y: st.pinch.y }))
      return
    }
    const dx = e.clientX - st.sx, dy = e.clientY - st.sy
    if (!st.moved && Math.hypot(dx, dy) < 8) return
    st.moved = true
    if (st.pan) setZ(clampPan(z.k, st.pan.x + dx, st.pan.y + dy))
    else setSwipe({ dx, dy: Math.max(0, dy) })
  }
  const up = (e: React.PointerEvent) => {
    ptrs.current.delete(e.pointerId)
    const st = g.current
    if (ptrs.current.size) return  // still pinching with the other finger
    if (st.pinch) { g.current = { sx: 0, sy: 0, moved: true }; return }
    if (!st.moved) {
      const now = Date.now()
      if (now - lastTap.current < 300) {  // double tap / double click: zoom in there, or back out
        window.clearTimeout(tapTimer.current); lastTap.current = 0
        setZ(z.k > 1 ? { k: 1, x: 0, y: 0 } : zoomAt(2.5, e.clientX, e.clientY))
      } else {
        lastTap.current = now
        tapTimer.current = window.setTimeout(() => setChrome(c => !c), 260)
      }
      return
    }
    if (!st.pan) {
      const { dx, dy } = swipe
      setSwipe({ dx: 0, dy: 0 })
      if (dy > 120 && Math.abs(dx) < dy) close()
      else if (dx < -60) go(1)
      else if (dx > 60) go(-1)
    }
  }
  const wheel = (e: React.WheelEvent) => setZ(zoomAt(z.k * (e.deltaY < 0 ? 1.15 : 1 / 1.15), e.clientX, e.clientY))

  if (!p) return null
  const tr = z.k > 1 ? `translate(${z.x}px, ${z.y}px) scale(${z.k})` : `translate(${swipe.dx}px, ${swipe.dy}px)`
  return (
    <div className="fsphoto" ref={root} role="dialog" aria-label={`${p.filename}, full screen`}
      onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up} onWheel={wheel}
      style={{ background: `rgba(0,0,0,${1 - Math.min(.6, swipe.dy / 400)})` }}>
      <img src={thumbUrl(p, 'l')} alt={p.filename} draggable={false} style={{ transform: tr, transition: swipe.dx || swipe.dy || ptrs.current.size ? 'none' : 'transform .18s ease-out' }} />
      {chrome && (
        <>
          <div className="fs-top" onPointerDown={e => e.stopPropagation()}>
            <div className="col grow" style={{ gap: 2, minWidth: 0 }}>
              <b className="ellipsis">{p.filename} {p.rating > 0 ? <span style={{ color: '#f3e3b0' }}>{stars(p.rating)}</span> : null}</b>
              <span className="small ellipsis" style={{ opacity: .75 }}>{p.taken ? fmtDay(p.taken) + ' ' + p.taken.slice(11, 16) : ''} · {exifLine(p)}</span>
            </div>
            <span className="small" style={{ opacity: .7 }}>{i + 1} / {fs.list.length}</span>
            <button onClick={close} aria-label="Close full screen">✕</button>
          </div>
          {!s.isMobile && i > 0 && <button className="fs-nav" style={{ left: 14 }} onPointerDown={e => e.stopPropagation()} onClick={() => go(-1)} aria-label="Previous">‹</button>}
          {!s.isMobile && i < fs.list.length - 1 && <button className="fs-nav" style={{ right: 14 }} onPointerDown={e => e.stopPropagation()} onClick={() => go(1)} aria-label="Next">›</button>}
        </>
      )}
    </div>
  )
}
