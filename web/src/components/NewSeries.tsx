// SPDX-License-Identifier: GPL-3.0-or-later
import { useState } from 'react'
import { useBackLayer } from '../back'

/** Phone: the name of a new series, asked in the page (window.prompt is unreliable in an Android web view).
 *  onCreate makes the series and says whether it worked (a name already taken keeps the sheet open). */
export function NewSeriesSheet({ onCreate, onClose }: { onCreate: (name: string) => Promise<boolean>; onClose: () => void }) {
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  useBackLayer(true, onClose)  // Back closes it
  const go = async () => {
    const n = name.trim()
    if (!n || busy) return
    setBusy(true)
    const ok = await onCreate(n)
    setBusy(false)
    if (ok) onClose()
  }
  return (
    <>
      <div className="backdrop" onClick={onClose} />
      <form className="sheet" role="dialog" aria-label="New series" onSubmit={e => { e.preventDefault(); go() }}>
        <span className="grab" />
        <div className="row"><span className="display grow" style={{ fontSize: 19, fontWeight: 500 }}>New series</span><button type="button" className="btn big" onClick={onClose}>Cancel</button></div>
        <input className="input" autoFocus style={{ height: 44, fontSize: 16 }} placeholder="Name of the new series" aria-label="Name of the new series" value={name} onChange={e => setName(e.target.value)} />
        <button type="submit" className="btn primary big" disabled={!name.trim() || busy}>Create series</button>
      </form>
    </>
  )
}
