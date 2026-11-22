// SPDX-License-Identifier: GPL-3.0-or-later
// Small pieces the Devices page, the top bar's indicator and Settings > Devices & library share.

export const Chevron = ({ open }: { open: boolean }) => (
  <svg className={'chev' + (open ? ' open' : '')} width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M9 5l7 7-7 7" /></svg>
)
export const StopIcon = () => <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="1.5" fill="currentColor" /></svg>
export const PlayIcon = () => <svg width="12" height="12" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13l11-6.5z" fill="currentColor" /></svg>
export const CrossIcon = () => <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>

/** The overall bar of a batch. still: nothing moves (stopped, or the device is not reporting). */
export function Bar({ fraction, label, still, thin }: { fraction: number; label: string; still?: boolean; thin?: boolean }) {
  const pct = Math.round(Math.min(1, Math.max(0, fraction)) * 100)
  return (
    <span className={'xbar' + (still ? ' still' : '') + (thin ? ' thin' : '')} role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
      <i style={{ width: `${pct}%` }} />
    </span>
  )
}
