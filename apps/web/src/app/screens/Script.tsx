/**
 * Script Studio — step 3.
 *
 * The highest-leverage screen in the product. The script decides scene count, voice length,
 * caption density and therefore cost, so this is the cheapest place to fix a video — a pacing
 * problem caught here costs a cent, and the same problem caught on the storyboard costs a
 * full round of generation.
 *
 * Three things make it more than a text box:
 *   - it is edited as beats, because every downstream stage needs the parts, not the prose;
 *   - length is measured in seconds as you type, from the language pack's reading speed, so
 *     "too long" is visible before anything is generated at the wrong pace;
 *   - the hook gets its own treatment, because 71% of viewers decide inside three seconds.
 */
import { useMemo, useState } from 'react'

import { useHooks, useRewrite, useTiming } from '../../api/queries'
import { BEAT_SPEC, speakMs, type Beat } from '../data'
import type { Studio } from '../live'
import { Head, View } from '../Shell'
import { IconPlus, IconRefresh, IconSpark, IconTrash } from '../icons'

const TARGET_MS = 60_000

/** The rewrite operations, and what each does. Keys match the API's `operation` values. */
const REWRITE_LABELS: [string, string][] = [
  ['tighter', 'Tighter'],
  ['simpler', 'Simpler'],
  ['punchier', 'Punchier'],
  ['concrete', 'More concrete'],
  ['shorter', 'Shorter'],
]

export function Script({ s }: { s: Studio }) {
  const [hooksOpen, setHooksOpen] = useState(false)
  const hooks = useHooks()
  const rewrite = useRewrite()

  const script = useMemo(() => s.beats.map((b) => b.text).join(' '), [s.beats])

  /**
   * Length from the server's language pack, with the local estimate as the fallback.
   *
   * Both use the same characters-per-second figure, so they agree — but the pack is the one
   * that gets a new language without a frontend release, and reading speed varies about
   * threefold across scripts. The local copy exists so the number does not flicker between
   * keystrokes while the request is in flight.
   */
  const timing = useTiming(script, s.language, TARGET_MS)
  const local = s.beats.reduce((n, b) => n + speakMs(b.text, s.language), 0)
  const total = timing.data?.spoken_ms ?? local
  const over = total > TARGET_MS
  const secs = (ms: number) => `${(ms / 1000).toFixed(1)}s`

  const applyHook = (text: string) => {
    const hook = s.beats.find((b) => b.kind === 'hook')
    if (hook) s.setBeat(hook.id, text)
    setHooksOpen(false)
  }

  const openHooks = () => {
    setHooksOpen((open) => {
      if (!open && !hooks.data) hooks.mutate({ text: script, language: s.language })
      return !open
    })
  }

  return (
    <View
      action={
        <>
          <div className="why">
            <span>
              {s.beats.length} beats · reads in {secs(total)}
            </span>
            <b>{s.scenes.length} scenes</b>
          </div>
          <button
            className="btn primary block"
            onClick={() => {
              // Save before moving on. The storyboard generates from these lines, so an
              // unsaved edit would be silently generated around.
              s.saveBeats()
              s.go('board')
            }}
          >
            <IconSpark size={19} />
            Make the storyboard
          </button>
        </>
      }
    >
      <Head
        kicker="Script"
        title="Say it out loud"
        lede="Written as beats, timed as you type. Fix the pacing here — it is the cheapest place to fix anything."
      />

      <div className={`budget${over ? ' over' : ''}`}>
        <b>{secs(total)}</b>
        <div className="grow">
          <div className="meter">
            <i style={{ width: `${Math.min(100, (total / TARGET_MS) * 100)}%` }} />
          </div>
          <p className="muted" style={{ margin: '8px 0 0', font: '400 13px/1.4 var(--ui)' }}>
            {over
              ? `${secs(total - TARGET_MS)} over a 60-second target. Trim a turn, or shorten the setup.`
              : `${secs(TARGET_MS - total)} of headroom at a 60-second target.`}
          </p>
        </div>
      </div>

      <div className="card pad">
        {s.beats.map((b) => (
          <BeatRow
            key={b.id}
            beat={b}
            lang={s.language}
            onChange={(t) => s.setBeat(b.id, t)}
            onAdd={() => s.addBeat(b.id)}
            onRemove={s.beats.length > 2 ? () => s.removeBeat(b.id) : undefined}
            onHooks={b.kind === 'hook' ? openHooks : undefined}
            onRewrite={(operation) =>
              rewrite.mutate(
                { line: b.text, operation, language: s.language },
                { onSuccess: (out) => s.setBeat(b.id, out.line) },
              )
            }
            busy={rewrite.isPending}
          />
        ))}
        {!s.beats.length ? (
          <p className="muted">No script yet — this fills in once there is a plan.</p>
        ) : null}
      </div>

      {hooksOpen ? (
        <section style={{ marginTop: 22 }}>
          <div className="label">
            {hooks.isPending ? 'Writing some openings…' : 'Other hooks — pick one'}
          </div>
          <div className="hooks">
            {(hooks.data?.hooks ?? []).map((h) => (
              <button key={h.text} className="hookopt" onClick={() => applyHook(h.text)}>
                {/* The pattern is named, not decorative: a creator who knows they picked
                    "surprising number" can write the next one themselves. */}
                <em title={h.why}>{h.pattern}</em>
                <span>{h.text}</span>
              </button>
            ))}
          </div>
        </section>
      ) : null}
    </View>
  )
}

function BeatRow({
  beat,
  lang,
  onChange,
  onAdd,
  onRemove,
  onHooks,
  onRewrite,
  busy,
}: {
  beat: Beat
  lang: string
  onChange: (text: string) => void
  onAdd: () => void
  onRemove?: () => void
  onHooks?: () => void
  onRewrite: (operation: string) => void
  busy: boolean
}) {
  const spec = BEAT_SPEC[beat.kind]
  const ms = speakMs(beat.text, lang)
  const over = ms > spec.targetMs * 1.35

  return (
    <div className={`beat ${beat.kind}`}>
      <div className="beat-kind">
        <b>{spec.label}</b>
        <span className={over ? 'over' : ''}>{(ms / 1000).toFixed(1)}s</span>
      </div>
      <div>
        <textarea
          rows={beat.kind === 'hook' ? 2 : 2}
          value={beat.text}
          placeholder={spec.note}
          onChange={(e) => onChange(e.target.value)}
        />
        <div className="beat-tools">
          {onHooks ? (
            <button className="mini" onClick={onHooks}>
              <IconRefresh size={13} /> Other hooks
            </button>
          ) : null}
          {/* One operation, one beat. Never "regenerate everything" — the same principle
              as per-scene regeneration on the storyboard. */}
          {REWRITE_LABELS.map(([op, label]) => (
            <button key={op} className="mini" disabled={busy} onClick={() => onRewrite(op)}>
              {label}
            </button>
          ))}
          <button className="mini" onClick={onAdd}>
            <IconPlus size={13} /> Beat
          </button>
          {onRemove ? (
            <button className="mini warn" onClick={onRemove}>
              <IconTrash size={13} />
            </button>
          ) : null}
        </div>
        {over ? (
          <p style={{ margin: '8px 0 0', font: '400 13px/1.4 var(--ui)', color: '#ff9c8f' }}>
            {spec.note}
          </p>
        ) : null}
      </div>
    </div>
  )
}
