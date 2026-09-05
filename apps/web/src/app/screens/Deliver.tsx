/**
 * Deliver — the last screen.
 *
 * It now shows the finished video. That sounds obvious; it was the whole problem. This screen
 * used to be three rows with a small gradient rectangle beside each one — decoration, not a
 * frame of the video — so a creator who had planned, corrected and paid for a render reached
 * the end of the product without ever seeing it, and the only way to find out what they had
 * made was to download it and open it somewhere else.
 *
 * So: the player is the screen. Shapes are a switch above it rather than a list beside it,
 * because a creator is picking *where they post*, and 9:16 is the detail underneath that —
 * and because switching between them is how you decide whether the square crop actually works,
 * which is a question you can only answer by looking.
 *
 * The post copy keeps its place: it is a different artefact from the captions burnt into the
 * video, and it genuinely differs per destination. On a wide screen it sits beside the player
 * rather than under it, where it was previously below the fold.
 */
import { useEffect, useState } from 'react'

import { assetUrl, downloadUrl } from '../../api/client'
import { usePostCopy } from '../../api/queries'
import { DESTINATIONS, FORMATS } from '../data'
import type { Studio } from '../live'
import { Pipeline } from '../Pipeline'
import { Head, View } from '../Shell'
import { IconCheck, IconDownload, IconRefresh, IconSpark } from '../icons'

/**
 * The shape offered first.
 *
 * Wide rather than tall: it is the one a creator is most likely to want checked, and the only
 * one whose crop can lose the sides of the picture rather than the top and bottom.
 */
const DEFAULT_SHAPE = '16:9'

export function Deliver({ s }: { s: Studio }) {
  const { formats, balance } = s
  const idle = FORMATS.filter((f) => formats[f.id] === 'idle')

  /*
   * Which shape is on screen. Starts on the first one that is actually watchable, so arriving
   * here after a single render does not open on an empty frame with two finished videos one
   * click away.
   */
  const ready = FORMATS.filter((f) => formats[f.id] === 'ready')
  const [shape, setShape] = useState(() => ready[0]?.id ?? DEFAULT_SHAPE)
  //: Whether the creator has chosen a shape themselves. Once they have, nothing moves them.
  const [chose, setChose] = useState(false)
  useEffect(() => {
    /*
     * Only until they pick one.
     *
     * This used to re-run whenever any render landed, so switching to YouTube while it was
     * still rendering bounced the creator straight back to whichever shape had finished — and
     * the download button then offered "tall" while they were looking at the wide one.
     */
    if (!chose && formats[shape] !== 'ready' && ready[0]) setShape(ready[0].id)
  }, [ready.length, chose]) // eslint-disable-line react-hooks/exhaustive-deps

  /** The finished file for an aspect, if there is one: one URL to watch, one to save. */
  const fileFor = (aspect: string) => {
    const row = s.renders.find((r) => r.aspect === aspect && r.status === 'ready' && r.asset_id)
    if (!row?.asset_id) return null
    return {
      watch: assetUrl(row.asset_id),
      //: Named after the project and where it is going, because "lumina-5baadf" is what the
      //: creator finds in their downloads folder otherwise.
      //: Named after where it is going, because "lumina-5baadf" with no extension is what
      //: the creator finds in their downloads folder otherwise.
      save: downloadUrl(row.asset_id, `lumina ${aspect.replace(':', 'x')}`),
    }
  }

  const state = formats[shape] ?? 'idle'
  const file = fileFor(shape)

  /**
   * Which shapes the next Make will produce.
   *
   * A set rather than the one on screen: wanting two of the three is ordinary — a Short and a
   * YouTube cut, but nothing square — and the alternatives were making one at a time or
   * paying for all three.
   *
   * Starts on whatever is being previewed, and drops a shape once it exists, so the button
   * never offers to remake something.
   */
  const [picked, setPicked] = useState<ReadonlySet<string>>(() => new Set([shape]))
  useEffect(() => {
    setPicked((was) => {
      const next = new Set([...was].filter((id) => formats[id] === 'idle'))
      if (!next.size && formats[shape] === 'idle') next.add(shape)
      return next
    })
  }, [formats, shape])

  const toggle = (id: string) =>
    setPicked((was) => {
      const next = new Set(was)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  const meta = FORMATS.find((f) => f.id === shape) ?? FORMATS[0]!
  //: The one ticked shape, when exactly one is — so the button can name what it will make
  //: rather than say "Make 1".
  const only = FORMATS.find((f) => picked.has(f.id)) ?? meta

  return (
    <View
      wide
      action={
        file ? (
          <>
            <div className="why">
              <span>{meta.where}</span>
              {/* What is left, and the way to get it — a count on its own is an observation,
                  not an offer. Ticking the boxes is the other way. */}
              {picked.size ? (
                <button className="linky" onClick={() => s.render([...picked])}>
                  Make {picked.size === 1 ? 'the other one' : `${picked.size} more`} ·{' '}
                  {picked.size * 2} credits
                </button>
              ) : null}
            </div>
            {/* A plain link, not a scripted save: the browser's own download is more reliable
                than anything JavaScript does with a blob, and it works on a long-press too.
                The server sends `content-disposition: attachment`, which is what stops the
                tab navigating away to the video — the `download` attribute cannot, because
                the API is a different origin from the app. */}
            <a className="btn primary block" href={file.save}>
              <IconDownload size={19} />
              Download {meta.tag.toLowerCase()}
            </a>
          </>
        ) : (
          <>
            <div className="why">
              <span>Close the tab if you like — we&rsquo;ll tell you when it&rsquo;s done.</span>
              {idle.length > 1 ? (
                <button
                  className="linky"
                  onClick={() =>
                    setPicked(
                      picked.size === idle.length
                        ? new Set([shape])
                        : new Set(idle.map((f) => f.id)),
                    )
                  }
                >
                  {picked.size === idle.length ? 'Just this one' : `All ${idle.length}`}
                </button>
              ) : null}
            </div>
            {/*
             * What the button makes is what is ticked.
             *
             * It used to make every unmade shape whenever more than one was pending, so a
             * creator who had picked YouTube and wanted only that was charged for three.
             */}
            <button
              className="btn primary block"
              disabled={state === 'working' || !picked.size}
              onClick={() => s.render([...picked])}
            >
              <IconSpark size={19} />
              {state === 'working'
                ? 'Making it…'
                : picked.size === 1
                  ? `Make the ${only.tag.toLowerCase()} one · 2 credits`
                  : `Make ${picked.size} · ${picked.size * 2} credits`}
            </button>
          </>
        )
      }
    >
      <Head
        kicker="Deliver"
        title="Where are you posting it?"
        lede={`${balance} credits left. Each shape costs 2.`}
      />

      {/*
       * The pipeline, as on every other screen.
       *
       * This was the one screen without it, which is the wrong place to leave it off: it is
       * where the last two stages actually run, so it is the only screen where the strip is
       * reporting live work rather than history.
       */}
      <div className="brief-steps">
        <Pipeline used={s.stages} state={s.progress} />
      </div>

      {/*
       * Why nothing happened.
       *
       * The button fired a mutation and never looked at the answer, so a refusal — no credits,
       * a plan with nothing to show — left the screen exactly as it was. A creator pressing a
       * button that visibly does nothing has no way to tell a slow render from a broken one.
       */}
      {s.renderError ? (
        <p className="ed-error" data-inline="true" role="alert">
          {s.renderError}
        </p>
      ) : null}

      <div className="out">
        <div className="out-stage">
          {/*
           * Named by where they post, not by ratio: the creator is choosing a destination.
           * Each carries its own state, so the switch doubles as the render controls — there
           * is no separate list to keep in sync with what is on screen.
           */}
          <div className="out-shapes" role="tablist" aria-label="Shape">
            {FORMATS.map((f) => {
              const st = formats[f.id] ?? 'idle'
              return (
                <div key={f.id} className="out-shape" data-current={f.id === shape}>
                  {/*
                   * Two controls, not one.
                   *
                   * Looking at a shape and choosing to make it are different decisions — you
                   * check the square crop precisely to decide you do *not* want it. Folding
                   * them together would mean previewing a shape signed you up to pay for it.
                   */}
                  <button
                    role="tab"
                    aria-selected={f.id === shape}
                    data-state={st}
                    onClick={() => {
                      setShape(f.id)
                      setChose(true)
                    }}
                  >
                    <span className="out-chip" style={{ width: f.w * 0.42, height: f.h * 0.42 }} />
                    <span>
                      <b>{f.where}</b>
                      <small>
                        {f.tag} · {f.id}
                        {st === 'working' ? ' · rendering' : st === 'idle' ? ' · not made' : ''}
                      </small>
                    </span>
                  </button>

                  {/* Only where there is something to make: a finished shape has nothing to
                      tick, and a box that does nothing is worse than no box. */}
                  {st === 'idle' ? (
                    <label className="out-check" title={`Make the ${f.tag.toLowerCase()} one`}>
                      <input
                        type="checkbox"
                        checked={picked.has(f.id)}
                        onChange={() => toggle(f.id)}
                      />
                      <span aria-hidden="true" />
                    </label>
                  ) : null}
                </div>
              )
            })}
          </div>

          <div className="out-frame" data-shape={shape}>
            {file ? (
              // `key` on the source: without it React keeps the same element across a shape
              // switch and the browser goes on playing the old file.
              <video key={file.watch} src={file.watch} controls playsInline preload="metadata" />
            ) : state === 'working' ? (
              <div className="out-waiting">
                <IconRefresh size={22} />
                <b>Rendering the {meta.tag.toLowerCase()} one</b>
                <small>
                  {/* What is actually happening, from the pipeline. Minutes with no sign of
                      life is the state people close the tab during. */}
                  {s.progress.compose === 'now'
                    ? 'Putting the video together'
                    : s.progress.captions === 'now'
                      ? 'Drawing the captions'
                      : 'Working through it'}
                </small>
              </div>
            ) : (
              /*
               * The empty state, which is most of this screen until something is rendered —
               * so it should say what will fill it rather than being a black rectangle with
               * two words in it. The source video is already on this machine, so it can show
               * the actual first frame, cropped exactly as the export will crop it.
               */
              <div className="out-waiting">
                {s.sourceVideo ? (
                  <video
                    className="out-ghost"
                    src={`${s.sourceVideo}#t=0.1`}
                    preload="metadata"
                    muted
                    playsInline
                    aria-hidden="true"
                  />
                ) : null}
                <b>Not made yet</b>
                <small>
                  {meta.where} · {meta.id} · {s.scenes.length} captions burnt in
                </small>
              </div>
            )}
          </div>
        </div>

        <PostCopy s={s} />
      </div>
    </View>
  )
}

/**
 * The caption that goes in the upload box.
 *
 * A different artefact from the captions burned into the video, and one that genuinely
 * differs per destination: TikTok's copy is searched, so it rewards length and keywords;
 * Reels weights three-second retention over description, so a long caption there is wasted
 * work. Writing one caption and pasting it three places is the mistake this prevents.
 */
function PostCopy({ s }: { s: Studio }) {
  const [dest, setDest] = useState(DESTINATIONS[0]!.id)
  const [copied, setCopied] = useState(false)

  const script = s.scenes.map((x) => x.line).join(' ')
  const copy = usePostCopy(s.project, script, s.language)

  const posts = copy.data ?? []
  const post = posts.find((x) => x.platform === dest) ?? posts[0]
  const meta = DESTINATIONS.find((x) => x.id === dest) ?? DESTINATIONS[0]!

  // Against the platform's own limit, which the API returns alongside the text — the ceiling
  // differs by an order of magnitude between Shorts and TikTok, and a hardcoded number here
  // would be wrong for two of the three.
  const len = post?.text.length ?? 0
  const limit = post?.limit ?? meta.limit
  const [lo, hi] = meta.ideal
  const verdict =
    len < lo ? 'Short for this platform' : len > hi ? 'Longer than ideal' : 'Good length'
  const tone = len < lo || len > hi ? '' : 'good'

  return (
    <div className="out-copy">
      <div className="label" style={{ marginTop: 0 }}>
        Post copy
      </div>
      <div className="card pad">
        <div className="chips" style={{ marginTop: 0 }}>
          {DESTINATIONS.map((x) => (
            <button
              key={x.id}
              className="chip"
              aria-pressed={x.id === dest}
              onClick={() => {
                setDest(x.id)
                setCopied(false)
              }}
              style={
                x.id === dest
                  ? {
                      borderColor: 'var(--accent)',
                      color: 'var(--accent)',
                      background: 'var(--accent-soft)',
                    }
                  : undefined
              }
            >
              {x.name}
            </button>
          ))}
        </div>

        <p style={{ margin: 0, font: '400 15px/1.6 var(--ui)', color: 'var(--ink-2)' }}>
          {copy.isLoading ? 'Writing it…' : (post?.text ?? 'Nothing to write about yet.')}
        </p>

        <div className="chips">
          {(post?.hashtags ?? []).map((h) => (
            <span key={h} className="tag">
              {h}
            </span>
          ))}
        </div>

        <div className="spread" style={{ marginTop: 18 }}>
          <span className={`tag ${tone}`}>
            {len} / {limit} · {verdict}
          </span>
          <button
            className="btn sm"
            disabled={!post}
            onClick={() => {
              if (!post) return
              void navigator.clipboard?.writeText(`${post.text}\n\n${post.hashtags.join(' ')}`)
              setCopied(true)
            }}
          >
            {copied ? <IconCheck size={15} /> : null}
            {copied ? 'Copied' : 'Copy for ' + meta.name}
          </button>
        </div>
      </div>
    </div>
  )
}
