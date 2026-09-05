/**
 * What did the user just give us?
 *
 * One job only: preselect a task when the input makes one obvious, so dropping a video lands
 * on Subtitle without anyone naming it. `design-brief.md` asks for that — "the system detects
 * what it got".
 *
 * It used to do more, and the more was wrong. It decided which tasks were *allowed* and greyed
 * out the rest, and it narrated whatever was attached — a line written before the task was
 * chosen, which then described the wrong task's behaviour. Both are gone. The person picks;
 * this only saves them a click; and what a task can actually accept is the server's answer,
 * because the server is the only thing that knows.
 *
 * Kept as a pure function so the rule is one readable table rather than branching scattered
 * through a component.
 */

export type InputKind = 'video' | 'audio' | 'image' | 'link' | 'script' | 'idea' | 'empty'

export type Detected = {
  kind: InputKind
  /** Which task to preselect, best first. Empty leaves whatever was chosen alone. */
  recipes: string[]
}

const VIDEO = /\.(mp4|mov|m4v|webm|mkv|avi)$/i
const AUDIO = /\.(mp3|m4a|wav|aac|ogg|flac)$/i
const IMAGE = /\.(png|jpe?g|webp|gif|heic)$/i
const URL_LIKE = /^https?:\/\/\S+$/i

/**
 * A pasted script rather than an idea.
 *
 * Length alone is a poor signal — someone can type a long idea. Structure is better: real
 * scripts arrive with line breaks or several sentences, because they were written to be
 * read aloud.
 */
function looksScripted(text: string): boolean {
  const lines = text.trim().split(/\n+/).filter(Boolean)
  const sentences = text.split(/[.!?]+\s/).filter((s) => s.trim().length > 12)
  return lines.length >= 3 || sentences.length >= 4
}

/**
 * Detection suggests; it never gates.
 *
 * It preselects a task when the input makes one obvious, and describes what it saw. It used
 * to also decide which tasks were *allowed*, greying out the rest — which produced five
 * identical warnings on a dropped image and put the detector in charge of a choice the person
 * had already made. What a task can actually accept is the server's answer, in a sentence.
 */
export function detect(text: string, file: File | null): Detected {
  if (file) {
    if (VIDEO.test(file.name)) {
      return {
        kind: 'video',
        // A finished video can be captioned, dubbed or cut. It cannot be "planned" — the
        // plan already exists, it is the video.
        recipes: ['subtitle_only', 'dub', 'clip_long_video'],
      }
    }
    if (AUDIO.test(file.name)) {
      return {
        kind: 'audio',
        // No suggestion. Clip and Subtitle cut and caption frames, and a sound file has none —
        // cutting one does not even fail, it produces a 0x0 MP4. Making a video from a podcast
        // means generating visuals for it, which is a sixth lane and is not built. The task
        // stays whatever was chosen, and the server explains if it cannot use this.
        recipes: [],
      }
    }
    if (IMAGE.test(file.name)) {
      return {
        kind: 'image',
        recipes: ['explainer'],
      }
    }
    return {
      kind: 'script',
      recipes: ['explainer'],
    }
  }

  const trimmed = text.trim()
  if (!trimmed) return { kind: 'empty', recipes: [] }

  if (URL_LIKE.test(trimmed)) {
    return {
      kind: 'link',
      recipes: ['explainer'],
    }
  }
  if (looksScripted(trimmed)) {
    return {
      kind: 'script',
      recipes: ['explainer'],
    }
  }
  return {
    kind: 'idea',
    recipes: ['explainer'],
  }
}
