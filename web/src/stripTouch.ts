// SPDX-License-Identifier: GPL-3.0-or-later
// Touch rules of the Strip, kept apart from the component so they can be tested without a browser.

/** A finger on a lane's handle (touch-action: none) starts reordering after this long without moving. */
export const GRIP_HOLD_MS = 220
/** Pixels a finger or a mouse must move on a handle before the lane lifts. */
export const GRIP_SLOP_TOUCH = 4
export const GRIP_SLOP_MOUSE = 5
/** A blur this soon after the name editor opened is the menu that opened it handing focus back, not the user leaving it. */
export const EDIT_GRACE_MS = 400

/** Does a press on a lane handle turn into a drag? Moved far enough, or held long enough without moving. */
export function gripDragStarts(dx: number, dy: number, heldMs: number, touch: boolean): boolean {
  const moved = Math.hypot(dx, dy)
  if (moved >= (touch ? GRIP_SLOP_TOUCH : GRIP_SLOP_MOUSE)) return true
  return touch && heldMs >= GRIP_HOLD_MS
}

/** Should a blur of the name editor be ignored (focus taken back) because the editor has only just opened? */
export const blurIsFocusHandBack = (openedAt: number, now: number) => now - openedAt < EDIT_GRACE_MS

/** On a phone a tap on a lane's or folder's name opens the name editor (a desktop has the double-click and the pencil). */
export const isNameTapRename = (phone: boolean, canRename: boolean) => phone && canRename
