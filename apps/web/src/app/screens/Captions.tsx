/**
 * The subtitle editor — the Subtitle lane's whole screen.
 *
 * A working surface, not a document, and laid out like one: it fills the window, the video is
 * the largest thing on it, and the only thing that scrolls is the list of lines.
 *
 * The version before this was built as an article — a display heading, a paragraph of
 * explanation, a 380px video stranded beside a column of very tall rows, and a third of a wide
 * monitor left empty. Everything the creator was meant to be checking was the smallest element
 * on screen, five cues fitted at a time, and the timecodes were raw seconds, so a cue three
 * minutes in read `202.5` and had to be typed that way.
 *
 * Two things here go past what the established tools do, and both fall out of work the rest of
 * the product had already done:
 *
 *   - **Reading speed in the creator's actual language.** The warning threshold comes from the
 *     language pack, and comfortable reading speed varies about threefold across the six we
 *     ship. Tools that hardcode ~17 characters per second are hardcoding English, and would
 *     tell a Burmese creator that a caption they cannot possibly read is fine.
 *   - **Overlap is impossible, not warned about.** These captions burn into the picture, so
 *     two at once means one drawn on top of the other. Editing a cue's end clamps against its
 *     neighbour rather than letting the creator author a frame that cannot be rendered.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { fetchDubPreview } from '../../api/client'
import { useFixTiming, useLanguages } from '../../api/queries'
import { CAPTION_STYLES, RECIPES } from '../data'
import type { Studio } from '../live'
import { Pipeline } from '../Pipeline'
import { Voices } from '../Voices'
import { View } from '../Shell'
import { IconClose, IconInfo, IconPlay, IconSpark } from '../icons'

/**
 * Where the video is going, and the shape that implies.
 *
 * Named by destination, not by ratio. "9:16" is a fact about the file; "TikTok" is the
 * decision the creator is actually making, and most people posting a Short could not tell you
 * which number theirs is. The ratio stays visible underneath for the people who do care.
 *
 * Vertical first because it is what short-form is, and because it is the crop that loses the
 * most: a 16:9 upload exported to 9:16 keeps a third of its width, so a caption that fitted
 * comfortably across the source wraps to four lines in the export. Seeing that before paying
 * for the render is the point of previewing at all.
 */
const SHAPES = [
  { id: '9:16', name: 'TikTok', also: 'Reels, Shorts, FB Reels' },
  { id: '1:1', name: 'Instagram', also: 'Feed posts, Facebook' },
  { id: '16:9', name: 'YouTube', also: 'Telegram, X, LinkedIn' },
]

/** A cue as this screen thinks about it: text, and the window it is on screen for. */
type Cue = {
  id: string
  text: string
  startMs: number
  endMs: number
  /** The same line in the other burnt-in languages. */
  translations: Record<string, string>
  /**
   * Which language this line is in, which is not always the project's source language: a
   * file can change language part way through, and the project records only the commonest.
   */
  spokenLanguage: string | null
}

export function Captions({ s }: { s: Studio }) {
  const video = useRef<HTMLVideoElement>(null)
  const [at, setAt] = useState(0)
  const [playing, setPlaying] = useState(false)
  /*
   * Which export shape the preview is showing.
   *
   * Not the source's shape. Captions are sized and placed against the frame they burn into,
   * so previewing a 16:9 upload in a 16:9 box shows a caption that is nothing like the one a
   * TikTok export produces — different crop, different proportion, different wrap. The whole
   * complaint about the export "not matching" starts here.
   */
  //: YouTube first, matching the Deliver screen. It is the shape whose crop can lose the
  //: sides of the picture rather than the top and bottom, so it is the one worth checking
  //: before the others.
  const [shape, setShape] = useState('16:9')
  //: The pack's line spacing, so a wrapped caption breaks here exactly as it does in the
  //: render. Burmese needs 1.75 where English needs 1.4.
  const langs = useLanguages()
  const leading = langs.data?.find((l) => l.code === s.planLanguage)?.line_height ?? 1.4
  //: Still an array, and the cue rows still render one box per entry, so restoring the
  //: picker is putting the control back rather than rebuilding the screen around it.
  const tracks = s.captionLanguages.length ? s.captionLanguages : [s.planLanguage]

  /**
   * Whether to show the source line under each caption at all.
   *
   * A whole-file switch, because that is the only thing it can honestly be: on a file that
   * changed language part way through there is no single "compare against" language. Which
   * *language* a particular line is shown in is decided on that line — see the tag in `Row`.
   */
  const showSource = Boolean(s.sourceLanguage && s.sourceLanguage !== s.planLanguage)
  const [reference, setReference] = useState(true)

  /**
   * Lines the creator has asked to see in English.
   *
   * Per line, not per file. Most of this track is already English and needs no switch; six
   * lines are Japanese and are the only ones worth flipping. A global control would translate
   * English into English for the other thirty-five.
   */
  const [inEnglish, setInEnglish] = useState<ReadonlySet<string>>(() => new Set())
  const flip = useCallback(
    (id: string) => {
      setInEnglish((was) => {
        const next = new Set(was)
        if (next.has(id)) next.delete(id)
        else next.add(id)
        return next
      })
      //: Nothing stored yet, so ask — translated for reading, never burnt into the video.
      if (!s.scenes.some((x) => x.translations?.en)) s.readIn('en')
    },
    [s],
  )

  /*
   * The plan's scenes *are* the cues. There is no second transcript document — `transcribe`
   * produced these lines, `plan` wrote one scene per line, and the caption stage reads them
   * back. A local copy would give the screen something to disagree with.
   */
  /*
   * Where each line sits, and the two lanes disagree about it.
   *
   * A lane that keeps the creator's footage has a timestamp per scene — the moment it was
   * said — and the cue belongs there. A lane that *writes* its video has none: the scenes are
   * laid end to end, so a line starts where the one before it ended.
   *
   * Falling back to zero for the second case put every cue at 0:00.0 — thirty lines all
   * claiming to start at the beginning, a timeline with one mark on it, and a preview that
   * drew every caption at once on top of itself.
   */
  const cues: Cue[] = useMemo(() => {
    let at = 0
    return s.scenes.map((scene) => {
      const start = scene.sourceStartMs ?? at
      at = start + scene.durationMs
      return {
        id: scene.id,
        text: scene.line,
        startMs: start,
        endMs: start + scene.durationMs,
        translations: scene.translations,
        spokenLanguage: scene.spokenLanguage,
      }
    })
  }, [s.scenes],
  )

  const duration = s.durationMs || cues.at(-1)?.endMs || 0
  const live = cues.find((c) => at >= c.startMs && at < c.endMs) ?? null

  /*
   * Which lines are on screen too briefly to be read.
   *
   * Measured against the *target* language's reading rate, which is the whole point: a cue
   * keeps the window of the speech it was translated from, and English sustains about 17
   * characters a second where Burmese sustains 9. So a faithful translation of a line that
   * fitted comfortably in English routinely needs twice the time it inherited — and nothing
   * about it looks broken, because the words are right and the timing is still in sync.
   *
   * The rate comes from the server with the language list rather than a constant here; it
   * varies about threefold across the languages Lumina renders.
   */
  const cps = langs.data?.find((l) => l.code === s.planLanguage)?.cps ?? 17
  const tooFast = useMemo(
    () =>
      new Set(
        cues
          .filter((c) => rushedAt(burntIn(c, tracks[0]), cps, c.endMs - c.startMs))
          .map((c) => c.id),
      ),
    [cues, cps, tracks],
  )
  const fixTiming = useFixTiming(s.projectId ?? '')

  const dubbing = s.recipe === 'dub'
  const narrating = s.recipe === 'narrate'
  /** The scene whose line is on screen — its picture is what the frame should show. */
  const beat = useMemo(() => s.scenes.find((x) => x.id === live?.id) ?? null, [s.scenes, live])
  const plate = useRef<HTMLVideoElement>(null)

  //: A clip on a beat follows the preview's own clock rather than running on its own — the
  //: beat is as long as its line takes to say, and the render cuts the clip to exactly that.
  useEffect(() => {
    const el = plate.current
    if (!el) return
    if (playing) void el.play().catch(() => {})
    else el.pause()
  }, [playing, beat?.pictureUrl])
  //: Hearing the dub instead of the original. Off by default: the creator is checking the
  //: words against what was actually said for most of this screen's life, and that needs the
  //: original audio.
  const [hearDub, setHearDub] = useState(false)
  const speech = useRef<HTMLAudioElement>(null)
  const dub = useDubTrack(video, speech, {
    //: Narrate speaks too — it was left out when the row below was opened up to it, so the
    //: button toggled, the label changed, and nothing was ever fetched or played.
    on: (dubbing || narrating) && hearDub,
    projectId: s.projectId,
    voice: s.voiceId,
  })

  const seek = useCallback(
    (ms: number) => {
      const el = video.current
      if (el) el.currentTime = Math.max(0, ms / 1000)
      //: Scrubbing has to move the voice as well, or the picture jumps and the speech
      //: carries on from where it was.
      const speech = dub.ref.current
      if (speech && dub.ready) speech.currentTime = Math.max(0, ms / 1000)
      setAt(ms)
    },
    [dub.ref, dub.ready],
  )

  const toggle = useCallback(() => {
    const el = video.current
    if (el) {
      if (el.paused) void el.play()
      else el.pause()
      return
    }
    //: No video on a narrated project — the pictures are stills. If the narration is loaded
    //: it is the thing being played, and the pictures follow it; otherwise the bare clock
    //: below runs so the preview still moves before any voice has been made.
    const speech = dub.ref.current
    if (speech && dub.ready) {
      if (speech.paused) void speech.play().catch(() => {})
      else speech.pause()
      setPlaying(speech.paused)
      return
    }
    setPlaying((was) => !was)
  }, [dub.ref, dub.ready])

  /*
   * The clock, when there is no video to be one.
   *
   * Every other lane inherits time from the footage it is previewing. A narrated video has no
   * footage yet — it is a script, a voice and a pile of pictures — so pressing play advanced
   * nothing and the preview sat on the first line forever.
   *
   * Real time, sampled, rather than a frame counter: the beats come from measured speech, so
   * the preview should drift the same way the render will.
   */
  /*
   * The narration drives everything when it is playing.
   *
   * One clock or the pictures and the voice drift apart — they were two independent timers,
   * so a line could be on screen while a different one was being spoken. The audio is the
   * authority because it is the thing whose length is real: a beat lasts exactly as long as
   * its line took to say.
   */
  useEffect(() => {
    const speech = dub.ref.current
    if (!speech || !dub.ready) return
    const follow = () => setAt(Math.round(speech.currentTime * 1000))
    const started = () => setPlaying(true)
    const stopped = () => setPlaying(false)
    speech.addEventListener('timeupdate', follow)
    speech.addEventListener('play', started)
    speech.addEventListener('pause', stopped)
    speech.addEventListener('ended', stopped)
    return () => {
      speech.removeEventListener('timeupdate', follow)
      speech.removeEventListener('play', started)
      speech.removeEventListener('pause', stopped)
      speech.removeEventListener('ended', stopped)
    }
  }, [dub.ref, dub.ready])

  useEffect(() => {
    //: Only when nothing else is keeping time — a video, or a narration that has loaded.
    if (!playing || video.current || (dub.ref.current && dub.ready)) return
    const started = Date.now() - at
    const tick = setInterval(() => {
      const now = Date.now() - started
      if (now >= duration) {
        setAt(duration)
        setPlaying(false)
        return
      }
      setAt(now)
    }, 100)
    return () => clearInterval(tick)
  }, [playing, duration, at, dub.ref, dub.ready])

  // Space plays and pauses, the arrows step a second — the shortcuts every editor has, and
  // the reason a captioner can work without reaching for the mouse. Ignored while typing, or
  // space would insert a character instead of pausing.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null
      if (el && (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT')) return
      if (e.code === 'Space') {
        e.preventDefault()
        toggle()
      } else if (e.code === 'ArrowRight') seek(Math.min(duration, at + 1000))
      else if (e.code === 'ArrowLeft') seek(Math.max(0, at - 1000))
      /*
       * Mark in and out while listening — the way captioning is actually done. You hear the
       * line begin, you press `[`. Both act on the cue currently on screen, or on the next
       * one when the playhead is in a gap, so marking the start of a line you can hear
       * approaching works without selecting it first.
       */
      else if (e.key === '[' || e.key === ']') {
        const target = live ?? cues.find((c) => c.startMs >= at)
        if (!target) return
        e.preventDefault()
        const i = cues.indexOf(target)
        if (e.key === '[') {
          const floor = cues[i - 1]?.endMs ?? 0
          const start = Math.min(Math.max(at, floor), target.endMs - 200)
          s.setCueTime(target.id, start, target.endMs - start)
        } else {
          const ceiling = cues[i + 1]?.startMs ?? duration
          const end = Math.max(Math.min(at, ceiling), target.startMs + 200)
          s.setCueTime(target.id, target.startMs, end - target.startMs)
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [toggle, seek, at, duration, cues, live, s])

  return (
    <View
      full
      action={
        <>
          <div className="why">
            {/*
             * What this screen is about to do, in the words of the lane it is in.
             *
             * Dub and Subtitle share this editor because they share the work — read back
             * what was heard, fix it, commit — but they do not share the outcome. Telling a
             * creator dubbing their video into Burmese that "only the captions are drawn on"
             * describes a different product from the one they are buying.
             */}
            <span>{narrating ? NARRATE_WHY : dubbing ? DUB_WHY : SUBTITLE_WHY}</span>
            <b>1 credit</b>
          </div>
          <button
            className="btn primary block"
            onClick={() => s.go('deliver')}
            disabled={(dubbing || narrating) && !s.voiceId}
          >
            <IconSpark size={19} />
            {dubbing || narrating
              ? s.voiceId
                ? narrating
                  ? 'Make the video'
                  : 'Make the dub'
                : 'Choose a voice first'
              : 'Burn in the captions'}
          </button>
        </>
      }
    >
        <div className="ed">
          {/*
           * Two rows, because the header does two jobs.
           *
           * What this is and how far along it is belong together and change rarely; the
           * controls are used constantly and need room. Sharing one row made a strip of
           * eleven controls that wrapped, and putting the pipeline on a line of its own left
           * it stranded at a third of the page width beside empty space.
           */}
          <header className="ed-top">
            <span className="ed-what">
              {/* The lane's own name, from the one table that has it — so a lane added
                  later is not a third special case here. */}
              <b>{RECIPES.find((r) => r.id === s.recipe)?.name ?? 'Subtitles'}</b>
              <span>
                {cues.length} lines · {clock(duration)}
              </span>
            </span>
            <span className="ed-flow">
              <Pipeline used={s.stages} state={s.progress} />
            </span>
          </header>

          {/*
           * The dub itself: who says it, and hearing them say it over the footage.
           *
           * On its own row above the caption controls rather than among them, because it is
           * a different kind of decision — those adjust how the words *look*, this decides
           * what the video *sounds* like, which is the whole product on this lane.
           */}
          {dubbing || narrating ? (
            <div className="ed-dub">
              {/*
               * `planLanguage`, not `language`. The second is the *channel's* language — what
               * this creator usually works in — and passing it here asked for voices for the
               * wrong language and previewed them reading the wrong one: a creator dubbing
               * into Burmese pressed play on a voice and heard an English sentence.
               */}
              <Voices projectId={s.projectId ?? ''} language={s.planLanguage} picked={s.voiceId} />
              {/*
               * Which language is burnt onto a dub.
               *
               * Not the same question as which language it is spoken in, and a dub is where
               * they come apart: the audio is Burmese, and the creator may still want English
               * on screen for viewers who have it muted — or their own language, for viewers
               * reading along. Empty means the project's own, which is why the current value
               * falls back to it.
               */}
              <label className="lang ed-subs">
                <span>Subtitled in</span>
                <select
                  value={tracks[0] ?? s.planLanguage}
                  onChange={(e) => {
                    const next = e.target.value
                    for (const was of tracks) if (was !== next) s.setTrack(was, false)
                    s.setTrack(next, true)
                  }}
                >
                  {(langs.data ?? [])
                    .filter((l) => l.can_render)
                    .map((l) => (
                      <option key={l.code} value={l.code}>
                        {l.code === l.english.toLowerCase() ? l.name : `${l.name} · ${l.english}`}
                      </option>
                    ))}
                </select>
              </label>

              <div className="ed-hear">
                <button
                  type="button"
                  className="chip"
                  aria-pressed={hearDub}
                  disabled={!s.voiceId}
                  title={
                    s.voiceId
                      ? 'Play the video with the dub instead of the original audio'
                      : 'Pick a voice first'
                  }
                  onClick={() => setHearDub((was) => !was)}
                >
                  {dub.working ? (
                    <>
                      <span className="spinner" aria-hidden="true" />
                      Preparing
                    </>
                  ) : hearDub ? (
                    narrating ? 'Hearing it' : 'Hearing the dub'
                  ) : narrating ? (
                    'Hear it'
                  ) : (
                    'Hear the dub'
                  )}
                </button>
                <small>
                  {dub.problem
                    ? dub.problem
                    : dub.working
                      ? `Speaking ${cues.length} ${cues.length === 1 ? 'line' : 'lines'}… ${dub.elapsed}s`
                      : hearDub
                        ? 'The original is muted. This is the whole video in that voice.'
                        : narrating ? 'Reads your script aloud in the voice you picked.' : 'Plays the whole video in the voice you picked.'}
                </small>
              </div>
            </div>
          ) : null}

          <div className="ed-bar">
            {/* Where it is going. The preview crops exactly as ffmpeg does for that shape. */}
            <span className="ed-shapes" role="group" aria-label="Where you are posting it">
              {SHAPES.map((x) => (
                <button
                  key={x.id}
                  aria-pressed={shape === x.id}
                  onClick={() => setShape(x.id)}
                  title={`${x.name} — also ${x.also} · ${x.id}`}
                >
                  <b>{x.name}</b>
                  <small>{x.id}</small>
                </button>
              ))}
            </span>

            {/*
             * Reading pace. Shown only when something is actually wrong with it, because a
             * control that says "0 lines too fast" every time is a control people stop
             * reading — and this one needs to be noticed the once it matters.
             */}
            {tooFast.size ? (
              <span className="ed-pace">
                <b>{tooFast.size}</b>
                <small>
                  {tooFast.size === 1 ? 'line is' : 'lines are'} too fast to read
                </small>
                <button
                  className="chip"
                  disabled={fixTiming.isPending}
                  title="Widen each caption into the silence around it, and shorten the ones still too long"
                  onClick={() => fixTiming.mutate(true)}
                >
                  {fixTiming.isPending ? 'Fixing…' : 'Fix the pace'}
                </button>
              </span>
            ) : null}

            <span className="ed-size" role="group" aria-label="Caption size">
              <button
                onClick={() => s.setCaptionScale(s.captionScale - 0.1)}
                disabled={s.captionScale <= 0.6}
                aria-label="Smaller captions"
              >
                A
              </button>
              <button
                onClick={() => s.setCaptionScale(s.captionScale + 0.1)}
                disabled={s.captionScale >= 1.8}
                aria-label="Bigger captions"
              >
                A
              </button>
            </span>

            <span className="ed-styles" role="group" aria-label="Caption style">
              {CAPTION_STYLES.map((c) => (
                <button
                  key={c.id}
                  aria-pressed={s.captionStyle === c.id}
                  onClick={() => s.setCaptionStyle(c.id)}
                  title={c.name}
                >
                  {/* Drawn in the style it selects. A list of names cannot be compared. */}
                  <em style={styleOf(c.id, true)}>Aa</em>
                </button>
              ))}
            </span>

            {/*
             * How the caption moves, which is a separate decision from how it looks.
             *
             * Word-by-word is the short-form convention and what this shipped with — but it
             * is a distraction on anything a viewer reads rather than skims, and there was no
             * way to turn it off. Still is also the cheaper render: one image per line
             * instead of one per word.
             */}
            <span className="ed-motion" role="group" aria-label="Caption motion">
              <button
                className="chip"
                aria-pressed={s.captionKaraoke}
                onClick={() => s.setCaptionKaraoke(true)}
                title="Each word lights up as it is spoken"
              >
                Word by word
              </button>
              <button
                className="chip"
                aria-pressed={!s.captionKaraoke}
                onClick={() => s.setCaptionKaraoke(false)}
                title="The whole line stays still"
              >
                Still
              </button>
            </span>

            {/*
             * The language-track picker lived here: a chip per language, burning a second set
             * of captions in under the first. It works — `POST /projects/{id}/tracks/{lang}`
             * and the stacked rasterizer are still there — but it was the loudest control on a
             * toolbar most people will never translate anything with, and translation wants to
             * be a deliberate act rather than a row of switches. Hidden until it comes back in
             * a shape that earns the space.
             */}
            {/*
             * What to show under each caption for checking.
             *
             * Only on a translation, and off by default: most of the time the caption is the
             * only text that matters, and a permanent second line halves how many cues fit on
             * screen. It earns its space the moment you are checking a translation you cannot
             * read back — which is the whole reason it exists.
             */}
            {/*
             * Whether the source line shows at all — a whole-file switch, because that is the
             * only honest one. *Which language* each line shows in is decided on the line
             * itself: see the tag in `Row`.
             */}
            {showSource ? (
              <span className="ref-pick">
                <button
                  type="button"
                  aria-pressed={reference}
                  onClick={() => setReference((v) => !v)}
                >
                  Show original
                </button>
              </span>
            ) : null}

            <Glossary terms={s.glossary} onChange={s.setGlossary} />

            <span className="ed-shift" role="group" aria-label="Move every line">
              <small>Shift all</small>
              {[-500, -100, 100, 500].map((by) => (
                <button key={by} onClick={() => s.shiftCues(by)}>
                  {by > 0 ? '+' : '−'}
                  {Math.abs(by) / 1000}s
                </button>
              ))}
            </span>
          </div>

          {/* The server's own words. The usual reason is one an operator has to fix, and the
              creator needs to be told rather than left clicking a button that does nothing. */}
          {s.trackError ? (
            <p className="ed-error" role="alert">
              {s.trackError}
            </p>
          ) : null}

          <div className="ed-main">
            <div className="ed-stage">
              {/*
                * Sizes the frame to fit the row in *both* axes.
                *
                * The frame carries an aspect ratio and a height budget, and those two
                * fought: `max-height` clamped the height while nothing clamped the width,
                * so the box kept its width and stopped being the shape it claims to
                * export — a 9:16 frame measured 1.4:1, landscape, on a short window. The
                * video inside still sized itself against the *unclamped* height and hung
                * up to 235px past the bottom of its own box, where `overflow: hidden` cut
                * the picture off above the play bar.
                *
                * A size container is what lets the frame see the height it was given, so
                * one dimension can be derived from the other instead of the two being
                * declared independently and disagreeing.
                */}
              <div className="ed-fit">
                <div
                  className="ed-video"
                  data-shape={shape}
                  // `cqmin` is 1% of the frame's smaller side, which is exactly what the
                  // rasterizer sizes from — so the caption here is the caption that burns in,
                  // at any preview size, with no measuring in JavaScript.
                  style={{
                    ['--cap' as string]: s.captionScale,
                    ['--lead' as string]: leading,
                  }}
                >
                  {/*
                   * What the frame shows, and it is not the same question on every lane.
                   *
                   * A lane that keeps the creator's footage previews that footage: the video is
                   * the subject and the clock. A narrated video has no such thing — it is a
                   * sequence of pictures, one per line, and the source is only the first of
                   * them. Playing it linearly showed the whole clip running under captions cut
                   * from a script, which is not the video that will be rendered.
                   */}
                  {/* The narration. Rendered rather than constructed, so it belongs to the
                      tree that owns it and its state can be seen from outside this file. */}
                  <audio ref={speech} hidden />

                  {narrating ? (
                    <div className="ed-plate">
                      {/*
                       * A still is drawn; a clip is played.
                       *
                       * Both arrive as `/assets/<uuid>` with no extension, so the kind comes
                       * from the server. Handing an mp4 to `background-image` renders a black
                       * frame and reports nothing — which is exactly what a creator saw after
                       * narrating over a video clip.
                       */}
                      {beat?.pictureIsClip && beat.pictureUrl ? (
                        <video
                          key={beat.pictureUrl}
                          ref={plate}
                          src={beat.pictureUrl}
                          muted
                          loop
                          playsInline
                        />
                      ) : beat?.pictureUrl ? (
                        <span
                          className="ed-plate-still"
                          style={{ backgroundImage: `url("${beat.pictureUrl}")` }}
                        />
                      ) : (
                        <span className="ed-novideo">
                          <IconInfo size={20} />
                          <span>No picture on this line yet.</span>
                        </span>
                      )}
                    </div>
                  ) : s.sourceVideo ? (
                    <video
                      ref={video}
                      src={s.sourceVideo}
                      playsInline
                      onTimeUpdate={(e) => setAt(Math.round(e.currentTarget.currentTime * 1000))}
                      onPlay={() => setPlaying(true)}
                      onPause={() => setPlaying(false)}
                    />
                  ) : (
                    <div className="ed-novideo">
                      <IconInfo size={20} />
                      <span>The video is still uploading.</span>
                    </div>
                  )}
                  {/*
                   * Every burnt-in language, drawn where it will burn in — and draggable.
                   *
                   * Caption height was hardcoded at 16% from the bottom, which is right for a
                   * talking head and wrong the moment the subject is in the lower third. Dragging
                   * it here is the only way to judge it, because whether a caption is in the way
                   * is a fact about *this* footage.
                   */}
                  <Placement
                    bottom={s.captionBottom}
                    onMove={s.setCaptionBottom}
                    empty={!live}
                    hint={cues.length > 0}
                  >
                    {live
                      ? tracks.map((code, i) => (
                          <span
                            key={code}
                            className="ed-caption"
                            data-sub={i > 0}
                            style={styleOf(s.captionStyle)}
                          >
                            {lineIn(live, code, s.sourceLanguage)}
                          </span>
                        ))
                      : null}
                  </Placement>
                </div>
              </div>

              <div className="ed-transport">
                <button className="ed-play" onClick={toggle} aria-label={playing ? 'Pause' : 'Play'}>
                  {playing ? <Pause /> : <IconPlay size={20} />}
                </button>

                <Timeline
                  cues={cues}
                  at={at}
                  duration={duration}
                  live={live?.id ?? null}
                  onSeek={seek}
                  onTime={(id, startMs, endMs) => s.setCueTime(id, startMs, endMs - startMs)}
                />

                <span className="ed-clock">
                  {clock(at)} <i>/</i> {clock(duration)}
                </span>
              </div>
            </div>

            <div className="ed-list">
              <div className="ed-rows">
                <div className="ed-head" aria-hidden="true">
                  <span>#</span>
                  <span>Line</span>
                  <span>
                    <i>In</i>
                    <i>Out</i>
                    <u>Length</u>
                  </span>
                </div>
                {/*
                  * A line can be added before the first cue too — a title, or something said
                  * before the first thing the engine heard.
                  */}
                <Insert onAdd={() => s.insertCue(-1)} />
                {cues.map((cue, i) => (
                  <Row
                    key={cue.id}
                    cue={cue}
                    index={i}
                    spoken={s.sourceLanguage}
                    tracks={tracks}
                    reference={reference}
                    inEnglish={inEnglish.has(cue.id)}
                    cps={cps}
                    burnt={burntIn(cue, tracks[0])}
                    onFlip={() => flip(cue.id)}
                    live={live?.id === cue.id}
                    // A cue may not run past the one after it: burnt-in captions cannot overlap,
                    // so the second would be drawn on top of the first.
                    ceiling={cues[i + 1]?.startMs ?? duration}
                    floor={cues[i - 1]?.endMs ?? 0}
                    onSeek={() => seek(cue.startMs)}
                    glossary={s.glossary}
                    onText={(text) => s.setCaptionText(cue.id, text)}
                    onTime={(startMs, endMs) => s.setCueTime(cue.id, startMs, endMs - startMs)}
                    onKeep={(term) => s.setGlossary([...s.glossary, term])}
                    onRemove={() => s.removeScene(cue.id)}
                    onInsert={() => s.insertCue(i)}
                  />
                ))}
              </div>
            </div>
          </div>
        </div>
      </View>
    )
  }

/** One cue: when it shows, what it says, and whether it can be read in the time given. */
function Insert({ onAdd }: { onAdd: () => void }) {
  /*
   * "Put a line here."
   *
   * A thin strip between two cues rather than one button at the end of the list, because the
   * position is the whole request: a line the engine missed belongs at a particular moment,
   * and adding it at the end and dragging it into place is the same job done twice.
   *
   * Quiet until hovered. Forty of these down the side of a caption list would otherwise be
   * the loudest thing on the screen.
   */
  return (
    <button className="ed-insert" onClick={onAdd} title="Add a line here">
      <span>+ line</span>
    </button>
  )
}

function Row({
  cue,
  index,
  spoken,
  tracks,
  reference,
  inEnglish,
  cps,
  burnt,
  onFlip,
  live,
  ceiling,
  floor,
  glossary,
  onSeek,
  onText,
  onTime,
  onKeep,
  onRemove,
  onInsert,
}: {
  cue: Cue
  index: number
  /** What was said, when that differs from what is written. `null` when not a translation. */
  spoken: string | null
  tracks: string[]
  /** Whether to show the source line underneath for checking. */
  reference: boolean
  /** Whether this line's source is shown in English rather than as it was spoken. */
  inEnglish: boolean
  /** Characters per second this language sustains, from the server — see `readableMs`. */
  cps: number
  /** The text that will actually be burnt in, which is what has to be readable. */
  burnt: string
  /** Switch this one line between as-spoken and English. */
  onFlip: () => void
  live: boolean
  ceiling: number
  floor: number
  glossary: string[]
  onSeek: () => void
  onText: (text: string) => void
  onTime: (startMs: number, endMs: number) => void
  onKeep: (term: string) => void
  /** Throw this line away. */
  onRemove: () => void
  /** Add a line in the gap after this one. */
  onInsert: () => void
}) {
  const row = useRef<HTMLDivElement>(null)
  /*
   * Whatever is selected inside this row's primary line.
   *
   * Selecting the word is how a creator notices the problem in the first place — they are
   * reading the line, they hit "Computer", and they think "not that one". Offering it right
   * there beats making them retype it into a panel.
   */
  const [picked, setPicked] = useState('')
  const catchSelection = (e: React.SyntheticEvent<HTMLTextAreaElement>) => {
    const el = e.currentTarget
    const term = el.value.slice(el.selectionStart, el.selectionEnd).trim()
    setPicked(term.length > 1 && term.length < 60 ? term : '')
  }
  const shown = cue.endMs - cue.startMs

  /*
   * Comfortable reading time for this text, in this language.
   *
   * From the language pack's characters-per-second, which ranges from 7.5 for Chinese to 17
   * for English. A fixed threshold — what most tools use — is an English threshold, and it
   * tells a Chinese or Burmese creator that a caption they cannot possibly read is fine.
   */
  const rushed = burnt.trim().length > 0 && rushedAt(burnt, cps, shown)

  // Follow playback. Without this the list and the video drift apart the moment the track is
  // longer than the window, and the creator is hunting for the line they can hear.
  useEffect(() => {
    if (live) row.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [live])

  return (
    <>
      <div className="ed-cue" data-live={live} data-rushed={rushed} ref={row}>
        <button className="ed-n" onClick={onSeek} title="Jump the video here">
          {String(index + 1).padStart(2, '0')}
        </button>

        {/*
          * Throwing a line away.
          *
          * Needed as much as adding one: a speech engine writes lines over music that nobody
          * said, and until now there was no way to remove them from the editor at all.
          */}
        <button
          className="ed-drop"
          onClick={onRemove}
          title="Remove this line"
          aria-label="Remove this line"
        >
          ×
        </button>

        <div className="ed-cue-lines">
          {tracks.map((code, t) => (
            <CueText
              key={code}
              code={code}
              secondary={t > 0}
              text={lineIn(cue, code, spoken)}
              // Only the primary is editable: the others are translations of it, and letting
              // them drift apart silently is how a bilingual track stops being bilingual.
              onText={t === 0 ? onText : undefined}
              onSelect={t === 0 ? catchSelection : undefined}
              onBlur={t === 0 ? () => setPicked('') : undefined}
              label={`Line ${index + 1}${t > 0 ? ` in ${code}` : ''}`}
            />
          ))}
          {/*
           * What the caption is being checked against.
           *
           * Not a track — it is never burnt in — so it is a paragraph rather than another
           * textarea, and visibly not editable. Hidden when it would only repeat the caption.
           */}
          {reference ? (
            <p className="ed-cue-ref" lang={inEnglish ? 'en' : (cue.spokenLanguage ?? undefined)}>
              {/*
               * The tag is the switch.
               *
               * It says which language this line is showing in, and pressing it swaps between
               * as-spoken and English. Per line because that is where the need is: most of a
               * track is already readable and only the odd line is in a script the creator
               * cannot read — a whole-file control would translate English into English for
               * everything else.
               */}
              {cue.spokenLanguage && cue.spokenLanguage !== 'en' ? (
                <button
                  type="button"
                  className="cue-lang"
                  onClick={onFlip}
                  aria-pressed={inEnglish}
                  title={inEnglish ? 'Show it as spoken' : 'Show it in English'}
                >
                  {inEnglish ? 'EN' : cue.spokenLanguage.toUpperCase()}
                </button>
              ) : null}
              {(inEnglish ? (cue.translations.en ?? '') : cue.text) || '…'}
            </p>
          ) : null}
          {picked && !glossary.some((g) => g.toLowerCase() === picked.toLowerCase()) ? (
            <button
              className="ed-keep"
              // `onMouseDown`, not `onClick`: clicking blurs the textarea first, which clears
              // the selection and unmounts this button before the click ever lands.
              onMouseDown={(e) => {
                e.preventDefault()
                onKeep(picked)
                setPicked('')
              }}
            >
              Keep &ldquo;{picked}&rdquo; untranslated
            </button>
          ) : null}
      </div>

      <div className="ed-cue-time">
        <Timecode
          ms={cue.startMs}
          min={floor}
          max={cue.endMs - 200}
          label={`Line ${index + 1} starts`}
          onChange={(at) => onTime(at, cue.endMs)}
        />
        <i className="ed-arrow">→</i>
        <Timecode
          ms={cue.endMs}
          min={cue.startMs + 200}
          max={ceiling}
          label={`Line ${index + 1} ends`}
          onChange={(at) => onTime(cue.startMs, at)}
        />
        <span
          className="ed-len"
          data-warn={rushed}
          title={rushed ? 'Too fast to read comfortably in this language' : undefined}
        >
          {(shown / 1000).toFixed(1)}s
        </span>
      </div>
      </div>
      {/* The gap after this line, offered as somewhere to put another. */}
      <Insert onAdd={onInsert} />
    </>
  )
}

/** Intervals people count video in. The smallest that keeps the ruler under ten labels wins. */
const TICKS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800] .map((s) => s * 1000)

/**
 * The timeline: every cue as a block you can drag.
 *
 * This replaces a row of typed timecodes, and it is the difference between describing a
 * timing and adjusting one. Typing `0:12.8` asks the creator to convert what they *saw* —
 * "it comes in a beat late" — into a number, then check the result by playing it again.
 * Dragging the block is the same edit with the conversion and the check removed.
 *
 * Three grabs, which is what every editor that does this well settles on:
 *
 *   - the **body** moves the cue, keeping its length;
 *   - the **left edge** moves when it comes in;
 *   - the **right edge** moves when it goes out.
 *
 * Everything clamps against the neighbouring cues, because burnt-in captions cannot overlap —
 * two at once means one drawn on top of the other. Dragging commits on release, so one gesture
 * is one save rather than one per frame, and the block follows the pointer meanwhile.
 */
function Timeline({
  cues,
  at,
  duration,
  live,
  onSeek,
  onTime,
}: {
  cues: Cue[]
  at: number
  duration: number
  live: string | null
  onSeek: (ms: number) => void
  onTime: (id: string, startMs: number, endMs: number) => void
}) {
  const track = useRef<HTMLDivElement>(null)
  const scroller = useRef<HTMLDivElement>(null)
  const [drag, setDrag] = useState<{ id: string; startMs: number; endMs: number } | null>(null)
  /**
   * How many screens wide the track is drawn.
   *
   * Not decoration. A 3:21 video in an 870px strip is 4.3 pixels a second, and the drag
   * handler converts pixels to time — so one pixel is 231ms and a cue edge cannot be placed
   * finer than a quarter of a second, which is visible as a caption landing late. Zooming
   * raises the resolution of the *edit*, not just of the picture.
   */
  const [zoom, setZoom] = useState(1)
  //: The track's real width in pixels, which is what decides whether a label fits. Measured
  //: rather than assumed: it changes with the window, with the zoom, and with the sidebar.
  const [width, setWidth] = useState(0)

  useEffect(() => {
    const el = track.current
    if (!el) return
    const watch = new ResizeObserver(([entry]) => setWidth(entry?.contentRect.width ?? 0))
    watch.observe(el)
    return () => watch.disconnect()
  }, [])

  const pct = (ms: number) => (duration ? (ms / duration) * 100 : 0)

  /**
   * Which cues get their number drawn.
   *
   * Asking each block whether *it* is wide enough is the wrong question: it drops the number
   * from a three-second cue with empty track either side, where there was plenty of room, and
   * keeps it on a wide cue in a crowd. The right question is whether the label would collide
   * with the last one drawn, which is what a reader actually experiences.
   *
   * So numbers thin out through a dense passage and fill back in as you zoom — always as
   * complete as the space honestly allows, rather than all-or-nothing.
   */
  const numbered = useMemo(() => {
    const keep = new Set<string>()
    if (!width || !duration) return keep
    let last = -Infinity
    for (const cue of cues) {
      const x = (cue.startMs / duration) * width
      if (x - last >= LABEL_PX) {
        keep.add(cue.id)
        last = x
      }
    }
    return keep
  }, [cues, duration, width])

  //: Keep the playhead in view while it moves, but never fight a scroll the creator is
  //: making themselves — only nudge when it has actually left the window.
  useEffect(() => {
    const box = scroller.current
    if (!box || zoom === 1 || !duration) return
    const x = (at / duration) * box.scrollWidth
    const edge = box.clientWidth * 0.15
    if (x < box.scrollLeft + edge || x > box.scrollLeft + box.clientWidth - edge) {
      box.scrollTo({ left: x - box.clientWidth / 2, behavior: 'smooth' })
    }
  }, [at, zoom, duration])

  const grab = (
    e: React.PointerEvent<HTMLElement>,
    cue: Cue,
    edge: 'in' | 'out' | 'move',
    floor: number,
    ceiling: number,
  ) => {
    const box = track.current?.getBoundingClientRect()
    if (!box || !duration) return
    e.stopPropagation()
    const el = e.currentTarget
    el.setPointerCapture(e.pointerId)

    const from = e.clientX
    const span = cue.endMs - cue.startMs
    let next = { id: cue.id, startMs: cue.startMs, endMs: cue.endMs }

    el.onpointermove = (m) => {
      const by = ((m.clientX - from) / box.width) * duration
      if (edge === 'move') {
        // Keeps its length, and stops at the neighbours rather than pushing through them.
        const startMs = Math.min(Math.max(cue.startMs + by, floor), ceiling - span)
        next = { id: cue.id, startMs, endMs: startMs + span }
      } else if (edge === 'in') {
        next = {
          id: cue.id,
          startMs: Math.min(Math.max(cue.startMs + by, floor), cue.endMs - 200),
          endMs: cue.endMs,
        }
      } else {
        next = {
          id: cue.id,
          startMs: cue.startMs,
          endMs: Math.max(Math.min(cue.endMs + by, ceiling), cue.startMs + 200),
        }
      }
      setDrag(next)
    }
    el.onpointerup = () => {
      el.onpointermove = null
      el.onpointerup = null
      setDrag(null)
      if (Math.round(next.startMs) !== cue.startMs || Math.round(next.endMs) !== cue.endMs) {
        onTime(cue.id, Math.round(next.startMs), Math.round(next.endMs))
      }
    }
  }

  /*
   * A readable scale.
   *
   * Without one the track is a strip with some blocks near the left and no way to tell
   * whether that is the first ten seconds or the first two minutes — which for a file whose
   * captions all sit in the opening minute of a three-minute video is the single most useful
   * thing it could say. The step is chosen so there are six to nine labels at any length,
   * from a set of intervals people actually count in.
   */
  const step = TICKS.find((t) => duration / t <= 9) ?? TICKS.at(-1)!
  const marks: number[] = []
  // Stops short of the end: a label is drawn to the right of its tick, so one at the very
  // edge hangs outside the track and scrolls it sideways on a phone.
  for (let mark = 0; mark < duration * 0.93; mark += step) marks.push(mark)

  return (
    <div className="ed-timeline">
      <div className="ed-scroll" ref={scroller}>
        <div
          className="ed-track"
          ref={track}
          style={{ width: `${zoom * 100}%` }}
          /*
           * Scrubbing, not just seeking.
           *
           * This used to seek once on press and then let go of the pointer, so the playhead
           * jumped where you clicked and stopped — you could pick a moment but not *look for*
           * one, which is what a timeline is mostly used for. Holding and dragging now follows
           * the pointer, including past the ends of the track, because a scrub that stops when
           * your finger leaves the strip is a scrub that fights you.
           *
           * A press that lands on a cue never reaches here: those stop propagation so a block
           * can still be dragged.
           */
          onPointerDown={(e) => {
            const box = track.current?.getBoundingClientRect()
            if (!box || !duration) return
            const el = e.currentTarget
            el.setPointerCapture(e.pointerId)

            const to = (x: number) =>
              onSeek(Math.max(0, Math.min(1, (x - box.left) / box.width)) * duration)
            to(e.clientX)

            el.onpointermove = (m) => to(m.clientX)
            el.onpointerup = () => {
              el.onpointermove = null
              el.onpointerup = null
            }
          }}
        >
          {/*
           * The cue numbers, in a lane of their own.
           *
           * They used to be inside the block, which cannot work: a block six pixels wide
           * carries twelve pixels of padding and clips its own label, so the number vanished
           * exactly where the track was busiest and identifying a cue mattered most.
           *
           * Out here they are positioned by the cue's start and bounded only by their
           * neighbours — which `numbered` has already spaced them against.
           */}
          <span className="ed-nums" aria-hidden="true">
            {cues.map((cue, i) =>
              numbered.has(cue.id) || live === cue.id ? (
                <b
                  key={cue.id}
                  data-live={live === cue.id}
                  style={{ insetInlineStart: `${pct(cue.startMs)}%` }}
                >
                  {i + 1}
                </b>
              ) : null,
            )}
          </span>

          <span className="ed-ruler" aria-hidden="true">
            {marks.map((at) => (
              <i key={at} style={{ insetInlineStart: `${pct(at)}%` }}>
                <b>{clock(at)}</b>
              </i>
            ))}
          </span>

          {cues.map((cue, i) => {
            const shown = drag?.id === cue.id ? drag : cue
            const floor = cues[i - 1]?.endMs ?? 0
            const ceiling = cues[i + 1]?.startMs ?? duration
            return (
              <div
                key={cue.id}
                className="ed-block"
                data-live={live === cue.id}
                data-dragging={drag?.id === cue.id}
                style={{
                  insetInlineStart: `${pct(shown.startMs)}%`,
                  width: `${Math.max(0.5, pct(shown.endMs - shown.startMs))}%`,
                }}
                title={cue.text}
                onPointerDown={(e) => grab(e, cue, 'move', floor, ceiling)}
              >
                <i
                  className="ed-edge"
                  data-side="in"
                  onPointerDown={(e) => grab(e, cue, 'in', floor, ceiling)}
                />
                <em>{cue.text}</em>
                <i
                  className="ed-edge"
                  data-side="out"
                  onPointerDown={(e) => grab(e, cue, 'out', floor, ceiling)}
                />
              </div>
            )
          })}

        <span
          className="ed-playhead"
          style={{ insetInlineStart: `${pct(Math.min(at, duration))}%` }}
        >
          <i />
        </span>
      </div>
    </div>

    {/*
     * Zoom, pinned to the viewport rather than scrolling away with the track.
     *
     * Steps rather than a slider: the useful question is "can I see the whole thing" or
     * "can I place this edge", and two or three answers cover both. `Fit` is named rather
     * than called 1x because that is what it is for.
     */}
    <span className="ed-zoom" role="group" aria-label="Zoom the timeline">
      {ZOOMS.map((z) => (
        <button
          key={z}
          type="button"
          aria-pressed={zoom === z}
          onClick={() => setZoom(z)}
          title={
            z === 1
              ? 'The whole video at once'
              : `${z}x — finer placement when cues are close together`
          }
        >
          {z === 1 ? 'Fit' : `${z}×`}
        </button>
      ))}
    </span>
  </div>
)
}

/**
 * How far the timeline can be stretched.
 *
 * Eight screens is about 35 pixels a second on a desktop, where a pixel is 29ms — fine enough
 * that a caption edge lands where it was dropped. Past that the cost is scrolling, and there
 * is no gain: nobody is trimming subtitles to the frame.
 */
const ZOOMS = [1, 2, 4, 8] as const

/**
 * Pixels a cue number needs before the next one may be drawn.
 *
 * Two digits of 10px mono plus the gap that keeps them from reading as one number.
 */
const LABEL_PX = 20

/**
 * Words that must survive translation exactly as written.
 *
 * The thing a bilingual subtitle gets wrong most often, and the reason a line ends up in two
 * scripts at once: "Computer" in a Burmese sentence stays "Computer", because that is how the
 * word is actually said — the dictionary equivalent reads as stilted or simply wrong. Product
 * names, brands, acronyms and most technical vocabulary behave the same way.
 *
 * Terms are added by selecting them in a line, which is where a creator notices the problem,
 * or by typing. Kept on the identity kit, so a channel's vocabulary is built once rather than
 * re-entered for every video.
 */
function Glossary({ terms, onChange }: { terms: string[]; onChange: (terms: string[]) => void }) {
const [open, setOpen] = useState(false)
const [draft, setDraft] = useState('')

const add = (raw: string) => {
  // Comma or newline separated, so pasting a list from somewhere else works.
  const next = raw
    .split(/[,\n]/)
    .map((t) => t.trim())
    .filter(Boolean)
  if (next.length) onChange([...terms, ...next])
  setDraft('')
}

  return (
    <span className="ed-gloss">
      <button
        className="ed-gloss-open"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        title="Words to leave untranslated"
      >
        Keep as-is{terms.length ? ` · ${terms.length}` : ''}
      </button>

      {open ? (
        <div className="ed-gloss-panel" role="dialog" aria-label="Words to leave untranslated">
          <p>
            Left exactly as written when this is translated. Useful for technical words a
            translation would ruin — <b>Computer</b>, <b>API</b>, a product name.
          </p>

          <div className="ed-gloss-terms">
            {terms.map((t) => (
              <span key={t}>
                {t}
                <button
                  onClick={() => onChange(terms.filter((x) => x !== t))}
                  aria-label={`Stop keeping ${t}`}
                >
                  <IconClose size={13} />
                </button>
              </span>
            ))}
            {terms.length === 0 ? <em>Nothing yet.</em> : null}
          </div>

          <div className="ed-gloss-add">
            <input
              value={draft}
              placeholder="Add a word…"
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') add(draft)
                if (e.key === 'Escape') setOpen(false)
              }}
              aria-label="Add a word to keep untranslated"
            />
            <button onClick={() => add(draft)} disabled={!draft.trim()}>
              Add
            </button>
          </div>
        </div>
      ) : null}
    </span>
  )
}

/** This cue's line in one language. The primary is the scene's own text, not a translation. */
function lineIn(cue: Cue, code: string, spoken: string | null): string {
  /*
   * Keyed off what was *spoken*, not off whichever track happens to be first.
   *
   * A scene's own text is the source language — the server's `_track` has always said so —
   * and every other language lives in `translations`. This used to compare against the
   * primary track instead, which was the same thing only while the primary track *was* the
   * source. The moment a project was a translation, the Burmese track asked for "the primary"
   * and got handed the English transcript, so the editor showed the original and no Burmese
   * appeared anywhere on the screen.
   */
  return code === spoken ? cue.text : (cue.translations[code] ?? cue.text)
}

/**
 * The caption block on the video, where it will burn in — and draggable.
 *
 * Height was a constant: 16% up from the bottom, which is right for a talking head and wrong
 * the moment the subject is standing in the lower third or the footage has its own titles
 * there. Whether a caption is in the way is a fact about *this* video, so it is answered by
 * dragging it on this video rather than by a preset.
 *
 * Pointer events rather than mouse: this has to work on the phone the brief says people are
 * editing on. Capture keeps the drag alive when the pointer leaves the small target, and the
 * position is only written on release — one save per drag rather than one per frame.
 */
function Placement({
  bottom,
  onMove,
  empty,
  hint,
  children,
}: {
  bottom: number
  onMove: (v: number) => void
  empty: boolean
  hint: boolean
  children: React.ReactNode
}) {
  const [dragging, setDragging] = useState<number | null>(null)
  const shown = dragging ?? bottom

  const start = (e: React.PointerEvent<HTMLDivElement>) => {
    const frame = e.currentTarget.parentElement
    if (!frame) return
    e.currentTarget.setPointerCapture(e.pointerId)
    const box = frame.getBoundingClientRect()
    const move = (y: number) => Math.min(0.85, Math.max(0.02, (box.bottom - y) / box.height))
    setDragging(move(e.clientY))
    e.currentTarget.onpointermove = (m) => setDragging(move(m.clientY))
  }

  const end = (e: React.PointerEvent<HTMLDivElement>) => {
    e.currentTarget.onpointermove = null
    if (dragging !== null && Math.abs(dragging - bottom) > 0.001) onMove(dragging)
    setDragging(null)
  }

  return (
    <div
      className="ed-place"
      data-empty={empty}
      data-dragging={dragging !== null}
      style={{ bottom: `${shown * 100}%` }}
      onPointerDown={start}
      onPointerUp={end}
      onPointerCancel={end}
      role="slider"
      aria-label="Caption position"
      aria-valuemin={2}
      aria-valuemax={85}
      aria-valuenow={Math.round(shown * 100)}
      tabIndex={0}
      // Reachable without a pointer, and finer than a drag.
      onKeyDown={(e) => {
        if (e.key === 'ArrowUp') onMove(Math.min(0.85, bottom + 0.01))
        else if (e.key === 'ArrowDown') onMove(Math.max(0.02, bottom - 0.01))
        else return
        e.preventDefault()
      }}
    >
      {children}
      {empty && hint ? <span className="ed-place-ghost">Drag to move the captions</span> : null}
    </div>
  )
}

/**
 * A timecode you set the way you set a clock.
 *
 * Editable — but not a text box. A free text field asks for a whole formatted string, accepts
 * anything, and validates afterwards; `0:12.8` is three separate quantities and typing it
 * means retyping the two you did not want to change. This is what a native time input and
 * every editing suite's timecode field do instead: minutes, seconds and tenths as their own
 * segments, each one a spinner.
 *
 *   - **arrows** step the focused segment, which is the common edit — a caption is usually a
 *     tenth early, not at the wrong minute;
 *   - **digits** type into it and move on when it is full;
 *   - **left/right** walk between segments, so a whole timecode is enterable from the keyboard
 *     without ever selecting text.
 *
 * Every route clamps against the neighbouring cues, because burnt-in captions cannot overlap.
 */
function Timecode({
  ms,
  min,
  max,
  label,
  onChange,
}: {
  ms: number
  min: number
  max: number
  label: string
  onChange: (ms: number) => void
}) {
  const [typing, setTyping] = useState<{ part: number; digits: string } | null>(null)
  const field = useRef<HTMLSpanElement>(null)

  const total = Math.max(0, ms) / 1000
  const parts = [
    Math.floor(total / 60),
    Math.floor(total % 60),
    Math.floor((total * 10) % 10),
  ]
  //: What each segment is worth, and how far it counts before wrapping.
  const SEGMENTS = [
    { ms: 60_000, cap: 100, pad: 1, name: 'minutes' },
    { ms: 1_000, cap: 60, pad: 2, name: 'seconds' },
    { ms: 100, cap: 10, pad: 1, name: 'tenths' },
  ]

  const clamp = (v: number) => Math.min(Math.max(v, min), max)

  const bump = (part: number, by: number) => {
    const next = clamp(ms + by * SEGMENTS[part]!.ms)
    if (Math.round(next) !== ms) onChange(next)
  }

  const set = (part: number, value: number) => {
    const rest = ms - parts[part]! * SEGMENTS[part]!.ms
    const next = clamp(rest + value * SEGMENTS[part]!.ms)
    if (Math.round(next) !== ms) onChange(next)
  }

  const focusPart = (el: HTMLElement | null, part: number) => {
    const box = el?.closest('.ed-clockfield')
    const seg = box?.querySelectorAll<HTMLElement>('[data-part]')[part]
    seg?.focus()
  }

  const onKey = (e: React.KeyboardEvent<HTMLSpanElement>, part: number) => {
    const seg = SEGMENTS[part]!
    if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
      e.preventDefault()
      setTyping(null)
      bump(part, e.key === 'ArrowUp' ? 1 : -1)
    } else if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
      e.preventDefault()
      setTyping(null)
      focusPart(e.currentTarget, Math.min(2, Math.max(0, part + (e.key === 'ArrowRight' ? 1 : -1))))
    } else if (/^[0-9]$/.test(e.key)) {
      e.preventDefault()
      // Digits accumulate within the segment, then hand over — 1, 2 in the seconds segment
      // means twelve seconds, not one then two.
      const digits = ((typing?.part === part ? typing.digits : '') + e.key).slice(-seg.pad)
      const value = Number(digits)
      setTyping({ part, digits })
      set(part, Math.min(value, seg.cap - 1))
      if (digits.length >= seg.pad) {
        setTyping(null)
        if (part < 2) focusPart(e.currentTarget, part + 1)
      }
    } else if (e.key === 'Backspace' || e.key === 'Delete') {
      e.preventDefault()
      setTyping(null)
      set(part, 0)
    }
  }

  /*
   * Wheel over the focused segment steps it, like every other spinner.
   *
   * Registered by hand rather than with `onWheel`, because React attaches wheel listeners
   * passively: `preventDefault` there does nothing but log a warning, and the list scrolls
   * away underneath while the value changes. Focus is required first, or scrolling past the
   * list would silently retime whatever cue happened to be under the pointer.
   */
  useEffect(() => {
    const el = field.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      const seg = (e.target as HTMLElement | null)?.closest<HTMLElement>('[data-part]')
      if (!seg || document.activeElement !== seg) return
      e.preventDefault()
      bump(Number(seg.dataset.part), e.deltaY < 0 ? 1 : -1)
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  })

  return (
    <span className="ed-clockfield" ref={field} role="group" aria-label={label}>
      {SEGMENTS.map((seg, part) => (
        <span key={seg.name} style={{ display: 'contents' }}>
          {part > 0 ? <i aria-hidden="true">{part === 1 ? ':' : '.'}</i> : null}
          <span
            data-part={part}
            className="ed-seg"
            role="spinbutton"
            tabIndex={0}
            aria-label={`${label} ${seg.name}`}
            aria-valuenow={parts[part]}
            aria-valuemin={0}
            aria-valuemax={seg.cap - 1}
            onKeyDown={(e) => onKey(e, part)}
            onBlur={() => setTyping(null)}
          >
            {String(parts[part]).padStart(seg.pad, '0')}
            {/* Says it is a spinner, on the segment the arrows are actually moving. */}
            <u aria-hidden="true" />
          </span>
        </span>
      ))}
    </span>
  )
}

const Pause = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
    <rect x="6" y="5" width="4" height="14" rx="1" />
    <rect x="14" y="5" width="4" height="14" rx="1" />
  </svg>
)

/** `m:ss`, or `m:ss.s` where tenths matter — cue timings do, a playhead does not. */
function clock(ms: number, tenths = false): string {
  const total = Math.max(0, ms) / 1000
  const m = Math.floor(total / 60)
  const s = total - m * 60
  return tenths
    ? `${m}:${s.toFixed(1).padStart(4, '0')}`
    : `${m}:${String(Math.floor(s)).padStart(2, '0')}`
}

/**
 * A caption style as inline CSS, with the font *size* taken out.
 *
 * The styles are authored as `font: 700 15px/1 var(--display)` — a shorthand carrying a fixed
 * 15px, which was right for the chip these were first drawn in and wrong everywhere since. As
 * an inline style it beat the stylesheet, so the preview caption stayed 15px whatever frame it
 * sat in, and the one thing this screen exists to show — how big the caption will actually be
 * in the export — was the one thing it could not show.
 *
 * Weight, family and line-height are the style. Size belongs to the frame.
 */
function styleOf(id: string, keepSize = false): Record<string, string> {
  const css = CAPTION_STYLES.find((c) => c.id === id)?.css ?? ''
  const out: Record<string, string> = {}

  for (const rule of css.split(';')) {
    const [rawKey, ...rest] = rule.split(':')
    const key = rawKey?.trim()
    const value = rest.join(':').trim()
    if (!key || !value) continue

    if (key === 'font' && !keepSize) {
      // `<weight> <size>/<line-height> <family>` — every style here is written this way.
      //
      // Size *and* line-height are both dropped. They were authored for a one-line chip, so
      // the line-height is 1.0 — which on a caption that wraps draws the second line on top
      // of the first. Both belong to the frame and the language, not to the style: the
      // rasterizer takes its line-height from the language pack (1.4 for English, 1.75 for
      // Burmese, whose marks stack above and below), and so does the preview.
      const parts = /^(\d+)\s+[\d.]+px\/[\d.]+\s+(.+)$/.exec(value)
      if (parts) {
        out.fontWeight = parts[1]!
        out.fontFamily = parts[2]!
        continue
      }
    }
    if (key === 'line-height' && !keepSize) continue
    if ((key === 'font-size' || key === 'font') && !keepSize) continue

    out[key.replace(/-([a-z])/g, (_, c: string) => c.toUpperCase())] = value
  }
  return out
}

const DUB_WHY =
  'Your video comes back whole, in the voice you picked. The original speech is replaced.'
const NARRATE_WHY =
  'Your pictures, cut to the script and read aloud. One picture per line, in the order you added them.'
const SUBTITLE_WHY =
  'Your video comes back whole, at its full length. Only the captions are drawn on.'

type DubTrack = {
  /** Loading the first time, or after the voice changed. */
  working: boolean
  /** Seconds spent preparing, so a long wait can say how long it has been. */
  elapsed: number
  /** The speech itself, so a lane with no video can use it as the clock. */
  ref: React.RefObject<HTMLAudioElement | null>
  /** True once there is audio loaded and playable. */
  ready: boolean
  /** Why there is nothing to play, in words a creator can act on. */
  problem: string | null
}

/**
 * The dub, played against the picture instead of the original audio.
 *
 * The video element stays the clock — it is what the timeline, the captions and the cue list
 * are all already following — and the speech is a second element chased to it. The other way
 * round (audio as clock, video seeking to match) is what produces the stutter people
 * associate with dubbed playback: video seeks are expensive and land on keyframes.
 *
 * "Chased" rather than "synced once", because they drift: two media elements decoding
 * separately do not stay together over minutes. Correcting only past a threshold matters as
 * much as correcting at all — assigning `currentTime` every frame re-buffers the audio and
 * produces a click each time, so a tenth of a second of drift is left alone and anything
 * larger is snapped.
 */
function useDubTrack(
  video: React.RefObject<HTMLVideoElement | null>,
  speech: React.RefObject<HTMLAudioElement | null>,
  { on, projectId, voice }: { on: boolean; projectId: string | null; voice: string | null },
): DubTrack {
  const [working, setWorking] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [problem, setProblem] = useState<string | null>(null)
  const [ready, setReady] = useState(false)
  /*
   * The speech is an element on the page, not a `new Audio()`.
   *
   * A detached element plays perfectly well and is invisible to everything else: it cannot be
   * inspected, it is not torn down with the tree, and nothing outside this hook can tell
   * whether the narration is actually running. Rendering it makes the preview's state
   * observable — including to the person debugging it.
   */
  const audio = speech

  useEffect(() => {
    const el = video.current

    if (!on || !projectId || !voice) {
      //: Give the original audio back on the way out. A screen that leaves the video muted
      //: after the toggle is off looks like it broke the video.
      if (el) el.muted = false
      audio.current?.pause()
      setProblem(null)
      setReady(false)
      return
    }

    const track = audio.current
    if (!track) return

    let dropped = false
    let objectUrl: string | null = null
    if (el) el.muted = true
    setWorking(true)
    setProblem(null)
    setReady(false)

    //: Synthesis takes a while the first time each voice is heard — it is the whole video,
    //: one request per line — so the picture keeps playing and the speech joins when it is
    //: ready rather than freezing the screen behind a spinner. The toggle reports `working`
    //: so the wait is explained rather than mysterious.
    void fetchDubPreview(projectId, voice)
      .then((url) => {
        if (dropped) {
          URL.revokeObjectURL(url)
          return
        }
        objectUrl = url
        track.src = url
        if (el) track.currentTime = el.currentTime
        setWorking(false)
        setReady(true)
        if (el && !el.paused) void track.play().catch(() => {})
      })
      .catch((why: unknown) => {
        if (dropped) return
        setWorking(false)
        //: The video is audible again rather than left silent behind a dub that never came.
        if (el) el.muted = false
        setProblem(why instanceof Error ? why.message : 'The voice could not be prepared.')
      })

    /*
     * Two modes, and which one applies is decided by whether there is a picture to follow.
     *
     * With a video the picture is the clock and the speech chases it — video seeks are
     * expensive and land on keyframes, so driving it the other way stutters. A narrated
     * project has no video at all: its pictures are stills swapped at beat boundaries, so
     * there is nothing to chase and the speech *is* the clock. The hook used to require a
     * video element and returned immediately without one, which is why "Hear it" silently
     * did nothing on this lane.
     */
    if (!el) {
      return () => {
        dropped = true
        track.pause()
        if (objectUrl) URL.revokeObjectURL(objectUrl)
      }
    }

    const play = () => {
      track.currentTime = el.currentTime
      void track.play().catch(() => {})
    }
    const pause = () => track.pause()
    const chase = () => {
      if (Math.abs(track.currentTime - el.currentTime) > 0.1) track.currentTime = el.currentTime
    }

    el.addEventListener('play', play)
    el.addEventListener('pause', pause)
    el.addEventListener('seeked', chase)
    el.addEventListener('timeupdate', chase)
    return () => {
      dropped = true
      el.removeEventListener('play', play)
      el.removeEventListener('pause', pause)
      el.removeEventListener('seeked', chase)
      el.removeEventListener('timeupdate', chase)
      track.pause()
      el.muted = false
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [on, projectId, voice, video, audio])

  /*
   * Count the wait out loud.
   *
   * Preparing a narration is one speech request per line against a per-minute quota, so a
   * thirty-line script is minutes, not seconds. A label that never changes is
   * indistinguishable from a hang. A number that moves is proof it is still alive.
   */
  useEffect(() => {
    if (!working) {
      setElapsed(0)
      return
    }
    const started = Date.now()
    const tick = setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 1000)
    return () => clearInterval(tick)
  }, [working])

  return { working, problem, elapsed, ref: audio, ready }
}

/**
 * How long a line needs to be on screen to be read.
 *
 * The same rule the server applies in `execution/timing.py`, so the count in the toolbar, the
 * marks on the rows and what "Fix the pace" actually does are all describing one thing. They
 * disagreed before: the row used a characters-per-second table baked into the client, at a
 * threshold of 75%, so a line the server would widen could show no mark at all.
 */
function readableMs(text: string, cps: number): number {
  return Math.max(700, Math.round((text.trim().length / cps) * 1000))
}

/**
 * `timing.TOLERANCE` on the server. Kept in step deliberately.
 *
 * Without it the two disagree in the worst direction: pressing "Fix the pace" widened
 * everything the server considered tight, and the screen still reported 32 lines too fast —
 * because the client was flagging cues that were a few milliseconds short of perfect. A
 * button that visibly does nothing is worse than no button.
 */
const PACE_TOLERANCE = 1.08

/**
 * The words that will be burnt onto the video, which are the words that have to be readable.
 *
 * `cue.text` is the scene's `script_line` — the transcript, in the language that was *spoken*.
 * On a translated video that is not what anyone sees, and measuring it against the target
 * language's reading rate compares an English sentence to a Burmese reading speed. It made the
 * editor report 32 lines too fast on a video the server had just fixed down to 3.
 */
function burntIn(cue: Cue, language: string | undefined): string {
  if (!language) return cue.text
  return cue.translations?.[language] ?? cue.text
}

function rushedAt(text: string, cps: number, windowMs: number): boolean {
  return windowMs * PACE_TOLERANCE < readableMs(text, cps)
}

/**
 * One editable caption line.
 *
 * Its own component because of the caret.
 *
 * The textarea used to be driven straight from server state: every keystroke fired a mutation,
 * the mutation invalidated the plan, the plan came back, and the `value` prop was replaced
 * mid-word — which makes React re-set the element's value and drop the caret at the end. You
 * type three characters into the middle of a line and the next one lands at the end of it.
 *
 * It is worst in Burmese and Thai, and not because of the fix — because those scripts have no
 * word spaces, so editing *is* placing the caret inside a cluster, every time.
 *
 * So the field owns its text while it has focus, and accepts the server's version only when it
 * does not. The edit still saves on every keystroke; what stops is the round trip reaching back
 * in and moving the cursor.
 */
function CueText({
  code,
  text,
  secondary,
  label,
  onText,
  onSelect,
  onBlur,
}: {
  code: string
  text: string
  secondary: boolean
  label: string
  onText?: (text: string) => void
  onSelect?: (e: React.SyntheticEvent<HTMLTextAreaElement>) => void
  onBlur?: () => void
}) {
  const [draft, setDraft] = useState(text)
  const focused = useRef(false)

  //: Someone else's change — a re-transcription, a translation, the pace fix rewriting a line
  //: — still lands, as long as this field is not the one being typed into.
  useEffect(() => {
    if (!focused.current) setDraft(text)
  }, [text])

  return (
    <textarea
      rows={1}
      lang={code}
      data-sub={secondary}
      value={focused.current ? draft : text}
      readOnly={secondary}
      onFocus={() => {
        focused.current = true
        setDraft(text)
      }}
      onChange={
        onText
          ? (e) => {
              setDraft(e.target.value)
              onText(e.target.value)
            }
          : undefined
      }
      onSelect={onSelect}
      onBlur={() => {
        focused.current = false
        setDraft(text)
        onBlur?.()
      }}
      aria-label={label}
    />
  )
}
