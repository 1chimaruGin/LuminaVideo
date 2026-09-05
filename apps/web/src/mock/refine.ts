/**
 * Design refinement applied over the export.
 *
 * The prototype was drawn on a 1300px artboard, and its type scale reflects that: most UI
 * text sits between 9px and 12.5px, icons are 16-19px hairlines, and secondary text runs at
 * #6f7aa0-#8b95b8 on a near-black ground. At artboard size that reads as "compact"; on a
 * real display it reads as unfinished — small, thin, and low contrast.
 *
 * Rather than hand-editing hundreds of generated inline styles, every style string passes
 * through here on its way into the DOM. That keeps the change systematic — one type ramp,
 * one colour ramp, applied everywhere — instead of a scatter of one-off overrides, and it
 * leaves the generated files untouched so `make mock` still regenerates cleanly.
 */
import type { Style } from '../lib/sx'

/* ------------------------------------------------------------------ type */

/**
 * Type ramp.
 *
 * A flat multiplier is wrong here: scaling 10px and 34px by the same factor keeps small
 * text small while making headings enormous. The small end needs the most help, so the ramp
 * adds a fixed amount there and tapers to a multiplier for display sizes.
 */
export function scaleFontSize(px: number): number {
  if (px <= 14) return round(px + 2.5) // 10 -> 12.5, 12.5 -> 15, 14 -> 16.5
  if (px <= 24) return round(px + 2) // 15 -> 17, 19 -> 21, 22 -> 24
  return round(px * 1.12) // display sizes are already large enough
}

const round = (n: number) => Math.round(n * 2) / 2

/* --------------------------------------------------------------- family */

/**
 * Script fallbacks appended to every font stack.
 *
 * The export's inline stacks are only `'IBM Plex Sans','Noto Sans Myanmar',sans-serif`, so
 * anything outside Latin and Myanmar falls through to a generic family that may not carry
 * the glyph. That is why the fullwidth plus (U+FF0B) in "New short" and "Add a scene"
 * rendered as tofu. Extending the chain fixes that whole class of bug at once instead of
 * per element, and it matters more once a real language pack is active.
 */
const FALLBACKS =
  "'Noto Sans JP','Noto Sans KR','Noto Sans SC','Noto Sans Thai',system-ui,sans-serif"

function extendFamily(value: string): string {
  return value.replace(/(^|,\s*)sans-serif\s*$/, (_, lead: string) => `${lead}${FALLBACKS}`)
}

/** Scales every px length in a value — covers `clamp(24px, 3.4vw, 34px)` in one pass. */
function scaleLengths(value: string): string {
  return value.replace(/(\d*\.?\d+)px/g, (_, n: string) => `${scaleFontSize(parseFloat(n))}px`)
}

/* ---------------------------------------------------------------- colour */

/**
 * Secondary-text ramp, lifted for legibility on the near-black ground.
 *
 * Only the muted greys move. The accent (#ffc36b) and the near-whites are already doing
 * their job, and shifting them would change the design rather than sharpen it.
 */
const COLOR: Record<string, string> = {
  '#6f7aa0': '#99a4c6',
  '#7d88ad': '#a5aecd',
  '#8b95b8': '#aeb7d4',
  '#98a2c4': '#b7c0da',
  '#9aa6cc': '#b9c2de',
  '#b9c3e4': '#cdd4ec',
  '#c3cbe9': '#d8dcf0',
  '#cfd8f7': '#e2e7fb',
}

const HAIRLINE = /rgba\(150,\s*170,\s*230,\s*\.1[0-9]?\)/g

function liftColor(value: string): string {
  const hit = COLOR[value.trim().toLowerCase()]
  return hit ?? value
}

/* ----------------------------------------------------------------- apply */

const TYPE_KEYS = new Set(['font', 'fontSize'])
const COLOR_KEYS = new Set(['color', 'stroke', 'fill'])
const EDGE_KEYS = new Set([
  'border',
  'borderTop',
  'borderBottom',
  'borderLeft',
  'borderRight',
  'borderColor',
])

/* -------------------------------------------------------------- surface */

/** Panel and card grounds in the export. */
const SURFACE = /rgba\(\s*20,\s*25,\s*48|rgba\(\s*5,\s*6,\s*15/

/**
 * Elevation for panels.
 *
 * A dark UI separates layers with either a visible edge or a shadow; this design has almost
 * neither, so every surface sits flush with the page and the whole thing reads flat. A soft
 * shadow is enough to make panels read as surfaces.
 *
 * Gated on radius AND padding because the same ground colour is used for small chips, which
 * should stay flush — a shadow on a 5px-padded pill looks like a mistake.
 */
function elevate(style: Style): boolean {
  const bg = style.background ?? style.backgroundColor ?? ''
  if (!SURFACE.test(bg) || style.boxShadow) return false
  return maxPx(style.borderRadius) >= 12 && maxPx(style.padding) >= 12
}

function maxPx(value: string | undefined): number {
  if (!value) return 0
  const found = [...value.matchAll(/(\d*\.?\d+)px/g)].map((m) => parseFloat(m[1]!))
  return found.length ? Math.max(...found) : 0
}

const ELEVATION = '0 1px 2px rgba(0,0,0,.28), 0 10px 28px -14px rgba(0,0,0,.6)'

export function refineStyle(style: Style): Style {
  const out: Style = {}
  for (const [key, value] of Object.entries(style)) {
    if (key === 'font') {
      out[key] = extendFamily(scaleLengths(value))
    } else if (key === 'fontFamily') {
      out[key] = extendFamily(value)
    } else if (TYPE_KEYS.has(key)) {
      out[key] = scaleLengths(value)
    } else if (COLOR_KEYS.has(key)) {
      out[key] = liftColor(value)
    } else if (EDGE_KEYS.has(key)) {
      // Card and panel edges are close to invisible at .13-.14 alpha; a small lift is what
      // separates "flat" from "layered" without introducing a new visual language.
      out[key] = value.replace(HAIRLINE, 'rgba(156,176,236,.2)')
    } else {
      out[key] = value
    }
  }
  if (elevate(out)) out.boxShadow = ELEVATION
  return out
}
