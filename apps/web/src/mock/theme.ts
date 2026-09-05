/**
 * The universe theme.
 *
 * The export gestures at space — three faint radial gradients and ten hand-placed dots —
 * but at real size that reads as "dark grey page", not as a sky. This builds the backdrop
 * properly: a layered starfield with depth, a richer nebula field, a horizon glow so every
 * screen opens on something, and film grain so the large flat gradients do not band.
 *
 * The starfield is generated from a fixed seed rather than random, so the page renders
 * identically on every load. That keeps screenshot comparison meaningful — a random sky
 * would make every diff noise.
 */

/** Small deterministic PRNG (mulberry32). Same seed, same sky, every time. */
function rng(seed: number): () => number {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** Star tints: mostly white, with cool and warm giants for variety. */
const TINTS = ['255,255,255', '255,255,255', '206,224,255', '255,217,168', '186,206,255']

type Layer = { seed: number; count: number; min: number; max: number; alpha: [number, number] }

/**
 * Three depth layers. Distant stars are small, dim and numerous; near stars are few, larger
 * and brighter. That size/brightness correlation is what makes a flat plane read as depth.
 */
const LAYERS: Layer[] = [
  { seed: 20240817, count: 110, min: 0.8, max: 1.3, alpha: [0.22, 0.5] },
  { seed: 991, count: 46, min: 1.3, max: 1.9, alpha: [0.45, 0.75] },
  { seed: 5150, count: 14, min: 2.0, max: 2.8, alpha: [0.7, 0.95] },
]

function starLayer({ seed, count, min, max, alpha }: Layer): string[] {
  const rand = rng(seed)
  const out: string[] = []
  for (let i = 0; i < count; i++) {
    const x = (rand() * 100).toFixed(2)
    const y = (rand() * 100).toFixed(2)
    const r = (min + rand() * (max - min)).toFixed(2)
    const a = (alpha[0] + rand() * (alpha[1] - alpha[0])).toFixed(2)
    const tint = TINTS[Math.floor(rand() * TINTS.length)]!
    out.push(`radial-gradient(${r}px ${r}px at ${x}% ${y}%, rgba(${tint},${a}), transparent)`)
  }
  return out
}

/** Replaces the export's `starsStyle`. Inset slightly so the drift never exposes an edge. */
export const STARS_STYLE = [
  'position:absolute',
  'inset:-4%',
  'pointer-events:none',
  'opacity:.85',
  'background-repeat:no-repeat',
  `background-image:${LAYERS.flatMap(starLayer).join(',')}`,
  'animation:lum-drift 180s linear infinite alternate, lum-twinkle 9s ease-in-out infinite',
].join(';')

/**
 * Replaces the export's `deskScreenStyle`.
 *
 * Deeper and warmer than the original: a sunlit limb top-right, cold blue at the left, a
 * violet cloud rising from below, and a vignette to stop the corners floating.
 */
export const DESK_SCREEN_STYLE = [
  'flex:1',
  'min-width:0',
  'overflow-y:auto',
  'position:relative',
  'isolation:isolate',
  [
    'background:',
    'radial-gradient(120% 80% at 84% -12%, rgba(255,172,80,.20), transparent 58%),',
    'radial-gradient(95% 70% at 2% 4%, rgba(74,124,255,.24), transparent 62%),',
    'radial-gradient(120% 85% at 46% 118%, rgba(146,70,255,.22), transparent 62%),',
    'radial-gradient(140% 120% at 50% 50%, transparent 42%, rgba(2,3,10,.55) 100%),',
    'linear-gradient(180deg,#080b1c 0%,#04050e 100%)',
  ].join(''),
].join(';')

/** Film grain, as a tiny inline SVG so it costs no request. */
const GRAIN =
  "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='140'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='3'/%3E%3C/filter%3E%3Crect width='140' height='140' filter='url(%23n)' opacity='.55'/%3E%3C/svg%3E\")"

export const THEME_CSS = `
@keyframes lum-drift {
  from { transform: translate3d(0, 0, 0) }
  to   { transform: translate3d(-1.6%, 1.1%, 0) }
}
@keyframes lum-twinkle {
  0%, 100% { opacity: .72 }
  50%      { opacity: .95 }
}

/*
 * The horizon.
 *
 * Every screen used to open with bare text against a flat edge. This puts a light source
 * above the fold — a warm limb on one side, cold sky on the other — so the top of the page
 * is part of the composition instead of where the page happens to start.
 */
#lum-slot-desktop::before {
  content: '';
  position: absolute;
  inset: 0 0 auto 0;
  height: min(48vh, 460px);
  pointer-events: none;
  z-index: 0;
  background:
    radial-gradient(120% 100% at 16% 0%, rgba(255,178,92,.15), transparent 60%),
    radial-gradient(95% 100% at 78% 0%, rgba(96,140,255,.18), transparent 64%),
    linear-gradient(180deg, rgba(126,156,255,.09), transparent 80%);
}
#lum-slot-desktop > div { position: relative; z-index: 1; }

/* Grain over everything, so the wide gradients do not band on a real display. */
[data-lum='window'] { position: relative; }
[data-lum='window']::after {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 3;
  opacity: .04;
  mix-blend-mode: overlay;
  background-image: ${GRAIN};
}

/*
 * Page header.
 *
 * A faint light-trail under the title separates the header from the content without a hard
 * rule. Positioned absolutely so it works whether the header block is a column (most
 * screens) or a row (the storyboard, which pairs the title with Play all).
 */
[data-lum='col'] > div:first-child { position: relative; }

/*
 * There is deliberately no second glow behind the title. An earlier attempt put a radial
 * bloom on the header block, and because that element is short and wide the gradient ran
 * out of room before it faded — it painted as a visible grey rectangle. The horizon above
 * is the page's single light source; stacking another one on top only muddied it.
 */

/* The horizon line: a lit edge with bloom, not a divider rule. */
[data-lum='col'] > div:first-child::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: -18px;
  height: 1px;
  pointer-events: none;
  background: linear-gradient(
    90deg,
    rgba(255,201,120,.72),
    rgba(150,175,245,.3) 34%,
    transparent 78%
  );
  box-shadow: 0 0 16px rgba(255,190,110,.3);
}

/*
 * The title carries the page, so it gets real display scale — the export sizes it for a
 * 1300px artboard, where it lands around 34px and reads as a large label rather than a
 * heading.
 */
[data-lum='col'] h1 {
  font-size: clamp(30px, 2.7vw, 44px) !important;
  line-height: 1.06 !important;
  letter-spacing: -.025em !important;
  text-shadow: 0 0 48px rgba(255,196,120,.26);
}
[data-lum='col'] h2 { text-shadow: 0 0 44px rgba(255,196,120,.22); }

/* The rail is a darker instrument panel against the sky, with a lit inner edge. */
[data-lum='sidebar'] {
  position: relative;
  background: linear-gradient(180deg, rgba(8,11,24,.82), rgba(6,8,18,.9)) !important;
  backdrop-filter: blur(2px);
}
[data-lum='sidebar']::after {
  content: '';
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  width: 1px;
  background: linear-gradient(
    180deg,
    transparent,
    rgba(150,180,255,.28) 18%,
    rgba(255,195,107,.16) 62%,
    transparent
  );
}
`
