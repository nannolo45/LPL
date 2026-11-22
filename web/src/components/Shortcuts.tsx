import { useEffect, useState } from 'react'
import { ACTIONS, comboOf, isCustom, keysOf, resetAll, SCOPES, setKeys, useShortcutsVersion } from '../shortcuts'
import { DEFAULT_SETTINGS, resetSettings, setSetting, Settings, useSettings } from '../settings'
import { FRAME_COLORS, NOTE_COLORS } from './Board'
import { useStore } from '../store'
import { useInstall } from '../install'
import { useBackLayer } from '../back'
import { DevicesSettings } from './DevicesSettings'

const pretty = (k: string) => k.replace('ArrowRight', '→').replace('ArrowLeft', '←').replace('ArrowUp', '↑').replace('ArrowDown', '↓')

export type SettingsSection = 'board' | 'keys' | 'devices'

/** Settings: board options and keyboard shortcuts, saved in the library so every PC (and the phone) gets them. */
export function SettingsDialog({ onClose, section = 'board' }: { onClose: () => void; section?: SettingsSection }) {
  const s = useStore()
  const [sec, setSec] = useState<SettingsSection>(s.isMobile && section === 'keys' ? 'board' : section)  // (no keyboard on the phone)
  const [rec, setRec] = useState<string | null>(null)   // shortcut waiting for a key
  const [note, setNote] = useState('')
  useBackLayer(s.isMobile, onClose)  // the phone's Back closes it

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement
      if (!rec) {  // while this is open, other shortcuts stay quiet
        if (e.key === 'Escape') { e.preventDefault(); onClose() }
        if (!(t && (t.tagName === 'INPUT' || t.tagName === 'SELECT'))) e.stopPropagation()
        return
      }
      e.preventDefault(); e.stopPropagation()
      const c = comboOf(e)
      if (!c) return  // a modifier alone: wait for the real key
      if (c === 'Escape') { setRec(null); return }
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
      <div className={s.isMobile ? 'sheet' : 'dialog settings'} role="dialog" aria-label="Settings">
        {s.isMobile && <span className="grab" />}
        <div className="row">
          <span className="display grow" style={{ fontSize: 19 }}>Settings</span>
          <button className="btn" onClick={onClose}>Close</button>
        </div>
        <InstallRow />
        <div className="settings-body">
          {!s.isMobile && (
            <nav className="settings-nav" aria-label="Settings sections">
              <button className={sec === 'board' ? 'on' : ''} onClick={() => setSec('board')}>Board</button>
              <button className={sec === 'keys' ? 'on' : ''} onClick={() => setSec('keys')}>Keyboard shortcuts</button>
              <button className={sec === 'devices' ? 'on' : ''} onClick={() => setSec('devices')}>Devices &amp; library</button>
            </nav>
          )}
          <div className="settings-main">
            {s.isMobile && (
              <div className="seg" role="group" aria-label="Settings sections" style={{ marginBottom: 10 }}>
                <button className={sec === 'board' ? 'on' : ''} aria-pressed={sec === 'board'} onClick={() => setSec('board')}>Board</button>
                <button className={sec === 'devices' ? 'on' : ''} aria-pressed={sec === 'devices'} onClick={() => setSec('devices')}>Devices &amp; library</button>
              </div>
            )}
            {sec === 'board' ? <BoardSettings phone={s.isMobile} /> : sec === 'devices' ? <DevicesSettings /> : <ShortcutsPanel rec={rec} setRec={setRec} note={note} />}
          </div>
        </div>
      </div>
    </>
  )
}

/** Install LPL as an app: the browser's prompt, or how to do it where there is no prompt (see install.ts). */
function InstallRow() {
  const { hint, install } = useInstall()
  if (hint === 'none') return null
  return (
    <div className="row small soft" style={{ gap: 10 }}>
      {hint === 'button' && <><span className="grow">Use LightPhotoLibrary like an app, in its own window and on the home screen.</span><button className="btn primary" onClick={install}>Install app</button></>}
      {hint === 'menu' && <span className="grow">To install it as an app, open the browser's menu and choose “Install” or “Add app to Home screen”.</span>}
      {hint === 'https' && <span className="grow muted">Installing it as an app needs LightPhotoLibrary to be opened over https (see “Install as an app” in the README).</span>}
    </div>
  )
}

function Choice<K extends keyof Settings>({ k, label, help, options }: { k: K; label: string; help?: string; options: [Settings[K], string][] }) {
  const st = useSettings()
  return (
    <div className="setrow">
      <div className="col" style={{ gap: 2, minWidth: 0 }}>
        <span className="small">{label}</span>
        {help && <span className="tiny muted">{help}</span>}
      </div>
      <div className="seg">
        {options.map(([v, l]) => <button key={String(v)} className={st[k] === v ? 'on' : ''} aria-pressed={st[k] === v} onClick={() => setSetting(k, v)}>{l}</button>)}
      </div>
    </div>
  )
}

function BoardSettings({ phone }: { phone: boolean }) {
  const st = useSettings()
  return (
    <div className="col" style={{ gap: 4 }}>
      <span className="caps">Moving around</span>
      {!phone && <Choice k="boardPan" label="Drag the board with" help="Holding Space and dragging always works too." options={[['both', 'Right or middle button'], ['right', 'Right button'], ['middle', 'Middle button']]} />}
      {!phone && <Choice k="boardWheel" label="Mouse wheel" help={st.boardWheel === 'scroll' ? 'Ctrl + wheel zooms.' : 'Shift + wheel scrolls sideways, Ctrl + wheel up and down.'} options={[['scroll', 'Scrolls'], ['zoom', 'Zooms']]} />}
      <Choice k="boardTouch" label="One finger on empty space (touch screens)" help="Two fingers always pinch to zoom." options={[['pan', 'Moves around'], ['select', 'Draws a selection box']]} />
      <span className="caps" style={{ marginTop: 10 }}>Photos and notes</span>
      <Choice k="boardPhotoDouble" label={phone ? 'Double-tap on a photo' : 'Double-click on a photo'} options={[['darktable', 'Edit in darktable'], ['fullscreen', 'Full screen'], ['large', 'Open large']]} />
      <Choice k="boardNumbers" label="Reading-order numbers on photos" options={[[true, 'Show'], [false, 'Hide']]} />
      <Choice k="boardSnap" label="Snap to a grid" help="Photos, notes and frames land on a 20 px grid when you let go." options={[[false, 'Off'], [true, 'On']]} />
      <div className="setrow">
        <span className="small">Colour of new notes</span>
        <div className="row" style={{ gap: 6 }}>
          {NOTE_COLORS.map(c => <button key={c} className={'swatch' + (st.boardNoteColor === c ? ' on' : '')} style={{ background: c }} aria-label={`Note colour ${c}`} aria-pressed={st.boardNoteColor === c} onClick={() => setSetting('boardNoteColor', c)} />)}
        </div>
      </div>
      <div className="setrow">
        <span className="small">Colour of new frames</span>
        <div className="row" style={{ gap: 6 }}>
          {FRAME_COLORS.map(([c, n]) => <button key={n} className={'swatch' + (c ? '' : ' none') + (st.boardFrameColor === c ? ' on' : '')} style={{ background: c || 'transparent' }} title={n} aria-label={`Frame colour ${n}`} aria-pressed={st.boardFrameColor === c} onClick={() => setSetting('boardFrameColor', c)} />)}
        </div>
      </div>
      <div className="setrow">
        <div className="col" style={{ gap: 2 }}><span className="small">Zoom a board opens at</span><span className="tiny muted">{phone ? 'On the phone.' : 'On computers and tablets; the phone has its own.'}</span></div>
        <div className="row" style={{ gap: 8 }}>
          <input type="range" min={20} max={200} step={10} value={phone ? st.boardZoomPhone : st.boardZoom} style={{ accentColor: '#E8A33D', width: 140 }} aria-label="Opening zoom"
            onChange={e => setSetting(phone ? 'boardZoomPhone' : 'boardZoom', +e.target.value)} />
          <span className="small" style={{ width: 40 }}>{phone ? st.boardZoomPhone : st.boardZoom}%</span>
        </div>
      </div>
      <div className="row" style={{ marginTop: 10 }}>
        <span className="tiny muted grow">Saved in the library, so every PC and the phone get the same.</span>
        <button className="btn ghost" onClick={() => resetSettings(Object.keys(DEFAULT_SETTINGS).filter(k => k.startsWith('board')) as (keyof Settings)[])}>Reset board settings</button>
      </div>
    </div>
  )
}

function ShortcutsPanel({ rec, setRec, note }: { rec: string | null; setRec: (r: string | null) => void; note: string }) {
  useShortcutsVersion()
  return (
    <div className="col" style={{ gap: 8 }}>
      <div className="row">
        <span className="small muted grow">Click “+” and press the key (with Ctrl, Alt or Shift if you like). Click a key to remove it. Saved in the library, so every PC gets them.</span>
        <button className="btn ghost" onClick={() => { if (window.confirm('Put every shortcut back to how it was?')) resetAll() }}>Reset all</button>
      </div>
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
    </div>
  )
}
