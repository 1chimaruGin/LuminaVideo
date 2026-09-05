/**
 * Storyboard — step 3, and the product.
 *
 * The whole screen exists for one repeated judgement: which of these shots is worth paying
 * to finish? That is a judgement about pictures, so the frames are large, portrait (the shape
 * these actually publish in), and the frame itself is the control — tap it to select.
 *
 * Earlier versions put a button inside every row, which turned a visual decision into
 * form-filling. Here the running cost lives in the action bar, next to the button that
 * commits it, so the price is never a surprise.
 *
 * Regenerating one frame visibly touches only that frame; nothing else moves.
 */
import type { Scene } from '../data'
import type { Studio } from '../live'
import { UPGRADE_COST } from '../data'
import { View } from '../Shell'
import { Pipeline } from '../Pipeline'
import { IconCheck, IconClock, IconCoin, IconMore, IconPlay, IconPlus, IconRefresh } from '../icons'

/**
 * Total runtime, read off the last scene's end time.
 *
 * Derived rather than assumed: an earlier version multiplied the scene count by a constant,
 * which is a number that looks real, changes when it should, and is wrong.
 */
/**
 * How long the whole video runs.
 *
 * Summed from the scenes' own durations. An earlier version read the last scene's `time`
 * string, which was right only while that field held a running range — once it became a
 * per-scene length the storyboard quietly reported the last shot's duration as the total.
 */
function totalSeconds(scenes: Scene[]): number {
  return Math.round(scenes.reduce((ms, x) => ms + x.durationMs, 0) / 1000)
}

export function Board({ s }: { s: Studio }) {
  const picked = s.scenes.filter((x) => x.selected)
  const done = s.scenes.filter((x) => x.status === 'final').length
  const cost = picked.length * UPGRADE_COST

  return (
    <View
      action={
        <>
          <div className="why" data-bad={Boolean(s.shortfall)}>
            <span>
              {s.shortfall
                ? `${s.shortfall.short_by} credits short — you have ${s.shortfall.available}`
                : picked.length
                  ? `${picked.length} scene${picked.length > 1 ? 's' : ''} to finish`
                  : 'Tap the scenes worth finishing properly'}
            </span>
            <b>{picked.length ? `${cost} credits` : `${s.balance} left`}</b>
          </div>
          <div style={{ display: 'flex', gap: 10 }}>
            <button
              className="btn"
              onClick={() => {
                s.clearShortfall()
                s.go(s.shortfall ? 'credits' : 'deliver')
              }}
            >
              {s.shortfall ? 'Get credits' : 'Skip to deliver'}
            </button>
            <button
              className="btn primary"
              style={{ flex: 1 }}
              onClick={s.upgrade}
              disabled={!picked.length}
            >
              {picked.length ? `Finish ${picked.length} · ${cost} credits` : 'Nothing selected'}
            </button>
          </div>
        </>
      }
    >
      <div className="spread" style={{ marginBottom: 16, flexWrap: 'wrap' }}>
        <div>
          <div className="kicker">Storyboard</div>
          <h1 className="big" style={{ fontSize: 'clamp(24px, 3.4vw, 36px)' }}>
            {s.project}
          </h1>
          {/* Counts as numerals with icons rather than a sentence — the same three facts,
              legible without reading the interface language. */}
          <div className="row" style={{ marginTop: 12, flexWrap: 'wrap' }}>
            <span className="stat-chip">
              <IconPlay size={13} />
              {s.scenes.length}
            </span>
            <span className="stat-chip hot">
              <IconCheck size={13} />
              {done}
            </span>
            <span className="stat-chip">
              <IconClock size={13} />
              {totalSeconds(s.scenes)}s
            </span>
            <span className="stat-chip">
              <IconCoin size={13} />
              {s.balance}
            </span>
          </div>
        </div>
        <button className="btn sm" onClick={() => s.go('preview')}>
          <IconPlay size={16} />
          Play all
        </button>
      </div>

      {/* Where the work has got to, without a sentence. */}
      <div style={{ marginBottom: 18 }}>
        <Pipeline used={s.stages} state={s.progress} />
      </div>

      <div className="frames">
        {s.scenes.map((scene, i) => (
          <Frame
            key={scene.id}
            n={i + 1}
            scene={scene}
            onPick={() => s.toggleScene(scene.id)}
            onRegen={() => s.regen(scene.id)}
            onOpen={() => {
              s.setOpenScene(scene.id)
              s.go('scene')
            }}
          />
        ))}
        <button className="addframe" onClick={s.addScene}>
          <IconPlus size={22} />
          Add a scene
        </button>
      </div>
    </View>
  )
}

function Frame({
  n,
  scene,
  onPick,
  onRegen,
  onOpen,
}: {
  n: number
  scene: Scene
  onPick: () => void
  onRegen: () => void
  onOpen: () => void
}) {
  const final = scene.status === 'final'
  const cls = [
    'frame',
    !scene.loaded && 'skeleton',
    scene.selected && 'sel',
    final && 'final',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <button
      className={cls}
      style={scene.loaded ? { backgroundImage: scene.media } : undefined}
      onClick={final ? undefined : onPick}
      aria-pressed={scene.selected}
      aria-label={`Scene ${n}: ${scene.line}`}
    >
      <span className="idx">{String(n).padStart(2, '0')}</span>
      <span className="mark">
        <IconCheck size={14} />
      </span>

      <span className="tools">
        <span
          role="button"
          tabIndex={0}
          className="tool"
          title="Try this scene again"
          onClick={(e) => {
            e.stopPropagation()
            onRegen()
          }}
        >
          <IconRefresh size={15} />
        </span>
        <span
          role="button"
          tabIndex={0}
          className="tool"
          title="Open this scene"
          onClick={(e) => {
            e.stopPropagation()
            onOpen()
          }}
        >
          <IconMore size={15} />
        </span>
      </span>

      <span className="veil">
        <p className="line">{scene.line}</p>
        <span className="tc">
          {scene.time} · {final ? 'FINAL' : `${UPGRADE_COST} CR`}
        </span>
      </span>

      {scene.busy ? <span className="working">Working</span> : null}
    </button>
  )
}
