/**
 * The pipeline strip.
 *
 * Seven stages as seven icons, lit as each completes. Jobs run for minutes, and the usual
 * alternative — a spinner and a percentage — tells the creator nothing about *what* is
 * happening or how much is left.
 *
 * The labels are there for people who want them, but the strip is designed to be read
 * without them: position says where you are, colour says what is done.
 */
import {
  IconCheck,
  IconDownload,
  IconEar,
  IconFilm,
  IconSpeak,
  IconSpark,
  IconType,
  IconUpload,
  IconWand,
} from './icons'

export type StageState = 'todo' | 'now' | 'done'

/**
 * How each backend stage is drawn. Not an ordering — the server sends the order, because it
 * is the one that runs them.
 */
export const STAGES = [
  { id: 'ingest', label: 'Take in', Icon: IconUpload },
  { id: 'transcribe', label: 'Listen', Icon: IconEar },
  { id: 'analyze', label: 'Find', Icon: IconSpark },
  { id: 'plan', label: 'Plan', Icon: IconWand },
  { id: 'generate', label: 'Picture', Icon: IconFilm },
  { id: 'voice', label: 'Voice', Icon: IconSpeak },
  { id: 'captions', label: 'Words', Icon: IconType },
  { id: 'compose', label: 'Video', Icon: IconDownload },
] as const

export type StageId = (typeof STAGES)[number]['id']

/** A readable label for a stage this build has not been taught to draw. */
function title(id: string): string {
  const words = id.replace(/[_-]+/g, ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

export function Pipeline({
  used,
  state,
}: {
  /** Which stages this recipe runs. Others are hidden — not greyed, hidden. */
  used: readonly string[]
  state: Partial<Record<string, StageState>>
}) {
  /*
   * Drawn in the order the server gave, and never dropped.
   *
   * This used to filter a local list by `used`, which meant a stage the backend had but this
   * table did not simply vanished: `transcribe` shipped on the server and Subtitle drew three
   * steps for four, with nothing lit for the whole time it was listening. An unknown stage
   * now shows up — unlabelled and generic, but visibly there.
   */
  const shown = used.map(
    (id) => STAGES.find((s) => s.id === id) ?? { id, label: title(id), Icon: IconSpark },
  )

  return (
    <div className="pipeline" role="group" aria-label="Progress">
      {shown.map(({ id, label, Icon }, i) => {
        const at = state[id] ?? 'todo'
        return (
          <span key={id} style={{ display: 'contents' }}>
            {i > 0 ? <i className="pipe-link" /> : null}
            <span className="pipe-step" data-state={at} title={label}>
              <span className="dot">
                {at === 'done' ? <IconCheck size={17} /> : <Icon size={17} />}
              </span>
              <small>{label}</small>
            </span>
          </span>
        )
      })}
    </div>
  )
}
