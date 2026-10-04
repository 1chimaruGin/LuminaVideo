/**
 * Static catalogues and shared types.
 *
 * What lives here is everything the app knows without asking the server: the funnel's shape,
 * the caption styles and aspect ratios on offer, the beat specification, and per-language
 * reading speeds. The live view model is in ./live.ts — anything the server owns belongs
 * there, not here.
 *
 * `Scene` mirrors the backend's domain (services/lumina/src/lumina/domain/plan.py): a scene
 * carries its own status and its own assets, so finishing one never disturbs another.
 */

export const UPGRADE_COST = 15
export const MONTHLY_CREDITS = 200

export type SceneStatus = 'preview' | 'final'

export type Scene = {
  /** The server's scene id. A uuid, and opaque — never index into the array with it. */
  id: string
  line: string
  caption: string
  /** Formatted for display. Never parsed back — read `durationMs` for arithmetic. */
  time: string
  durationMs: number
  /** Whether narration has been attached. `voice` is a whole-plan stage, not a per-scene one. */
  hasVoice: boolean
  /**
   * Where this scene sits inside the project's source video, for the lanes that keep footage.
   * Null when the scene's picture is generated — there is no source to point into.
   */
  sourceStartMs: number | null
  /** The same line in the other burnt-in languages, keyed by BCP-47 tag. */
  translations: Record<string, string>
  /**
   * Which language this line is in, which is not always the project's source language.
   *
   * A file can change language part way through — Japanese speech under an English song —
   * and `sourceLanguage` is then only the commonest answer, not the answer for every row.
   */
  spokenLanguage: string | null
  media: string
  /** The beat's picture as a plain URL, or null while it has none. */
  pictureUrl: string | null
  /** True when that picture is a clip: it has to be played, not drawn. */
  pictureIsClip: boolean
  status: SceneStatus
  selected: boolean
  busy: boolean
  loaded: boolean
  /** The image prompt. Editable, because "regenerate" without changing anything is a coin flip. */
  prompt: string
  /** Voice direction for this line: pace, and which words to lean on. */
  pace: number
  emphasis: string[]
}

export const CAPTION_STYLES = [
  {
    id: 'pop',
    name: 'Bold pop',
    css: 'background:var(--accent);color:#1d1204;padding:6px 12px;border-radius:8px;font:700 15px/1 var(--display)',
  },
  {
    id: 'line',
    name: 'Clean line',
    css: 'background:#0b0e18;color:#fff;padding:6px 12px;border-radius:6px;font:600 15px/1 var(--ui)',
  },
  {
    id: 'glow',
    name: 'Soft glow',
    css: 'color:#fff;font:600 15px/1 var(--ui);text-shadow:0 0 14px rgba(160,200,255,.9)',
  },
  {
    id: 'outline',
    name: 'Outline',
    css: 'color:#fff;font:800 15px/1 var(--ui);-webkit-text-stroke:0.8px #05070f;paint-order:stroke fill',
  },
  {
    id: 'news',
    name: 'Lower third',
    css: 'background:rgba(6,8,16,.9);color:#f2f5ff;padding:5px 11px;border-left:3px solid var(--accent);font:550 15px/1 var(--ui)',
  },
  {
    id: 'spot',
    name: 'Word spot',
    css: 'color:#fff;font:800 15px/1 var(--ui);text-shadow:0 1px 5px #000',
  },
  {
    id: 'paper',
    name: 'Paper',
    css: 'background:rgba(248,248,245,.94);color:#14161c;padding:5px 10px;border-radius:7px;font:700 15px/1 var(--ui)',
  },
]

export const FORMATS = [
  { id: '9:16', where: 'TikTok, Reels & Shorts', tag: 'Tall', w: 30, h: 52 },
  { id: '1:1', where: 'Instagram feed', tag: 'Square', w: 44, h: 44 },
  { id: '16:9', where: 'YouTube & X', tag: 'Wide', w: 56, h: 32 },
]

export const PACKS = [
  { credits: 200, price: '$6', best: false },
  { credits: 500, price: '$12', best: true },
  { credits: 1200, price: '$25', best: false },
]

export type Recipe = {
  id: string
  name: string
  blurb: string
  needs: string
  credits: number
  competes: string
}

export const RECIPES: Recipe[] = [
  {
    id: 'explainer',
    name: 'Generate video',
    blurb: 'From an image, a link, or just describe it.',
    needs: 'A link or an idea',
    credits: 4,
    competes: 'Pictory',
  },
  {
    id: 'narrate',
    name: 'Narrate',
    blurb: 'Your script, spoken over footage you pick.',
    needs: 'A script and a video',
    credits: 3,
    competes: 'Pictory',
  },
  {
    id: 'dub',
    name: 'Dub',
    blurb: 'Your video, speaking another language.',
    needs: 'A finished video',
    credits: 3,
    competes: 'HeyGen',
  },
  {
    id: 'clip_long_video',
    name: 'Clip video',
    blurb: 'Find the moments worth posting in a long video.',
    needs: 'A long video',
    credits: 2,
    competes: 'Opus Clip',
  },
  {
    id: 'subtitle_only',
    name: 'Subtitle',
    blurb: 'Animated captions on a video you already have.',
    needs: 'A finished video',
    credits: 1,
    competes: 'Submagic',
  },
]

/* ---------------------------------------------------------------- script */

export type BeatKind = 'hook' | 'setup' | 'turn' | 'payoff' | 'cta'

export type Beat = {
  /** The scene this beat is the script line of, when it came from a plan. */
  sceneId?: string
  id: number
  kind: BeatKind
  text: string
}

/** What each beat is for, and how long it should run. */
export const BEAT_SPEC: Record<BeatKind, { label: string; note: string; targetMs: number }> = {
  hook: {
    label: 'Hook',
    note: '71% of viewers decide here. Lead with the surprise, not the setup.',
    targetMs: 3000,
  },
  setup: {
    label: 'Setup',
    note: 'One sentence of context. No more.',
    targetMs: 5000,
  },
  turn: {
    label: 'Turn',
    note: 'One idea per beat. Cut when it lands.',
    targetMs: 7000,
  },
  payoff: {
    label: 'Payoff',
    note: 'The thing they will repeat to someone else.',
    targetMs: 5000,
  },
  cta: { label: 'CTA', note: 'One action, stated plainly.', targetMs: 4000 },
}

/**
 * Reading speed, characters per second.
 *
 * Mirrors `typography.cps` in the backend LanguagePack, which exists because reading speed
 * varies about threefold between languages. Used here in reverse: to tell the writer whether
 * a beat is over length *before* anything is generated at the wrong pace.
 */
export const CPS: Record<string, number> = {
  en: 17,
  my: 9.5,
  ja: 8,
  ko: 9,
  zh: 6.5,
  th: 10,
}

export function speakMs(text: string, lang = 'en'): number {
  const cps = CPS[lang] ?? 17
  return Math.round((text.trim().length / cps) * 1000)
}

export type Destination = {
  id: string
  name: string
  ideal: [number, number]
  limit: number
}

export const DESTINATIONS: Destination[] = [
  {
    id: 'tiktok',
    name: 'TikTok',
    ideal: [300, 1200],
    limit: 4000,
  },
  {
    id: 'reels',
    name: 'Instagram Reels',
    ideal: [100, 300],
    limit: 2200,
  },
  {
    id: 'shorts',
    name: 'YouTube Shorts',
    ideal: [150, 500],
    limit: 5000,
  },
]

export type Screen =
  | 'start'
  | 'recipe'
  | 'script'
  | 'board'
  | 'preview'
  | 'scene'
  | 'clips'
  | 'captions'
  | 'deliver'
  | 'projects'
  | 'identity'
  | 'credits'
  | 'voice'
  | 'auth'
  | 'profile'

/**
 * The funnel. Progress through these is the app's only navigation.
 *
 * Script sits between the plan and the storyboard deliberately: it is the cheapest place to
 * fix pacing, and every downstream cost — scene count, voice length, caption density — is
 * decided by it.
 */
/**
 * The screens a lane actually walks through, in order.
 *
 * There is no single funnel. Subtitle routes around the storyboard entirely — its words are
 * already in the footage — and Clip stops at the moments before it reaches one. This used to
 * be one hardcoded list belonging to the generate lanes, so on the two lanes that leave it
 * `indexOf` returned -1 and the whole step indicator vanished: no counter, no progress bar,
 * and the project's own title replaced by a generic label. The screen looked like it belonged
 * to a different app.
 */
const FUNNELS: Record<string, readonly Screen[]> = {
  subtitle_only: ['start', 'recipe', 'captions', 'deliver'],
  // Same shape as Subtitle, and for the same reason: both start from a finished video and
  // the screen in the middle is where the creator reads back what was heard before it is
  // committed to. Dub commits it to a voice instead of to burnt-in text.
  dub: ['start', 'recipe', 'captions', 'deliver'],
  // Same shape again: what the creator checks in the middle is the words and their timing,
  // which is exactly what the caption editor is. Narrate differs only in where those timings
  // came from — the narration made them rather than inheriting them from footage.
  narrate: ['start', 'recipe', 'captions', 'deliver'],
  clip_long_video: ['start', 'recipe', 'clips', 'board', 'deliver'],
}

const DEFAULT_FUNNEL = ['start', 'recipe', 'script', 'board', 'deliver'] as const

export function stepsFor(recipe: string): readonly Screen[] {
  return FUNNELS[recipe] ?? DEFAULT_FUNNEL
}

/**
 * Every screen that belongs to some lane's funnel.
 *
 * Used to tell a screen that is simply *elsewhere* — the job list, the identity kit — from
 * one that belongs to a different lane than the project is in. The first is a legitimate
 * place to be; the second is a creator stranded on a step their project does not have.
 */
const IN_A_FUNNEL: ReadonlySet<Screen> = new Set([
  ...Object.values(FUNNELS).flat(),
  ...DEFAULT_FUNNEL,
])

/**
 * Where a lane goes once its brief is answered.
 *
 * Read off the funnel, because the funnel is already the list of screens this lane has. It
 * used to be a ternary in `Shell.tsx` naming two lanes explicitly and sending everything
 * else to the script editor — so Dub, which has no script screen and no storyboard, walked
 * into both: 274 transcribed lines offered as beats to rewrite, under a button reading
 * "Make the storyboard". A lane added to `FUNNELS` now routes correctly without anyone
 * remembering this file exists.
 */
export function afterBrief(recipe: string): Screen {
  const steps = stepsFor(recipe)
  const at = steps.indexOf('recipe')
  return steps[at + 1] ?? 'board'
}

/**
 * Where a project opens when you pick it out of the list.
 *
 * The step before Deliver, which is where the work of each lane actually is: the storyboard
 * for a lane that draws pictures, the caption editor for one that subtitles. It used to be
 * `board` for everything, so opening a subtitle project showed a storyboard of forty-one
 * empty frames offering to generate them at fifteen credits each — a screen for a lane that
 * project is not in, about work it does not have.
 */
export function homeFor(recipe: string): Screen {
  const steps = stepsFor(recipe)
  const end = steps.indexOf('deliver')
  return (end > 0 ? steps[end - 1] : steps[steps.length - 1]) ?? 'recipe'
}

/**
 * Where to be, given where you are and which lane you are in.
 *
 * A subtitle project restored onto the Script screen is the bug this exists for: the screen
 * is remembered across reloads, the lane is decided by the project, and nothing checked that
 * the two agreed. It returns the same screen whenever that is reasonable — including screens
 * outside every funnel, which are always fine to be on.
 */
export function screenFor(screen: Screen, recipe: string): Screen {
  const steps = stepsFor(recipe)
  if (steps.includes(screen) || !IN_A_FUNNEL.has(screen)) return screen

  /*
   * Moved to the same *position* rather than to a fixed step.
   *
   * How far along the creator was is the thing worth keeping: someone editing captions in one
   * lane belongs at the equivalent step of the other, not sent back to the beginning. Looked
   * up in whichever funnel actually contains the screen, since it need not be the default one.
   */
  const owner =
    [...Object.values(FUNNELS), DEFAULT_FUNNEL].find((f) => f.includes(screen)) ?? DEFAULT_FUNNEL
  const was = owner.indexOf(screen)
  return steps[Math.min(was < 0 ? 1 : was, steps.length - 1)] ?? steps[0]!
}

/**
 * When each word of a caption appears, in milliseconds from the start of its scene.
 *
 * The same rule the renderer uses (`execution/compose/captions.py`, `word_timings`): the
 * scene's duration split across its words weighted by length, not evenly. An even split
 * visibly lags on a long word, and a highlight drifting out of sync with the voice is the
 * first thing anyone notices.
 *
 * Kept in step with the backend deliberately. This screen exists to show what the render will
 * do — numbers of its own invention would be worse than showing none, and an earlier version
 * displayed `index * 0.42s`, which was exactly that.
 */
export function wordTimings(text: string, durationMs: number): [string, number][] {
  const words = text.split(/\s+/).filter(Boolean)
  if (!words.length) return []

  const total = words.reduce((n, w) => n + w.length, 0)
  let offset = 0
  return words.map((word) => {
    const at = offset
    offset += Math.round(durationMs * (word.length / total))
    return [word, at]
  })
}
