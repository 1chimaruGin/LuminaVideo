/**
 * Moments — the Clip lane.
 *
 * For a long upload the plan is not written, it is *found*: transcribe, rank the moments,
 * cut. So this replaces the Script step for that recipe rather than adding a step to it.
 *
 * The score is shown because the ranking is a guess, and a creator who disagrees with it
 * needs to see that it is a guess rather than a verdict. Nothing is discarded on the strength
 * of it either — the moments below the cut are still listed.
 */
import type { Studio } from '../live'
import { Head, View } from '../Shell'
import { IconPlay, IconSpark } from '../icons'

/** `mm:ss`, because a timestamp in milliseconds is not a place in a video to anyone. */
function at(ms: number): string {
  const total = Math.round(ms / 1000)
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

export function Clips({ s, onNext }: { s: Studio; onNext: () => void }) {
  const moments = s.moments
  const found = moments.length

  return (
    <View
      mid
      action={
        <>
          <div className="why">
            <span>
              {found
                ? `${found} moment${found > 1 ? 's' : ''} worth posting.`
                : 'Nothing found yet.'}
            </span>
            <b>{s.scenes.length} scenes</b>
          </div>
          <button className="btn primary block" onClick={onNext} disabled={!found}>
            <IconSpark size={19} />
            {found ? `Cut ${found === 1 ? 'this one' : `these ${found}`}` : 'Nothing to cut'}
          </button>
        </>
      }
    >
      <Head
        kicker="Moments"
        title="The parts worth posting"
        lede="Found by listening to the whole thing. The score is a guess — trust your own ear over it."
      />

      <div className="card">
        {moments.map((m) => (
          <div className="clip" key={`${m.start_ms}-${m.line}`}>
            <span className={`score${m.score >= 85 ? ' hot' : ''}`}>{m.score}</span>
            <span style={{ minWidth: 0 }}>
              <b style={{ display: 'block', font: '450 15px/1.45 var(--ui)' }}>{m.line}</b>
              <span className="at">
                {at(m.start_ms)} · {Math.round((m.end_ms - m.start_ms) / 1000)}s
              </span>
            </span>
            <button
              className="btn sm icon"
              title="Watch this moment"
              onClick={() => s.go('preview')}
            >
              <IconPlay size={15} />
            </button>
          </div>
        ))}
        {!found ? (
          <div className="rowline">
            <span className="muted">
              This lane starts from a long video. Upload one and the moments show up here.
            </span>
          </div>
        ) : null}
      </div>
    </View>
  )
}
