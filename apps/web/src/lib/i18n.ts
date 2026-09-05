/**
 * i18n runtime.
 *
 * Mirrors the backend LanguagePack: the server owns segmentation, alignment and caption
 * timing; the client owns direction, typography and message catalogs. Both are keyed by the
 * same BCP-47 code, and neither branches on language outside this file.
 */
import { i18n } from '@lingui/core'

export type Direction = 'ltr' | 'rtl'

export interface PackTypography {
  /** CSS font stack for this script. */
  fontStack: string
  /** Web font URLs to load lazily on activation. Never bundled — some faces are 5–20MB. */
  webfonts: string[]
  lineHeight: number
  letterSpacingEm: number
  /** Scripts with no uppercase must never be rendered through text-transform. */
  hasCase: boolean
}

export interface Pack {
  code: string
  direction: Direction
  typography: PackTypography
}

const PACKS: Record<string, Pack> = {
  en: {
    code: 'en',
    direction: 'ltr',
    typography: {
      fontStack: "'IBM Plex Sans', system-ui, sans-serif",
      webfonts: [],
      lineHeight: 1.4,
      letterSpacingEm: 0,
      hasCase: true,
    },
  },
  // my: { ... }  — pack #2. Myanmar needs generous line-height and zero tracking.
}

export const DEFAULT_LANGUAGE = 'en'

export function getPack(code: string): Pack {
  return PACKS[code] ?? PACKS[DEFAULT_LANGUAGE]!
}

/** Applies a pack to the document: direction, font stack, and script-specific metrics. */
export async function activate(code: string): Promise<void> {
  const pack = getPack(code)
  const { messages } = await loadCatalog(pack.code)
  i18n.loadAndActivate({ locale: pack.code, messages })

  const root = document.documentElement
  root.lang = pack.code
  root.dir = pack.direction
  root.style.setProperty('--font-sans', pack.typography.fontStack)
  root.style.setProperty('--pack-line-height', String(pack.typography.lineHeight))
  root.style.setProperty('--pack-letter-spacing', `${pack.typography.letterSpacingEm}em`)

  await Promise.all(pack.typography.webfonts.map(loadFont))
}

/** Catalogs are per-pack chunks, never bundled together. */
async function loadCatalog(code: string): Promise<{ messages: Record<string, string> }> {
  switch (code) {
    case 'en':
      return import('../i18n/locales/en')
    default:
      return import('../i18n/locales/en')
  }
}

async function loadFont(url: string): Promise<void> {
  const face = new FontFace('LuminaPack', `url(${url})`)
  await face.load()
  document.fonts.add(face)
}

export { i18n }
