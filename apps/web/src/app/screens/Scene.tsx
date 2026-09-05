/**
 * Scene detail.
 *
 * The storyboard answers "which of these is worth finishing". This screen answers "why is
 * this one wrong, and what do I change" — and they are different jobs, which is why it is a
 * separate view rather than an expanding card.
 *
 * Four things make a scene, and each regenerates independently: the picture, the line that is
 * narrated, how it is read, and the caption drawn over it. Changing one must never invalidate
 * the other three — that is invariant 5 in DEVELOPMENT.md, expressed as a screen.
 */
import { UPGRADE_COST, wordTimings } from '../data'
import type { Studio } from '../live'
import { View } from '../Shell'
import { IconCheck, IconPlay, IconRefresh } from '../icons'

export function SceneDetail({ s }: { s: Studio }) {
  const scene = s.scenes.find((x) => x.id === s.openScene)
  if (!scene) return <View mid>Scene not found.</View>

  const n = s.scenes.indexOf(scene) + 1
  // The same split the renderer uses, so these are the times the caption will actually
  // appear at — not an invention. An earlier version showed `index * 0.42s`.
  const timings = wordTimings(scene.caption, scene.durationMs)
  const final = scene.status === 'final'

  return (
    <View
      action={
        <>
          <div className="why">
            <span>Changing one part re-runs only that part.</span>
            <b>{final ? 'Finished' : `${UPGRADE_COST} credits to finish`}</b>
          </div>
          <div style={{ display: 'flex', gap: 10 }}>
            <button className="btn" onClick={() => s.go('board')}>
              Back to storyboard
            </button>
            <button
              className="btn primary"
              style={{ flex: 1 }}
              onClick={() => {
                s.toggleScene(scene.id)
                s.go('board')
              }}
              disabled={final}
            >
              {final ? 'Already finished' : scene.selected ? 'Selected' : 'Finish this scene'}
            </button>
          </div>
        </>
      }
    >
      <div className="spread" style={{ marginBottom: 22, flexWrap: 'wrap' }}>
        <div>
          <div className="kicker">Scene {String(n).padStart(2, '0')}</div>
          <h1 className="big" style={{ fontSize: 'clamp(24px, 3vw, 34px)' }}>
            {scene.caption}
          </h1>
          <p className="lede" style={{ marginTop: 8 }}>
            {scene.time} · {final ? 'Finished render' : 'Draft preview'}
          </p>
        </div>
        <button className="btn sm" onClick={() => s.go('preview')}>
          <IconPlay size={16} />
          Play scene
        </button>
      </div>

      <div className="detail">
        <div>
          <div
            className="preview"
            style={scene.loaded ? { backgroundImage: scene.media } : undefined}
          >
            {scene.busy ? <span className="working">Working</span> : null}
          </div>
          <button
            className="btn sm block"
            style={{ marginTop: 12 }}
            onClick={() => s.regen(scene.id)}
          >
            <IconRefresh size={15} />
            Try this picture again · free
          </button>
        </div>

        <div>
          <div className="pane">
            <h3>Picture</h3>
            <p className="hint">
              What the image model is asked for. Editing this and regenerating is the fix;
              regenerating without changing anything is a coin flip.
            </p>
            <textarea
              rows={3}
              value={scene.prompt}
              onChange={(e) => s.setPrompt(scene.id, e.target.value)}
            />
          </div>

          <div className="pane">
            <h3>Narration</h3>
            <p className="hint">The line spoken over this scene.</p>
            <textarea
              rows={2}
              value={scene.line}
              onChange={(e) => s.setLine(scene.id, e.target.value)}
            />
          </div>

          <div className="pane">
            <h3>Voice</h3>
            <p className="hint">
              Direction for this line only. Re-reading one line never touches the others.
            </p>
            <label style={{ display: 'block', marginBottom: 14 }}>
              <span
                className="muted"
                style={{ display: 'flex', justifyContent: 'space-between', font: '400 13px/1 var(--ui)', marginBottom: 8 }}
              >
                <span>Pace</span>
                <span style={{ fontFamily: 'var(--mono)' }}>{scene.pace.toFixed(2)}×</span>
              </span>
              {/* Not persisted: there is no column for voice direction, and a control that
                  moves and forgets is worse than one that is plainly not ready. */}
              <input
                className="slider"
                type="range"
                min={0.75}
                max={1.35}
                step={0.05}
                value={scene.pace}
                disabled
                title={NO_VOICE}
                readOnly
              />
            </label>
            {/* Every one of these needs a voice to act on, and there is none until a
                text-to-speech provider is connected. Disabled and saying why, rather than
                three buttons that do nothing when pressed. */}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <button className="mini" disabled title={NO_VOICE}>
                <IconPlay size={13} /> Hear it
              </button>
              <button className="mini" disabled title={NO_VOICE}>
                <IconRefresh size={13} /> Re-read this line
              </button>
              <button className="mini" disabled title={NO_VOICE}>
                Add a pause
              </button>
            </div>
            <p className="hint" style={{ marginTop: 10 }}>
              Narration is not connected yet, so there is nothing to play back.
            </p>
          </div>

          <div className="pane">
            <h3>Caption</h3>
            <p className="hint">
              When each word appears on screen, at this scene's length. These are the times the
              render uses.
            </p>
            {/* Spans, not buttons. Marking a word emphasised needs somewhere to store it and
                a voice to act on it, and there is neither — so this shows when each word
                appears and does not pretend to be a control. */}
            <div className="words">
              {timings.map(([word, at], i) => (
                <span key={`${word}-${i}`} className="word">
                  {word}
                  <small>{(at / 1000).toFixed(1)}s</small>
                </span>
              ))}
            </div>
            {final ? (
              <p
                style={{ margin: '14px 0 0', display: 'flex', alignItems: 'center', gap: 8, font: '400 13.5px/1 var(--ui)', color: 'var(--good)' }}
              >
                <IconCheck size={15} /> Timing confirmed against the finished audio
              </p>
            ) : null}
          </div>
        </div>
      </div>
    </View>
  )
}

/** Shown on everything that needs narration, which nothing produces yet. */
const NO_VOICE = 'Narration is not connected yet'
