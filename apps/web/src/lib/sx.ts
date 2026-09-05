/**
 * CSS-string style helper.
 *
 * The design prototype (Lumina-Web.html) expresses every style as a CSS declaration string.
 * Rather than hand-transcribing those into React style objects — which is where a port
 * silently drifts from its design — `sx` parses them at runtime. The strings in the screen
 * components are byte-identical to the prototype's, so "does it match the design" is a
 * question about this one function rather than about a thousand transcriptions.
 */
import { useEffect } from 'react'

export type Style = Record<string, string>

const cache = new Map<string, Style>()

/**
 * Optional transform applied to every parsed style.
 *
 * The design mockup uses this to run the export's styles through a refined type and colour
 * ramp without editing generated files. It is deliberately a single global hook rather than
 * a parameter on `sx`: the call sites are generated code, so they cannot pass one.
 */
let transform: ((style: Style) => Style) | null = null

export function setStyleTransform(fn: ((style: Style) => Style) | null): void {
  if (fn === transform) return
  transform = fn
  cache.clear()
}

/** Parse `"display:flex;gap:8px"` into a React style object. */
export function sx(css: string | undefined | null): Style {
  if (!css) return {}
  const hit = cache.get(css)
  if (hit) return hit

  const out: Style = {}
  for (const decl of splitDeclarations(css)) {
    const colon = decl.indexOf(':')
    if (colon < 0) continue
    const prop = decl.slice(0, colon).trim()
    const value = decl.slice(colon + 1).trim()
    if (!prop || !value) continue
    out[toCamel(prop)] = value
  }
  const final = transform ? transform(out) : out
  cache.set(css, final)
  return final
}

/** Merge several CSS strings/objects, later winning. */
export function mx(...parts: (string | Style | undefined | null | false)[]): Style {
  const out: Style = {}
  for (const p of parts) {
    if (!p) continue
    Object.assign(out, typeof p === 'string' ? sx(p) : p)
  }
  return out
}

/**
 * Split on `;` but not inside parentheses — `gradient(a, b); color:red` must not split
 * inside the gradient, and neither must `url(data:...;base64,...)`.
 */
function splitDeclarations(css: string): string[] {
  const parts: string[] = []
  let depth = 0
  let start = 0
  for (let i = 0; i < css.length; i++) {
    const ch = css[i]
    if (ch === '(') depth++
    else if (ch === ')') depth--
    else if (ch === ';' && depth === 0) {
      parts.push(css.slice(start, i))
      start = i + 1
    }
  }
  parts.push(css.slice(start))
  return parts
}

function toCamel(prop: string): string {
  // Custom properties keep their exact name; React passes them through untouched.
  if (prop.startsWith('--')) return prop
  return prop.replace(/-([a-z])/g, (_, c: string) => c.toUpperCase())
}

/* ------------------------------------------------------------------ hover */

const sheetRules = new Map<string, string>()
let sheet: HTMLStyleElement | null = null

/**
 * The prototype expresses hover with a `style-hover="..."` attribute. Inline styles cannot
 * express `:hover`, so each distinct hover declaration becomes a generated class backed by
 * a real CSS rule — which keeps the interaction identical instead of approximating it with
 * mouse-enter state.
 */
export function hv(css: string | undefined | null): string | undefined {
  if (!css) return undefined
  const name = `hv-${hash(css)}`
  if (!sheetRules.has(name)) {
    sheetRules.set(name, `.${name}:hover{${css}}`)
    flush()
  }
  return name
}

function flush(): void {
  if (typeof document === 'undefined') return
  if (!sheet) {
    sheet = document.createElement('style')
    sheet.dataset.luminaHover = ''
    document.head.appendChild(sheet)
  }
  sheet.textContent = [...sheetRules.values()].join('\n')
}

function hash(s: string): string {
  let h = 0x811c9dc5
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 0x01000193)
  }
  return (h >>> 0).toString(36)
}

/** Injects the prototype's keyframes and page-level rules once. */
export function useProtoStylesheet(css: string): void {
  useEffect(() => {
    const el = document.createElement('style')
    el.textContent = css
    document.head.appendChild(el)
    return () => {
      el.remove()
    }
  }, [css])
}
