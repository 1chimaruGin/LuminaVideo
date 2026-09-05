/**
 * The universe, on a canvas.
 *
 * The logo is a black hole — a dark event horizon inside a gold accretion spiral, with a
 * small planet and a blue outer halo. That mark is the whole visual language, so the
 * backdrop extends it rather than sitting behind it: the same amber core, the same cold blue
 * rim, the same violet dust.
 *
 * Canvas rather than CSS keyframes because these need to actually move independently —
 * stars twinkling on their own phases, three parallax depths responding to the pointer,
 * nebula drifting, planets on their own orbits, an accretion disc rotating. CSS can fake one
 * of those; it cannot do all of them together at 60fps.
 *
 * Costs are kept honest: nebula blobs are rendered once to offscreen canvases and then just
 * blitted, the loop stops when the tab is hidden, and `prefers-reduced-motion` renders a
 * single static frame and never starts the loop at all.
 */
import { useEffect, useRef } from 'react'

type Star = {
  x: number
  y: number
  r: number
  a: number
  depth: number
  phase: number
  speed: number
  tint: string
}

type Planet = {
  x: number
  y: number
  r: number
  hue: string
  hue2: string
  drift: number
  seed: number
  bands: boolean
  ring?: boolean
  baked?: { img: HTMLCanvasElement; R: number }
}

type Shooter = { x: number; y: number; vx: number; vy: number; life: number }

const TINTS = ['255,255,255', '255,255,255', '202,222,255', '255,214,164', '186,206,255']

function rand(seed: number) {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/**
 * A spiral galaxy, built once as a particle system and cached.
 *
 * Stars are scattered along logarithmic spiral arms — r = a·e^(b·theta) — with the scatter
 * growing outward, which is what makes arms read as arms rather than as drawn curves. The
 * core is warm and dense, the arms cool and sparse, matching the mark: gold at the centre,
 * blue at the rim.
 *
 * Rendered to an offscreen canvas so the several thousand particles are paid for once, at
 * startup, instead of every frame.
 */
function galaxy(size: number, seed: number, warm: string, cool: string): HTMLCanvasElement {
  const c = document.createElement('canvas')
  c.width = c.height = size
  const g = c.getContext('2d')!
  const rnd = rand(seed)
  const mid = size / 2
  const R = size * 0.46
  const FLAT = 0.56 // how far the disc is inclined away from face-on
  const ARMS = 2
  const B = 0.36 // pitch of the logarithmic spiral

  /** Position along arm `k` at angle `t`, in disc coordinates. */
  const arm = (k: number, t: number) => {
    const theta = t + (k * Math.PI * 2) / ARMS
    const r = R * 0.12 * Math.exp(B * t)
    return { x: mid + Math.cos(theta) * r, y: mid + Math.sin(theta) * r * FLAT, r }
  }

  /** Stamps a soft blob — the unit that smooth arms and the disc are painted with. */
  const stamp = (x: number, y: number, rad: number, rgb: string, a: number) => {
    const gr = g.createRadialGradient(x, y, 0, x, y, rad)
    gr.addColorStop(0, `rgba(${rgb},${a})`)
    gr.addColorStop(0.55, `rgba(${rgb},${a * 0.35})`)
    gr.addColorStop(1, `rgba(${rgb},0)`)
    g.fillStyle = gr
    g.beginPath()
    g.arc(x, y, rad, 0, Math.PI * 2)
    g.fill()
  }

  g.globalCompositeOperation = 'lighter'

  /*
   * 1. The disc.
   *
   * A galaxy is a luminous sheet, not a swarm of dots. Without this underneath, the arms
   * read as scattered dust — which is exactly what the first versions looked like.
   */
  g.save()
  g.translate(mid, mid)
  g.scale(1, FLAT)
  const disc = g.createRadialGradient(0, 0, 0, 0, 0, R)
  disc.addColorStop(0, `rgba(${warm},0.2)`)
  disc.addColorStop(0.2, `rgba(${warm},0.08)`)
  disc.addColorStop(0.46, `rgba(${cool},0.05)`)
  disc.addColorStop(0.75, `rgba(${cool},0.028)`)
  disc.addColorStop(1, `rgba(${cool},0)`)
  g.fillStyle = disc
  g.beginPath()
  g.arc(0, 0, R, 0, Math.PI * 2)
  g.fill()
  g.restore()

  /*
   * 2. Arms as continuous density waves, stamped along the spiral. Width and colour both
   *    track radius: tight and warm near the core, broad and blue at the rim, where the
   *    young hot stars are.
   */
  for (let k = 0; k < ARMS; k++) {
    for (let t = 0; t < 3.9; t += 0.02) {
      const { x, y, r } = arm(k, t)
      if (r > R) break
      const f = r / R
      // ~200 stamps overlap along each arm. Anything above ~0.01 per stamp sums past white.
      stamp(x, y, R * (0.035 + f * 0.085), f > 0.45 ? cool : warm, 0.006 + (1 - f) * 0.005)
    }
  }

  /*
   * 3. Dust lanes.
   *
   * Erased along a spiral sitting just inside each bright arm. This is the detail that most
   * makes a spiral read as a spiral — the dark gap is as important as the bright arm, and
   * additive painting alone can never produce it.
   */
  g.globalCompositeOperation = 'destination-out'
  for (let k = 0; k < ARMS; k++) {
    for (let t = 0; t < 3.9; t += 0.012) {
      const { x, y, r } = arm(k, t - 0.34)
      if (r > R) break
      const f = r / R
      stamp(x, y, R * (0.022 + f * 0.05), '0,0,0', 0.02 * (1 - f * 0.35))
    }
  }

  /* 4. Field and arm stars, for texture on top of the smooth light. */
  g.globalCompositeOperation = 'lighter'
  for (let i = 0; i < 9000; i++) {
    const k = i % ARMS
    const t = Math.pow(rnd(), 0.7) * 3.9
    const { r } = arm(k, t)
    if (r > R) continue
    const f = r / R
    const spread = f * 0.7 + 0.1
    const theta = t + (k * Math.PI * 2) / ARMS + (rnd() - 0.5) * spread
    const rr = r * (1 + (rnd() - 0.5) * spread * 0.7)
    const x = mid + Math.cos(theta) * rr
    const y = mid + Math.sin(theta) * rr * FLAT
    g.fillStyle = `rgba(${rnd() < 1 - f ? warm : cool},${(0.1 + rnd() * 0.35) * (1 - f * 0.4)})`
    g.fillRect(x, y, 0.9, 0.9)
  }

  /* 5. Star-forming regions: the bright knots strung along the arms. */
  for (let i = 0; i < 130; i++) {
    const k = i % ARMS
    const t = 0.7 + rnd() * 3.1
    const { r } = arm(k, t)
    if (r > R) continue
    const theta = t + (k * Math.PI * 2) / ARMS + (rnd() - 0.5) * 0.28
    const rr = r * (1 + (rnd() - 0.5) * 0.24)
    stamp(
      mid + Math.cos(theta) * rr,
      mid + Math.sin(theta) * rr * FLAT,
      R * (0.012 + rnd() * 0.02),
      // Blue dominates: these are clusters of hot, newborn stars. The pink minority is
      // Halpha emission from the gas around them.
      rnd() < 0.3 ? '255,176,198' : '176,214,255',
      0.16 + rnd() * 0.16,
    )
  }

  /* 6. The nucleus, last, so nothing dims it. */
  g.save()
  g.translate(mid, mid)
  g.scale(1, FLAT + 0.2)
  const core = g.createRadialGradient(0, 0, 0, 0, 0, R * 0.34)
  core.addColorStop(0, 'rgba(255,248,232,0.8)')
  core.addColorStop(0.12, `rgba(${warm},0.4)`)
  core.addColorStop(0.4, `rgba(${warm},0.12)`)
  core.addColorStop(1, `rgba(${warm},0)`)
  g.fillStyle = core
  g.beginPath()
  g.arc(0, 0, R * 0.34, 0, Math.PI * 2)
  g.fill()
  g.restore()

  return c
}

/**
 * A planet, baked at full resolution.
 *
 * A single radial gradient reads as a coloured circle, not a sphere. What sells it is the
 * combination of an off-centre light, limb darkening toward the edge, latitude banding, and
 * a thin rim light on the lit side where the atmosphere catches the sun. The light comes
 * from upper-right, matching the black hole.
 */
function planet(px: number, seed: number, warm: string, cool: string, bands: boolean) {
  const c = document.createElement('canvas')
  c.width = c.height = px
  const g = c.getContext('2d')!
  const rnd = rand(seed)
  const R = px * 0.42
  const mid = px / 2
  const lx = mid + R * 0.42
  const ly = mid - R * 0.4

  g.save()
  g.beginPath()
  g.arc(mid, mid, R, 0, Math.PI * 2)
  g.clip()

  // Body, lit from upper-right and falling to near-black at the terminator.
  const body = g.createRadialGradient(lx, ly, R * 0.06, mid, mid, R * 1.22)
  body.addColorStop(0, warm)
  body.addColorStop(0.42, cool)
  body.addColorStop(0.86, '#0a0d18')
  body.addColorStop(1, '#05070f')
  g.fillStyle = body
  g.fillRect(0, 0, px, px)

  // Latitude bands for the gas giant. Ellipses, so they curve with the sphere.
  if (bands) {
    g.globalAlpha = 0.16
    for (let i = 0; i < 14; i++) {
      const y = mid - R + (i / 14) * R * 2
      const halfW = Math.sqrt(Math.max(0, R * R - (y - mid) * (y - mid)))
      g.fillStyle = i % 2 ? warm : cool
      g.beginPath()
      g.ellipse(mid, y, halfW, R * (0.03 + rnd() * 0.04), 0, 0, Math.PI * 2)
      g.fill()
    }
    g.globalAlpha = 1
  }

  // Speckle, so the surface is not a perfectly smooth gradient.
  g.globalAlpha = 0.06
  for (let i = 0; i < 340; i++) {
    const a = rnd() * Math.PI * 2
    const r = Math.sqrt(rnd()) * R
    g.fillStyle = rnd() < 0.5 ? '#fff' : '#000'
    g.beginPath()
    g.arc(mid + Math.cos(a) * r, mid + Math.sin(a) * r, rnd() * R * 0.035, 0, Math.PI * 2)
    g.fill()
  }
  g.globalAlpha = 1
  g.restore()

  // Rim light: the atmosphere catching the sun along the lit limb.
  g.save()
  g.beginPath()
  g.arc(mid, mid, R, 0, Math.PI * 2)
  g.clip()
  const rim = g.createRadialGradient(lx * 0.98, ly * 0.98, R * 0.72, mid, mid, R)
  rim.addColorStop(0, 'rgba(255,255,255,0)')
  rim.addColorStop(0.9, 'rgba(255,236,208,0.1)')
  rim.addColorStop(1, 'rgba(255,246,230,0.34)')
  g.fillStyle = rim
  g.fillRect(0, 0, px, px)
  g.restore()

  return { img: c, R }
}

/** A soft cloud, drawn once and reused every frame. */
function blob(size: number, rgb: string): HTMLCanvasElement {
  const c = document.createElement('canvas')
  c.width = c.height = size
  const g = c.getContext('2d')!
  const grad = g.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2)
  // Deliberately faint. These are drawn additively and they overlap, so anything stronger
  // sums into a flat colour wash that eats the stars and the text contrast with them.
  grad.addColorStop(0, `rgba(${rgb},0.15)`)
  grad.addColorStop(0.4, `rgba(${rgb},0.05)`)
  grad.addColorStop(1, `rgba(${rgb},0)`)
  g.fillStyle = grad
  g.fillRect(0, 0, size, size)
  return c
}

export function Cosmos() {
  const ref = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    const ctx = canvas.getContext('2d', { alpha: false })
    if (!ctx) return

    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const rnd = rand(20260829)

    let w = 0
    let h = 0
    let dpr = 1
    let stars: Star[] = []
    let haloGrad: CanvasGradient | null = null
    let vigGrad: CanvasGradient | null = null
    let planets: Planet[] = []
    let shooters: Shooter[] = []
    const clouds = [
      { img: blob(512, '70,116,255'), x: 0.04, y: 0.1, s: 1.05, vx: 0.0000042, vy: 0.0000021 },
      { img: blob(512, '255,146,58'), x: 0.94, y: -0.06, s: 0.92, vx: -0.0000031, vy: 0.0000016 },
      { img: blob(512, '146,70,255'), x: 0.42, y: 1.06, s: 1.2, vx: 0.0000024, vy: -0.0000013 },
      { img: blob(512, '86,186,255'), x: 0.8, y: 0.78, s: 0.78, vx: -0.0000018, vy: -0.0000024 },
    ]

    /*
     * Galaxies. Two, at different scales and inclinations, turning very slowly.
     *
     * Baked at their on-screen size times the device pixel ratio, and never scaled up when
     * drawn. Baking at a fixed 720px and stretching to ~1900 was magnifying every particle
     * 2.6x, which is what made them look low-resolution and blocky.
     */
    type Gx = { img: HTMLCanvasElement; x: number; y: number; s: number; spin: number; tilt: number; a: number }
    let galaxies: Gx[] = []
    const GX_SPEC = [
      { seed: 77_113, warm: '255,196,120', cool: '150,190,255', x: 0.13, y: 0.33, s: 0.66, spin: 0.0000058, tilt: -0.5, a: 1 },
      { seed: 4_242, warm: '255,172,122', cool: '170,200,255', x: 0.76, y: 0.9, s: 0.44, spin: -0.0000037, tilt: 0.9, a: 0.85 },
    ]

    // Where the black hole sits: off the right edge, so it is ambience, not an obstacle.
    const hole = { x: 1.02, y: 0.2, r: 0.34 }

    /*
     * The accretion disc, baked once.
     *
     * Its five arcs each need a linear gradient, and rebuilding those every frame was the
     * single most expensive thing in the loop. The shape never changes — only its rotation
     * does — so it is rendered to an offscreen canvas at startup and then simply spun.
     */
    let discImg: HTMLCanvasElement | null = null
    let discR = 0
    function bakeDisc(R: number) {
      const s2 = Math.ceil(R * 2.4)
      const cv = document.createElement('canvas')
      cv.width = cv.height = s2
      const d = cv.getContext('2d')!
      d.translate(s2 / 2, s2 / 2)
      for (let i = 0; i < 5; i++) {
        const rr = R * (0.52 + i * 0.1)
        const gr = d.createLinearGradient(-rr, 0, rr, 0)
        gr.addColorStop(0, 'rgba(255,150,60,0)')
        gr.addColorStop(0.35, `rgba(255,190,110,${0.3 - i * 0.045})`)
        gr.addColorStop(0.6, `rgba(255,236,200,${0.34 - i * 0.05})`)
        gr.addColorStop(1, 'rgba(120,170,255,0)')
        d.save()
        d.rotate(i * 0.35)
        d.strokeStyle = gr
        d.lineWidth = R * 0.075
        d.beginPath()
        d.ellipse(0, 0, rr, rr * 0.94, 0, 0.3, Math.PI * 1.75)
        d.stroke()
        d.restore()
      }
      discImg = cv
      discR = R
    }

    function build() {
      const count = Math.round(Math.min(700, (w * h) / 2600))
      stars = Array.from({ length: count }, () => {
        const depth = rnd() < 0.62 ? 0 : rnd() < 0.7 ? 1 : 2
        return {
          x: rnd(),
          y: rnd(),
          r: [0.7, 1.15, 1.8][depth]! + rnd() * 0.6,
          a: [0.32, 0.6, 0.9][depth]! * (0.5 + rnd() * 0.5),
          depth,
          phase: rnd() * Math.PI * 2,
          speed: 0.4 + rnd() * 1.4,
          tint: TINTS[Math.floor(rnd() * TINTS.length)]!,
        }
      })
      planets = [
        { x: 0.17, y: 0.76, r: 52, hue: '#ffd9a2', hue2: '#8a4a17', drift: 0.6, ring: true, seed: 31, bands: true },
        { x: 0.71, y: 0.13, r: 26, hue: '#9ed2ff', hue2: '#14508f', drift: -0.35, seed: 77, bands: false },
      ]
      for (const pl of planets) {
        // Bake at 2x the drawn diameter so the sphere stays crisp on a retina display.
        pl.baked = planet(Math.ceil(pl.r * 2.6 * dpr * 2), pl.seed, pl.hue, pl.hue2, pl.bands)
      }

      galaxies = GX_SPEC.map((g) => {
        const drawn = Math.max(w, h) * g.s
        const bake = Math.min(2400, Math.ceil(drawn * dpr))
        return { ...g, img: galaxy(bake, g.seed, g.warm, g.cool) }
      })
    }

    function resize() {
      dpr = Math.min(2, window.devicePixelRatio || 1)
      w = canvas!.clientWidth
      h = canvas!.clientHeight
      canvas!.width = Math.round(w * dpr)
      canvas!.height = Math.round(h * dpr)
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0)
      haloGrad = null
      vigGrad = null
      discImg = null
      build()
    }

    // Parallax follows the pointer a little; depth is what sells it.
    let px = 0
    let py = 0
    let tx = 0
    let ty = 0
    const onMove = (e: PointerEvent) => {
      tx = (e.clientX / window.innerWidth - 0.5) * 2
      ty = (e.clientY / window.innerHeight - 0.5) * 2
    }

    function drawHole(t: number) {
      const cx = hole.x * w
      const cy = hole.y * h
      const R = hole.r * Math.min(w, h)

      // Lensed halo. Cached — a radial gradient per frame is pure waste.
      if (!haloGrad) {
        haloGrad = ctx!.createRadialGradient(cx, cy, R * 0.42, cx, cy, R * 1.5)
        haloGrad.addColorStop(0, 'rgba(120,170,255,0.16)')
        haloGrad.addColorStop(0.5, 'rgba(90,120,255,0.06)')
        haloGrad.addColorStop(1, 'rgba(0,0,0,0)')
      }
      ctx!.fillStyle = haloGrad
      ctx!.beginPath()
      ctx!.arc(cx, cy, R * 1.5, 0, Math.PI * 2)
      ctx!.fill()

      // Accretion disc: the baked image, spun.
      if (!discImg || discR !== R) bakeDisc(R)
      if (discImg) {
        ctx!.save()
        ctx!.translate(cx, cy)
        ctx!.rotate(t * 0.00009)
        ctx!.drawImage(discImg, -discImg.width / 2, -discImg.height / 2)
        ctx!.restore()
      }

      // Event horizon.
      const core = ctx!.createRadialGradient(cx, cy, 0, cx, cy, R * 0.5)
      core.addColorStop(0, '#000')
      core.addColorStop(0.82, '#000')
      core.addColorStop(1, 'rgba(0,0,0,0)')
      ctx!.fillStyle = core
      ctx!.beginPath()
      ctx!.arc(cx, cy, R * 0.5, 0, Math.PI * 2)
      ctx!.fill()
    }


    /*
     * Planet layer.
     *
     * The planet and its rings are drawn together into an offscreen canvas at full opacity,
     * then blitted once at reduced alpha. Drawing them straight onto the scene with a global
     * alpha made the globe semi-transparent, so the far side of the ring showed *through* the
     * planet — a ring cannot be visible where the planet is in front of it.
     */
    let layer: HTMLCanvasElement | null = null
    let layerCtx: CanvasRenderingContext2D | null = null

    function drawPlanet(p: Planet, t: number) {
      const x = p.x * w + Math.sin(t * 0.00002 * p.drift) * 26
      const y = p.y * h + Math.cos(t * 0.000015 * p.drift) * 16
      const d = p.r * 2.6
      const tilt = -0.34
      const pad = Math.ceil(p.r * 5)

      if (!layer) {
        layer = document.createElement('canvas')
        layerCtx = layer.getContext('2d')
      }
      const L = layerCtx!
      if (layer.width !== pad * 2) {
        layer.width = layer.height = pad * 2
      }
      L.clearRect(0, 0, pad * 2, pad * 2)
      L.save()
      L.translate(pad, pad)

      if (p.ring) ring(L, p.r, tilt, 'back')
      if (p.baked) L.drawImage(p.baked.img, -d / 2, -d / 2, d, d)

      // The ring's shadow on the globe, clipped so it cannot spill past the limb.
      if (p.ring) {
        L.save()
        L.beginPath()
        L.arc(0, 0, p.r, 0, Math.PI * 2)
        L.clip()
        L.rotate(tilt)
        L.strokeStyle = 'rgba(4,6,12,0.42)'
        L.lineWidth = p.r * 0.28
        L.beginPath()
        L.ellipse(0, p.r * 0.12, p.r * 1.4, p.r * 0.4, 0, 0, Math.PI)
        L.stroke()
        L.restore()
      }

      if (p.ring) ring(L, p.r, tilt, 'front')
      L.restore()

      ctx!.save()
      ctx!.globalAlpha = 0.78
      ctx!.drawImage(layer, x - pad, y - pad)
      ctx!.restore()
    }

    /** Concentric ring bands. `half` selects the arc behind or in front of the globe. */
    function ring(L: CanvasRenderingContext2D, R: number, tilt: number, half: 'back' | 'front') {
      const inner = R * 1.35 // clear of the surface — a ring cannot exist below the Roche limit
      const outer = R * 2.25
      const from = half === 'back' ? Math.PI : 0
      const to = half === 'back' ? Math.PI * 2 : Math.PI

      L.save()
      L.rotate(tilt)
      for (let i = 0; i < 26; i++) {
        const f = i / 25
        const rr = inner + (outer - inner) * f
        const gap = Math.abs(f - 0.52) < 0.05 // a Cassini-style division
        const band = 0.1 + 0.16 * Math.abs(Math.sin(f * 22))
        const a = gap ? 0.015 : band * (1 - Math.pow(Math.abs(f - 0.45) * 2, 2) * 0.55)
        L.strokeStyle = `rgba(${f < 0.5 ? '255,226,186' : '226,206,182'},${a})`
        L.lineWidth = ((outer - inner) / 26) * 1.25
        L.beginPath()
        L.ellipse(0, 0, rr, rr * 0.3, 0, from, to)
        L.stroke()
      }
      L.restore()
    }

    function frame(t: number) {
      px += (tx - px) * 0.045
      py += (ty - py) * 0.045

      ctx!.fillStyle = '#05070f'
      ctx!.fillRect(0, 0, w, h)

      // Nebula.
      ctx!.globalCompositeOperation = 'lighter'
      for (const c of clouds) {
        const size = Math.max(w, h) * c.s
        const cx = (c.x + Math.sin(t * c.vx) * 0.04) * w - size / 2 + px * 14
        const cy = (c.y + Math.cos(t * c.vy) * 0.04) * h - size / 2 + py * 14
        ctx!.drawImage(c.img, cx, cy, size, size)
      }

      // Galaxies sit between the nebula and the stars — far enough back to be scenery.
      for (const gx of galaxies) {
        // Drawn at the size it was baked for. Downscaling stays crisp; upscaling is what
        // made these look like low-resolution smudges.
        const size = Math.min(Math.max(w, h) * gx.s, gx.img.width / dpr)
        ctx!.save()
        ctx!.globalAlpha = gx.a
        ctx!.translate(gx.x * w + px * 6, gx.y * h + py * 6)
        ctx!.rotate(gx.tilt + t * gx.spin)
        ctx!.drawImage(gx.img, -size / 2, -size / 2, size, size)
        ctx!.restore()
      }

      // Stars.
      for (const s of stars) {
        const k = (s.depth + 1) * 9
        const x = s.x * w + px * k
        const y = s.y * h + py * k
        const tw = still ? 1 : 0.65 + 0.35 * Math.sin(t * 0.0012 * s.speed + s.phase)
        ctx!.fillStyle = `rgba(${s.tint},${s.a * tw})`
        if (s.r <= 1.2) {
          // Indistinguishable from a disc at this size, and several times cheaper.
          ctx!.fillRect(x, y, s.r * 1.6, s.r * 1.6)
        } else {
          ctx!.beginPath()
          ctx!.arc(x, y, s.r, 0, Math.PI * 2)
          ctx!.fill()
        }
      }

      drawHole(t)
      ctx!.globalCompositeOperation = 'source-over'

      for (const p of planets) drawPlanet(p, t)

      // Vignette. Keeps the edges from floating and guarantees the centre stays dark
      // enough for text, whatever the nebula happens to be doing underneath.
      if (!vigGrad) {
        vigGrad = ctx!.createRadialGradient(w * 0.5, h * 0.46, 0, w * 0.5, h * 0.46, Math.max(w, h) * 0.78)
        vigGrad.addColorStop(0, 'rgba(5,7,15,0)')
        vigGrad.addColorStop(0.55, 'rgba(5,7,15,0.35)')
        vigGrad.addColorStop(1, 'rgba(3,4,10,0.85)')
      }
      ctx!.fillStyle = vigGrad
      ctx!.fillRect(0, 0, w, h)

      // Shooting stars, occasionally.
      ctx!.globalCompositeOperation = 'lighter'
      if (!still && Math.random() < 0.0022 && shooters.length < 2) {
        shooters.push({
          x: Math.random() * w * 0.7,
          y: Math.random() * h * 0.4,
          vx: 5 + Math.random() * 4,
          vy: 1.6 + Math.random() * 1.6,
          life: 1,
        })
      }
      shooters = shooters.filter((sh) => sh.life > 0)
      for (const sh of shooters) {
        sh.x += sh.vx
        sh.y += sh.vy
        sh.life -= 0.014
        const g = ctx!.createLinearGradient(sh.x, sh.y, sh.x - sh.vx * 16, sh.y - sh.vy * 16)
        g.addColorStop(0, `rgba(255,246,224,${sh.life * 0.9})`)
        g.addColorStop(1, 'rgba(255,246,224,0)')
        ctx!.strokeStyle = g
        ctx!.lineWidth = 1.6
        ctx!.beginPath()
        ctx!.moveTo(sh.x, sh.y)
        ctx!.lineTo(sh.x - sh.vx * 16, sh.y - sh.vy * 16)
        ctx!.stroke()
      }
      ctx!.globalCompositeOperation = 'source-over'
    }

    resize()
    if (still) {
      frame(0)
      return
    }

    let raf = 0
    let running = true
    const loop = (t: number) => {
      if (!running) return
      frame(t)
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)

    // Stop entirely when the tab is hidden — no reason to burn a phone battery in background.
    const onVis = () => {
      if (document.hidden) {
        running = false
        cancelAnimationFrame(raf)
      } else if (!running) {
        running = true
        raf = requestAnimationFrame(loop)
      }
    }

    window.addEventListener('resize', resize)
    window.addEventListener('pointermove', onMove, { passive: true })
    document.addEventListener('visibilitychange', onVis)
    return () => {
      running = false
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', resize)
      window.removeEventListener('pointermove', onMove)
      document.removeEventListener('visibilitychange', onVis)
    }
  }, [])

  return <canvas ref={ref} className="cosmos" aria-hidden="true" />
}
