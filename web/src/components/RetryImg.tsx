import { useEffect, useState } from 'react'

// A preview that may not exist yet: the server makes it next when asked (404 = "coming"),
// so retry a few times with growing pauses instead of leaving an empty box until the page is reloaded.
const WAITS = [2000, 4000, 8000, 15000, 30000, 60000]

export function RetryImg({ src, alt = '', className, style, placeholder, lazy = true }: {
  src: string; alt?: string; className?: string; style?: React.CSSProperties; placeholder?: React.ReactNode; lazy?: boolean
}) {
  const [tries, setTries] = useState(0)
  const [waiting, setWaiting] = useState(false)
  useEffect(() => { setTries(0); setWaiting(false) }, [src])
  useEffect(() => {
    if (!waiting) return
    if (tries >= WAITS.length) return
    const t = window.setTimeout(() => { setTries(n => n + 1); setWaiting(false) }, WAITS[tries])
    return () => window.clearTimeout(t)
  }, [waiting, tries])
  const url = tries ? `${src}${src.includes('?') ? '&' : '?'}r=${tries}` : src
  return (
    <>
      {waiting && (placeholder ?? null)}
      <img key={url} className={className} alt={alt} src={url} loading={lazy ? 'lazy' : undefined} decoding="async"
        style={waiting ? { ...style, display: 'none' } : style} onError={() => setWaiting(true)} />
    </>
  )
}
