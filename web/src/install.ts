// SPDX-License-Identifier: GPL-3.0-or-later
// "Install app" in Settings: the browser's own install prompt where there is one (Chrome and others send
// beforeinstallprompt), a line of guidance where there is none (Firefox), a note when the page is on plain
// http (browsers only install from https), nothing inside the LPL shell or once installed.
import { useEffect, useState } from 'react'
import { inShell } from './devices.ts'  // with .ts so Node can load this file in the tests

export type InstallHint = 'none' | 'button' | 'menu' | 'https'

export function installHint(o: { shell: boolean; standalone: boolean; installed: boolean; secure: boolean; canPrompt: boolean }): InstallHint {
  if (o.shell || o.standalone || o.installed) return 'none'
  if (o.canPrompt) return 'button'
  return o.secure ? 'menu' : 'https'
}

let prompt: any = null
let installed = false
const subs = new Set<() => void>()
const changed = () => subs.forEach(f => f())
if (typeof window !== 'undefined') {
  // listen from the start: the event can come before Settings is ever opened
  window.addEventListener('beforeinstallprompt', e => { e.preventDefault(); prompt = e; changed() })
  window.addEventListener('appinstalled', () => { installed = true; prompt = null; changed() })
}

const standalone = () => window.matchMedia('(display-mode: standalone)').matches || (navigator as any).standalone === true

export function useInstall(): { hint: InstallHint; install: () => void } {
  const [, tick] = useState(0)
  useEffect(() => { const f = () => tick(n => n + 1); subs.add(f); return () => { subs.delete(f) } }, [])
  const hint = installHint({ shell: inShell(), standalone: standalone(), installed, secure: window.isSecureContext, canPrompt: !!prompt })
  const install = () => {
    const p = prompt
    if (!p) return
    p.prompt()
    p.userChoice?.then((r: { outcome: string }) => { if (r.outcome === 'accepted') { prompt = null; changed() } }).catch(() => {})
  }
  return { hint, install }
}
