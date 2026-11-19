import { useEffect, useState } from 'react'
import { ACTIONS, comboOf, isCustom, keysOf, resetAll, SCOPES, setKeys, useShortcutsVersion } from '../shortcuts'

const pretty = (k: string) => k.replace('ArrowRight', '→').replace('ArrowLeft', '←').replace('ArrowUp', '↑').replace('ArrowDown', '↓')

export function ShortcutsDialog({ onClose }: { onClose: () => void }) {
  useShortcutsVersion()
  const [rec, setRec] = useState<string | null>(null)   // action waiting for a key
  const [note, setNote] = useState('')

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (!rec) {  // while this is open, other shortcuts stay quiet
        if (e.key === 'Escape') { e.preventDefault(); onClose() }
        e.stopPropagation()
        return
      }
      e.preventDefault(); e.stopPropagation()
      const c = comboOf(e)
      if (!c) return  // a modifier alone: wait for the real key
      if (c === 'Escape' && rec) { setRec(null); return }
      const me = ACTIONS.find(a => a.id === rec)!
      const clash = ACTIONS.find(a => a.id !== rec && a.scope === me.scope && keysOf(a.id).includes(c))
      if (clash) setKeys(clash.id, keysOf(clash.id).filter(k => k !== c))
      if (!keysOf(rec).includes(c)) setKeys(rec, [...keysOf(rec), c])
      setNote(clash ? `${c} was used for “${clash.label}”; it now does “${me.label}”.` : '')
      setRec(null)
    }
    window.addEventListener('keydown', h, true)  // first, before any other shortcut
    return () => window.removeEventListener('keydown', h, true)
  }, [rec, onClose])

  return (
    <>
      <div className="backdrop" onClick={onClose} />
      <div className="dialog" role="dialog" aria-label="Keyboard shortcuts" style={{ width: 'min(640px, calc(100vw - 32px))', maxHeight: '88vh', overflow: 'auto' }}>
        <div className="row">
          <span className="display grow" style={{ fontSize: 19 }}>Keyboard shortcuts</span>
          <button className="btn ghost" onClick={() => { if (window.confirm('Put every shortcut back to how it was?')) resetAll() }}>Reset all</button>
        </div>
        <span className="small muted">Click “+” and press the key (with Ctrl, Alt or Shift if you like). Click a key to remove it. They're saved in the library, so every PC gets them.</span>
        {note && <span className="small" style={{ color: 'var(--accent-text)' }}>{note}</span>}
        {SCOPES.map(([scope, title]) => (
          <div key={scope} className="col" style={{ gap: 2 }}>
            <span className="caps" style={{ marginTop: 6 }}>{title}</span>
            {ACTIONS.filter(a => a.scope === scope).map(a => (
              <div key={a.id} className="row keyrow">
                <span className="grow small">{a.label}</span>
                {keysOf(a.id).map(k => (
                  <button key={k} className="kbd" title="Remove this key" onClick={() => setKeys(a.id, keysOf(a.id).filter(x => x !== k))}>{pretty(k)}</button>
                ))}
                <button className={'kbd add' + (rec === a.id ? ' rec' : '')} onClick={() => setRec(rec === a.id ? null : a.id)}>{rec === a.id ? 'press a key…' : '+'}</button>
                {isCustom(a.id) ? <button className="btn ghost tiny" title="Back to the default" onClick={() => setKeys(a.id, null)}>↺</button> : <span style={{ width: 26 }} />}
              </div>
            ))}
          </div>
        ))}
        <div className="row"><div className="grow" /><button className="btn" onClick={onClose}>Close</button></div>
      </div>
    </>
  )
}
