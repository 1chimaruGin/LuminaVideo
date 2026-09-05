/**
 * The video screen.
 *
 * Watch the whole thing, jump between scenes, and fix the one that is wrong — without
 * leaving. Every tool in this category converged on the same three parts, and for a good
 * reason: a scene strip to navigate, a stage to watch, and one panel that edits whatever the
 * strip has selected. Pictory calls it a scene strip; the shape is the same in Descript and
 * InVideo.
 *
 * It is deliberately NOT a timeline. `design-brief.md` rules one out, and the reason holds:
 * a frame-level timeline asks the creator to think in frames, which is the skill the product
 * exists to not require. A scene is the unit here, so the strip is one thumbnail per scene.
 *
 * Two things can be on the stage, and which one matters:
 *
 *   - Once a render exists, the real MP4 plays — captions burned in, narration muxed, exactly
 *     what will be posted.
 *   - Before that, the storyboard plays *itself*: each scene's picture held for its own
 *     duration with its caption over it. That is not a mock-up of a video, it is the actual
 *     pacing the render will have, available before spending anything on one. Getting the
 *     rhythm wrong is the most expensive mistake to find late.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { assetUrl } from '../../api/client'
import type { Scene } from '../data'
import type { Studio } from '../live'
import { View } from '../Shell'
import { IconCheck, IconPlay, IconRefresh, IconSpark } from '../icons'

/**
 * How much width a scene's cell gets.
 *
 * Proportional to its length, but never more than four times the shortest — otherwise one
 * long scene squeezes the rest to slivers that cannot be read or tapped, which is worse than
 * losing the proportion. The exact length is written on the cell either way.
 */
function weightOf(durationMs: number, scenes: Scene[]): number {
  const shortest = Math.min(...scenes.map((x) => x.durationMs), durationMs) || 1
  return Math.min(4, Math.max(1, durationMs / shortest))
}

/** `0:07`. Seconds only — nothing here runs to an hour. */
function clock(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000))
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

export function Preview({ s }: { s: Studio }) {
  const scenes = s.scenes
  const [at, setAt] = useState(0)
  const [playing, setPlaying] = useState(false)
  const video = useRef<HTMLVideoElement>(null)

  /** The finished vertical cut, once there is one. */
  const rendered = useMemo(() => {
    const row = s.renders.find((r) => r.aspect === '9:16' && r.status === 'ready' && r.asset_id)
    return row?.asset_id ? assetUrl(row.asset_id) : null
  }, [s.renders])

  /**
   * What actually goes on the stage, in order of what is most true.
   *
   *   1. The finished render — what will be posted.
   *   2. The creator's own footage, for the lanes that keep it. Someone who uploads a video
   *      to be subtitled or clipped expects to watch *that video*; before this they got a
   *      grey plate, because the player only knew about generated stills.
   *   3. The storyboard playing itself, for the lanes that draw their frames.
   */
  const film = rendered ?? s.sourceVideo
  const fromSource = !rendered && Boolean(s.sourceVideo)

  /** Where each scene starts, so a position in the video maps to a scene and back. */
  const starts = useMemo(() => {
    let running = 0
    return scenes.map((scene) => {
      const start = running
      running += scene.durationMs
      return start
    })
  }, [scenes])

  const total = scenes.reduce((n, x) => n + x.durationMs, 0)
  const current = scenes[at]

  const jump = useCallback(
    (index: number) => {
      const next = Math.max(0, Math.min(index, scenes.length - 1))
      setAt(next)
      if (!video.current) return

      // Where in the film this scene is. In a render the scenes are laid end to end, so it is
      // the running total. In the creator's own footage it is where the moment actually sits —
      // a clip's two moments can be forty minutes apart, and seeking to the running total
      // would land in the middle of neither.
      const scene = scenes[next]
      const seconds = fromSource
        ? (scene?.sourceStartMs ?? starts[next] ?? 0) / 1000
        : (starts[next] ?? 0) / 1000
      video.current.currentTime = seconds
    },
    [scenes, starts, fromSource],
  )

  /**
   * Advance through the stills.
   *
   * Only when there is no rendered file — with one, the `<video>` element is the clock and a
   * second timer running beside it would drift against it within a few seconds.
   */
  useEffect(() => {
    if (!playing || film || !current) return
    const timer = setTimeout(() => {
      if (at + 1 < scenes.length) setAt(at + 1)
      else {
        setAt(0)
        setPlaying(false)
      }
    }, current.durationMs)
    return () => clearTimeout(timer)
  }, [playing, film, at, current, scenes.length])

  /** With a real video, the video's own position decides which scene is selected. */
  const onTime = useCallback(() => {
    const now = (video.current?.currentTime ?? 0) * 1000
    const marks = fromSource
      ? scenes.map((scene, i) => scene.sourceStartMs ?? starts[i] ?? 0)
      : starts
    let index = 0
    for (let i = 0; i < marks.length; i += 1) if (now >= (marks[i] ?? 0)) index = i
    setAt(index)
  }, [starts, scenes, fromSource])

  const toggle = () => {
    if (film && video.current) {
      if (video.current.paused) void video.current.play()
      else video.current.pause()
      return
    }
    setPlaying((on) => !on)
  }

  if (!scenes.length) {
    return (
      <View mid>
        <p className="muted">Nothing to watch yet — the storyboard is empty.</p>
      </View>
    )
  }

  return (
    <View
      action={
        <>
          <div className="why">
            <span>
              {rendered
                ? 'The finished cut, with captions and narration.'
                : fromSource
                  ? 'Your video. The strip marks where each line falls.'
                  : 'The real pacing, before you spend anything on a render.'}
            </span>
            <b>
              {clock(starts[at] ?? 0)} / {clock(total)}
            </b>
          </div>
          <div style={{ display: 'flex', gap: 10 }}>
            <button className="btn" onClick={() => s.go('board')}>
              Storyboard
            </button>
            <button className="btn primary" style={{ flex: 1 }} onClick={() => s.go('deliver')}>
              <IconSpark size={18} />
              {rendered ? 'Deliver' : 'Render it'}
            </button>
          </div>
        </>
      }
    >
      {/* Every screen needs a heading. This one's subject is the video itself, so a visible
          title would repeat the topbar and push the player down the page. */}
      <h1 className="sr-only">{s.project} — preview</h1>

      <div className="player">
        {/* The stage. A real video once one exists, the storyboard playing itself before. */}
        <div className="stage" data-empty={!film && !current?.loaded}>
          {film ? (
            <video
              ref={video}
              src={film}
              className="stage-video"
              playsInline
              controls={false}
              onTimeUpdate={onTime}
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
              onEnded={() => setPlaying(false)}
            />
          ) : (
            <>
              <div
                className="stage-still"
                style={current?.loaded ? { backgroundImage: current.media } : undefined}
              />
              {/* The caption, drawn where the render will burn it in — so what you read here
                  is where you will read it on the phone. */}
              {current?.caption ? <span className="stage-caption">{current.caption}</span> : null}
              {current?.busy ? <span className="working">Working</span> : null}
            </>
          )}
        </div>

        <div className="transport">
          <button
            className="btn sm icon"
            onClick={toggle}
            aria-label={playing ? 'Pause' : 'Play'}
            title={playing ? 'Pause' : 'Play'}
          >
            {playing ? <PauseMark /> : <IconPlay size={16} />}
          </button>
          <span className="tc">
            {clock(starts[at] ?? 0)} / {clock(total)}
          </span>
          <span className="grow" />
          <span className="tc">
            Scene {at + 1} of {scenes.length}
          </span>
        </div>
      </div>

      {/*
       * The scene strip.
       *
       * One cell per scene, not per second, each wider the longer it runs — the one thing a
       * timeline told you that is worth keeping.
       *
       * The proportion is CLAMPED, and that matters more than it sounds. Raw proportion is
       * fine while scenes are seconds apart and falls apart completely when they are not: a
       * 300-second scene beside a 1.6-second one made the short ones slivers a few pixels
       * wide, unreadable and unclickable. A cell is at most four times another, and the
       * duration is written on it for the real number.
       */}
      <div className="strip" role="tablist" aria-label="Scenes">
        {scenes.map((scene, i) => (
          <button
            key={scene.id}
            role="tab"
            aria-selected={i === at}
            className="strip-cell"
            style={{ flexGrow: weightOf(scene.durationMs, scenes) }}
            onClick={() => jump(i)}
            title={scene.line}
          >
            <span
              className="strip-thumb"
              style={scene.loaded ? { backgroundImage: scene.media } : undefined}
            />
            <span className="strip-meta">
              <b>{String(i + 1).padStart(2, '0')}</b>
              <span>{scene.time}</span>
            </span>
            {scene.status === 'final' ? (
              <span className="strip-flag">
                <IconCheck size={11} />
              </span>
            ) : null}
          </button>
        ))}
      </div>

      {current ? <SceneEditor s={s} scene={current} n={at + 1} /> : null}
    </View>
  )
}

/**
 * Edit whatever the strip has selected.
 *
 * The line is the thing worth editing here: it is narrated, it becomes the caption, and its
 * length decides how long the scene runs. The picture prompt sits behind it because changing
 * a picture is a regeneration and costs, while changing a word costs nothing until the next
 * render.
 */
function SceneEditor({ s, scene, n }: { s: Studio; scene: Scene; n: number }) {
  const [line, setLine] = useState(scene.line)

  // Reset when the strip moves, or the field keeps the previous scene's text.
  useEffect(() => setLine(scene.line), [scene.id, scene.line])

  const changed = line.trim() !== scene.line.trim()

  return (
    <div className="editor">
      <div className="spread" style={{ marginBottom: 12 }}>
        <div className="label" style={{ margin: 0 }}>
          Scene {String(n).padStart(2, '0')}
        </div>
        <div className="row">
          <button
            className="btn sm"
            onClick={() => s.regen(scene.id)}
            disabled={scene.busy}
            title="Draw this picture again"
          >
            <IconRefresh size={15} />
            {scene.busy ? 'Working' : 'New picture'}
          </button>
          <button
            className="btn sm"
            onClick={() => {
              s.setOpenScene(scene.id)
              s.go('scene')
            }}
          >
            More
          </button>
        </div>
      </div>

      <label className="field">
        <span>What is said</span>
        <textarea
          rows={2}
          value={line}
          onChange={(e) => setLine(e.target.value)}
          onBlur={() => {
            // Saved on blur rather than per keystroke: every save is a request, and a request
            // per character would fight the storyboard's own polling for the same rows.
            if (changed) s.setLine(scene.id, line.trim())
          }}
        />
      </label>
      <p className="hint">
        {changed
          ? 'Not saved yet — tap outside the box to keep it.'
          : 'This is narrated, and becomes the caption. Its length sets how long the scene runs.'}
      </p>
    </div>
  )
}

/** No pause glyph in the icon set, and one shape does not earn a file. */
function PauseMark() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
      <rect x="4" y="3" width="3" height="10" rx="1" fill="currentColor" />
      <rect x="9" y="3" width="3" height="10" rx="1" fill="currentColor" />
    </svg>
  )
}
