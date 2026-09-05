/**
 * Typed mirror of the CSS tokens in styles/theme.css.
 *
 * Only for the few places that need a colour in JS (canvas preview player, chart marks).
 * Components use Tailwind classes; they never import from here.
 */
export const tokens = {
  bg: 'var(--color-bg)',
  fg: 'var(--color-fg)',
  muted: 'var(--color-muted)',
  subtle: 'var(--color-subtle)',
  accentFrom: 'var(--color-accent-from)',
  accentTo: 'var(--color-accent-to)',
  onAccent: 'var(--color-on-accent)',
  hairline: 'var(--color-hairline)',
  surface: 'var(--color-surface)',
} as const

export const accentGradient = `linear-gradient(135deg, ${tokens.accentFrom}, ${tokens.accentTo})`
