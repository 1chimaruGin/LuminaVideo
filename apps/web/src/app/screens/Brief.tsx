/**
 * Step 2 — give the task what it needs.
 *
 * Two states of one screen, in the order they happen.
 *
 * **Before there is a plan** it collects the input. It can word the question properly because
 * the task is already chosen: "Drop the video you want subtitled", not "Paste a link, write an
 * idea, or drop a file". That is the whole reason the box is here and not on Start.
 *
 * **Once there is a plan** it states the outcome in one sentence and what it will cost, and
 * asks the creator to confirm — which is exactly what `design-brief.md` puts at this step:
 * "The system proposes an outcome in one plain sentence… Show the estimated credit cost here,
 * before anything runs."
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import { assetUrl } from '../../api/client'
import type { LanguageOut } from '@lumina/client'

import { messageOf } from '../../api/errors'
import {
  useCreateProject,
  useLanguages,
  useProjects,
  useQuote,
  useRecipes,
  useUpload,
} from '../../api/queries'
import { RECIPES } from '../data'
import { RECIPE_ART } from '../RecipeArt'
import type { Studio } from '../live'
import { Pipeline, type StageState } from '../Pipeline'
import { Head, View } from '../Shell'
import {
  IconCheck,
  IconClose,
  IconCoin,
  IconPaperclip,
  IconSpark,
  IconType,
  IconFilm,
  IconUpload,
} from '../icons'

export function Brief({ s, onNext }: { s: Studio; onNext: () => void }) {
  const spec = RECIPES.find((r) => r.id === s.recipe) ?? RECIPES[0]!
  const planned = s.scenes.length > 0

  return planned ? <Confirm s={s} onNext={onNext} /> : <Ask s={s} spec={spec} />
}

/**
 * What each task will actually accept, which is not the same question as what it asks for.
 *
 * This has to agree with the server or the creator meets a 400 they could not have predicted.
 * The three generating lanes take an optional *picture* to build every frame around; they
 * refuse footage, because generating a video from a video is not what they do. The two
 * source lanes require footage and nothing else. The file picker's `accept` is set from the
 * same row, so the file browser never offers a file the server would reject.
 */
type Takes = {
  required: boolean
  accept: string
  invite: string
  hint: string
  /**
   * Whether the text box holds the script rather than a name.
   *
   * Narrate is the first lane that needs a file *and* words: the footage is a bed and the
   * script is the content, so both are required and the box that is an optional title
   * everywhere else is the point of the screen here.
   */
  script?: boolean
}

const PICTURE: Takes = {
  required: false,
  accept: 'image/*',
  invite: 'Add a picture',
  hint: 'Optional — a still to build the look around.',
}

const TAKES: Record<string, Takes> = {
  explainer: PICTURE,
  clip_long_video: {
    required: true,
    accept: 'video/*',
    invite: 'Choose your video',
    hint: 'The long video you want cut down.',
  },
  subtitle_only: {
    required: true,
    accept: 'video/*',
    invite: 'Choose your video',
    hint: 'The video you want subtitled.',
  },
  // A dub is a finished video with a different voice on it, so it asks for the same thing
  // Subtitle asks for and in the same two ways. What it does with the words is the only
  // difference: they are spoken rather than drawn.
  dub: {
    required: true,
    accept: 'video/*',
    invite: 'Choose your video',
    hint: 'The video you want dubbed.',
  },
  narrate: {
    required: true,
    accept: 'video/*',
    invite: 'Choose the footage',
    hint: 'What plays underneath. It loops if your script runs longer.',
    script: true,
  },
}

/** Before there is a plan: collect what this task needs. */
function Ask({ s, spec }: { s: Studio; spec: (typeof RECIPES)[number] }) {
  const [text, setText] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [words, setWords] = useState<File | null>(null)
  const [over, setOver] = useState(false)
  const [failed, setFailed] = useState<string | null>(null)
  const picker = useRef<HTMLInputElement>(null)
  const wordPicker = useRef<HTMLInputElement>(null)

  const upload = useUpload()
  const create = useCreateProject()
  const recipes = useRecipes()
  const langs = useLanguages()
  const working = upload.isPending || create.isPending
  const takes = TAKES[spec.id] ?? PICTURE
  const Art = RECIPE_ART[spec.id] ?? RECIPE_ART.explainer!
  //: Asked for, never kept. A second copy of the stage list lived in `data.ts` and went stale
  //: without anything failing: three of the five were wrong, so Voice over drew a three-step
  //: pipeline for work that actually runs five.
  const stages = recipes.data?.find((r) => r.id === spec.id)?.stages ?? []

  /*
   * Languages: what was said, and what comes out.
   *
   * Only the two lanes that read an existing video have a *spoken* language — the other three
   * write their own script, so there is nothing to translate from. Both default to the
   * channel's language, which is the answer for everyone who is not translating.
   */
  //: Which of the two requests is in flight. Not a progress percentage — see `progress`.
  const [phase, setPhase] = useState<'idle' | 'sending' | 'working'>('idle')
  const projects = useProjects()
  /*
   * What was spoken defaults to "work it out", not to the channel's language.
   *
   * The creator knows what they want *out*; what went *in* is a property of the file they are
   * uploading, and they may not know it — or it may be two languages, which naming one cannot
   * express. Defaulting to English quietly forced every Japanese video through an English
   * decode unless someone thought to change it.
   */
  const [spoken, setSpoken] = useState(AUTO)
  const [into, setInto] = useState(s.language)
  //: No speech engine on this server, so the words have to come from a file. Known before the
  //: creator uploads anything, which is the whole point of asking — the alternative is
  //: letting them wait through an upload for a 422 the screen could have predicted.
  const deaf = langs.data?.every((l) => !l.can_listen) ?? false
  const needsWords = takes.required && deaf
  /**
   * A video already uploaded, chosen instead of a new one.
   *
   * Deduplicated by asset: the same file subtitled twice is two projects and one video, and a
   * picker that offered it twice would be listing our rows rather than their footage.
   */
  const [reused, setReused] = useState<Reusable | null>(null)
  //: Whether the "from your videos" picker is open. A sheet rather than a row of chips: a
  //: creator with a thousand videos cannot pick from six of them.
  const [browsing, setBrowsing] = useState(false)
  const mine = useMemo<Reusable[]>(() => {
    const seen = new Set<string>()
    const out: Reusable[] = []
    for (const p of projects.data ?? []) {
      const id = p.source_asset_id
      if (!id || seen.has(id)) continue
      seen.add(id)
      out.push({
        assetId: id,
        title: p.title || 'Untitled',
        length: p.source_duration_ms ? clock(p.source_duration_ms) : 'video',
      })
    }
    return out
  }, [projects.data])

  //: A video already uploaded counts as the file, because to the creator it is one.
  const hasVideo = Boolean(file) || Boolean(reused)
  const scripted = Boolean(takes.script)

  const ready = takes.required
    ? hasVideo && (scripted ? Boolean(text.trim()) : !needsWords || Boolean(words))
    : Boolean(text.trim() || file)
  //: What is still missing, in the order it is asked for.
  const waiting = !hasVideo
    ? (WAITING[spec.id] ?? 'Add something')
    : scripted && !text.trim()
      ? 'Write the script'
      : needsWords && !words
        ? 'Add the subtitles or script'
        : (WAITING[spec.id] ?? 'Add something')

  /**
   * How far along, from what the browser can actually see.
   *
   * Creation is one POST that transcribes, translates and plans before it answers, so the
   * client cannot observe the boundaries between those. Rather than animate a guess, this
   * reports only what it knows: the upload is a separate request, and everything after it is
   * the first server stage — which for the lanes that start from a file is listening, and is
   * where nearly all of the wait goes.
   *
   * The honest cost is that the last second or two still says "listening" while the server is
   * really translating. The dishonest alternative is a timer pretending to be progress.
   */
  const progress = useMemo<Partial<Record<string, StageState>>>(() => {
    if (phase === 'idle') return {}
    const first = stages[0]
    if (phase === 'sending') return first ? { [first]: 'now' } : {}
    const rest = stages.find((id) => id !== first)
    return {
      ...(first ? { [first]: 'done' as StageState } : {}),
      ...(rest ? { [rest]: 'now' as StageState } : {}),
    }
  }, [phase, stages])

  const attach = (picked: File | null | undefined) => {
    if (!picked) return
    setFile(picked)
    setFailed(null)
  }

  const make = async () => {
    if (!ready) return
    setFailed(null)
    setPhase('sending')
    try {
      //: Nothing to send when the video is already here — that is the whole point of the
      //: picker, and re-uploading it would be the slowest step in the product done twice.
      const source = file ? await upload.mutateAsync(file) : null
      const script = words ? await upload.mutateAsync(words) : null
      setPhase('working')
      const plan = await create.mutateAsync({
        brief: text.trim() || file?.name || 'Untitled',
        /*
         * The name, sent as a name.
         *
         * On a lane that starts from a file the box asks what to call it, not what it is
         * about — and the answer used to travel as the brief, become the project's title, and
         * then be overwritten by the plan's. On a subtitle track the plan's title is that
         * video's first caption, so typing a name did nothing and the header showed a line of
         * the transcript instead.
         */
        title: takes.required && !scripted && text.trim() ? text.trim().slice(0, 80) : null,
        recipe: spec.id,
        language: takes.required ? into : s.language,
        source_language: takes.required && !scripted ? spoken : null,
        source_asset_id: source?.asset_id ?? reused?.assetId ?? null,
        transcript_asset_id: script?.asset_id ?? null,
      })
      s.setProjectId(plan.project_id)
    } catch (err) {
      // The server's own words. It knows which task wanted what.
      setFailed(messageOf(err, 'That did not work. Try again.'))
      setPhase('idle')
    }
  }

  return (
    <View
      center
      mid
      action={
        <button
          className="btn primary block"
          onClick={() => void make()}
          disabled={!ready || working}
        >
          <IconSpark size={19} />
          {working ? 'Working it out…' : ready ? spec.name : waiting}
        </button>
      }
    >
      {/*
       * The task, drawn, beside the question.
       *
       * The screen was a heading and two boxes stranded in the middle of a very wide page. The
       * diagram is not decoration: it is the same picture that was pressed on the previous
       * screen, so this one is visibly a continuation of that choice rather than a form that
       * appeared from nowhere — and it says what comes out, in no language at all.
       */}
      <div className="brief-head">
        <span className="brief-art">
          <Art />
        </span>
        <Head kicker={spec.name} title={TITLES[spec.id] ?? 'What is it about?'} lede={spec.blurb} />
      </div>

      {/*
       * No pipeline strip before anything has run.
       *
       * It draws five stages with progress styling on a screen where nothing has started, so
       * it reports the state of work that does not exist — and it is the widest element on
       * the screen, pulling the eye away from the two things the creator is actually here to
       * do. It returns the moment there is a plan to track, on the confirm step below.
       */}
      {phase !== 'idle' && stages.length ? (
        <div className="brief-steps">
          <Pipeline used={stages} state={progress} />
        </div>
      ) : null}

      {/*
       * One box, not two.
       *
       * The words and the picture were a textarea and a large dashed panel stacked on top of
       * each other, which made an optional still look like a second required step and pushed
       * the button off a phone screen. They are one composer now — write in it, attach to it —
       * which is the shape every image-to-video tool has settled on, and it collapses two
       * loud boxes into one.
       */}
      <div
        className="composer"
        data-over={over}
        data-lead={takes.required}
        data-sky={scripted}
        onDragOver={(e) => {
          e.preventDefault()
          setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setOver(false)
          attach(e.dataTransfer.files?.[0])
        }}
      >
        {/*
         * File-first tasks lead with the file, because the file is the entire input.
         *
         * Narrate is not one of them. Its input is the script; the footage is a bed to run it
         * over. Two cards the size of these outweigh the box the creator came here to type
         * in, so this lane attaches from the composer's own footer instead — the pattern this
         * file already states below: "attaching is a control on the composer, not a panel of
         * its own".
         */}
        {takes.required && !scripted && !file && !reused ? (
          <div className="source">
            {/*
             * Two ways in, given equal weight.
             *
             * Upload is the first time; picking one you have is the second and third, and the
             * same footage is wanted more than once more often than not — subtitle it, then
             * cut it. Neither is the special case, so neither is hidden behind the other.
             */}
            <button className="source-way" onClick={() => picker.current?.click()}>
              <span className="composer-mark">
                <IconUpload size={22} />
              </span>
              <b>{takes.invite}</b>
              <small>Drop it here, or choose one</small>
            </button>

            {mine.length ? (
              <button className="source-way" onClick={() => setBrowsing(true)}>
                <span className="composer-mark">
                  <IconFilm size={22} />
                </span>
                <b>From your videos</b>
                <small>{mine.length} already uploaded</small>
              </button>
            ) : null}
          </div>
        ) : null}

        {browsing ? (
          <Library
            videos={mine}
            onPick={(v) => {
              setReused(v)
              setBrowsing(false)
              setFailed(null)
            }}
            onClose={() => setBrowsing(false)}
          />
        ) : null}

        {/* One already uploaded, chosen instead of a new file. */}
        {reused && !file ? (
          <div className="attached">
            <span className="attached-what">
              <b>{reused.title}</b>
              <span>Already uploaded · {reused.length}</span>
            </span>
            <button
              className="iconbtn"
              aria-label="Choose a different video"
              title="Choose a different video"
              onClick={() => setReused(null)}
            >
              <IconClose size={17} />
            </button>
          </div>
        ) : null}

        {file ? (
          <div className="attached">
            {/* The file itself, not its name in a chip. Seeing the frame you dropped is the
                only way to know you dropped the right one. */}
            <Thumb file={file} />
            <span className="attached-what">
              <b>{file.name}</b>
              <span>{describe(file)}</span>
            </span>
            <button
              className="iconbtn"
              aria-label={`Remove ${file.name}`}
              title="Remove"
              onClick={() => {
                setFile(null)
                setFailed(null)
                if (picker.current) picker.current.value = ''
              }}
            >
              <IconClose size={17} />
            </button>
          </div>
        ) : null}

        {/*
         * The words, if the creator already has them.
         *
         * Not an afterthought and not "advanced": on a server with no speech engine this is
         * the *only* way these lanes work, and the creator almost certainly has the file —
         * every download of a lecture, a podcast or an episode comes with one. Reading it is
         * also exact where listening is a guess, so it stays the better path even once an
         * engine exists.
         */}
        {takes.required && !scripted ? (
          <div className="composer-words" data-has={Boolean(words)} data-needed={deaf}>
            {words ? (
              <>
                <span className="composer-words-what">
                  <b>{words.name}</b>
                  <span>{deaf ? 'These words will be used' : 'Used instead of listening'}</span>
                </span>
                <button
                  className="iconbtn"
                  aria-label={`Remove ${words.name}`}
                  title="Remove"
                  onClick={() => {
                    setWords(null)
                    setFailed(null)
                    if (wordPicker.current) wordPicker.current.value = ''
                  }}
                >
                  <IconClose size={17} />
                </button>
              </>
            ) : (
              <>
                <button className="composer-attach" onClick={() => wordPicker.current?.click()}>
                  <IconType size={16} />
                  Add subtitles or a script
                </button>
                <small>
                  {deaf
                    ? 'Needed — this server cannot listen to speech yet. .srt, .vtt or .txt.'
                    : 'Optional — .srt, .vtt or .txt. Exact, where listening is a guess.'}
                </small>
              </>
            )}
            <input
              ref={wordPicker}
              type="file"
              hidden
              accept=".srt,.vtt,.txt,text/plain"
              onChange={(e) => {
                setWords(e.target.files?.[0] ?? null)
                setFailed(null)
              }}
            />
          </div>
        ) : null}


        <textarea
          className="composer-text"
          autoFocus={!takes.required || scripted}
          rows={takes.required && !scripted ? 2 : scripted ? 9 : 6}
          placeholder={
            takes.required && !scripted
              ? 'Give it a name — optional.'
              : (ASKS[spec.id] ?? 'Tell me about it…')
          }
          value={text}
          onChange={(e) => setText(e.target.value)}
        />

        {/* Attaching is a control on the composer, not a panel of its own. */}
        {scripted ? (
          <div className="composer-foot">
            <button
              className="composer-attach"
              onClick={() => picker.current?.click()}
              disabled={Boolean(file || reused)}
            >
              <IconPaperclip size={16} />
              {takes.invite}
            </button>
            {mine.length ? (
              <button
                className="composer-attach"
                onClick={() => setBrowsing(true)}
                disabled={Boolean(file || reused)}
              >
                <IconFilm size={16} />
                From your videos
              </button>
            ) : null}
            <small>{takes.hint}</small>
          </div>
        ) : takes.required ? null : (
          <div className="composer-foot">
            <button
              className="composer-attach"
              onClick={() => picker.current?.click()}
              disabled={Boolean(file)}
            >
              <IconPaperclip size={16} />
              {takes.invite}
            </button>
            <small>{takes.hint}</small>
          </div>
        )}

        <input
          ref={picker}
          type="file"
          hidden
          accept={takes.accept}
          onChange={(e) => attach(e.target.files?.[0])}
        />
      </div>

      {/*
       * Spoken in → subtitles in.
       *
       * One row, because they are one decision: leave it alone and you get subtitles in the
       * language of the video, which is what almost everyone wants. Change the right-hand one
       * and it becomes a translation — which is the feature, stated as a sentence rather than
       * hidden behind a "translate" toggle nobody would find.
       */}
      {/*
       * No language question on this lane.
       *
       * The creator answered it by typing. Burmese is Burmese by codepoint, and `detect`
       * resolves every language Lumina renders off the script itself — so a picker here is a
       * question whose answer is sitting in the box above it, and one more thing to get
       * wrong: choose English, paste Burmese, and the script is split on a full stop that
       * never appears.
       */}
      {takes.required && !scripted && langs.data ? (
        <div className="langs">
          <Pick
            label="Spoken in"
            value={spoken}
            options={langs.data}
            common={(l) => l.common_source ?? false}
            onChange={(code) => setSpoken(code)}
            auto
          />
          <span className="langs-arrow" aria-hidden="true">
            →
          </span>
          <Pick
            label={spec.id === 'dub' ? 'Dubbed in' : 'Subtitles in'}
            value={into}
            options={langs.data.filter((l) => l.can_render)}
            common={(l) => l.common_target ?? false}
            onChange={(code) => setInto(code)}
          />
        </div>
      ) : null}

      {failed ? (
        <p className="fineprint" data-bad role="alert" style={{ marginTop: 12 }}>
          {failed}
        </p>
      ) : null}

      {/*
       * Carry on from something already made.
       *
       * `design-brief.md` calls series memory the thing that should "feel automatic, not
       * configured" — episode two inheriting the voice, palette and caption style of episode
       * one. The identity kit already does that half, because it lives on the channel. What
       * was missing was any way to say *this one follows that one*, so a sequel had to be
       * described from scratch as though the first had never existed.
       *
       * Generating a video is the only task a sequel means anything for. Subtitling or
       * clipping a file has no previous episode — the video is the video.
       */}
      {spec.id === 'explainer' ? (
        <Series picked={sequelOf(text)} onPick={(title) => setText(followUp(title, text))} />
      ) : null}
    </View>
  )
}

/**
 * One language, chosen from what this server can actually do.
 *
 * A native `<select>` rather than a custom menu: it is one of the few controls a phone gives
 * a genuinely better version of, it is reachable by keyboard and screen reader with no work,
 * and it renders each language's own script without any font handling here.
 */
function Pick({
  label,
  value,
  options,
  common,
  onChange,
  auto,
}: {
  label: string
  value: string
  options: LanguageOut[]
  /** Which of them belong at the top for *this* side of the arrow. */
  common: (l: LanguageOut) => boolean
  onChange: (code: string) => void
  /**
   * Offer "work it out from the audio" above the list.
   *
   * Only ever on the spoken side: a video is in whatever language it is in, and the creator
   * may not know — or it may be in two, which is the case naming one cannot express. What
   * comes *out* is always a decision, so the target side never offers this.
   */
  auto?: boolean
}) {
  /*
   * Two groups, because the lists differ by side and the list is going to keep growing.
   * What people film in is not what they subtitle into — Chinese and Korean are common
   * sources and rare targets, Burmese and Thai the reverse — so one alphabetical list would
   * put the usual answer in a different arbitrary place on each side.
   *
   * Grouped rather than filtered: an unusual pair is still a pair somebody wants, and a
   * picker that hides it has decided for them.
   */
  const top = options.filter(common)
  const rest = options.filter((l) => !common(l))

  const name = (l: LanguageOut) =>
    // Its own name first: a Burmese creator is looking for မြန်မာ. The English name follows
    // for the creator picking a language they do not read.
    l.name === l.english ? l.name : `${l.name} · ${l.english}`

  return (
    <label className="lang">
      <span>{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {auto ? <option value={AUTO}>Detect it — including mixed</option> : null}
        {top.map((l) => (
          <option key={l.code} value={l.code}>
            {name(l)}
          </option>
        ))}
        {rest.length ? (
          <optgroup label="Also available">
            {rest.map((l) => (
              <option key={l.code} value={l.code}>
                {name(l)}
              </option>
            ))}
          </optgroup>
        ) : null}
      </select>
    </label>
  )
}

/** Once there is a plan: the outcome in one sentence, and what it costs. */
function Confirm({ s, onNext }: { s: Studio; onNext: () => void }) {
  const langs = useLanguages()
  /**
   * Whether the source column shows at all.
   *
   * Only on a translation: otherwise there is one text, and a column comparing it with itself
   * is furniture.
   */
  const compare = Boolean(s.sourceLanguage && s.sourceLanguage !== s.planLanguage)

  /**
   * Lines the creator has asked to read in English.
   *
   * Per line, not per file. Most of a track is already readable and only the odd line is in a
   * script they cannot read — this file is thirty-five English lines and six Japanese ones,
   * so a whole-file switch would translate English into English thirty-five times to help
   * with six. The tag at the start of each line is the control.
   */
  const [inEnglish, setInEnglish] = useState<ReadonlySet<string>>(() => new Set())
  const flip = (id: string) => {
    setInEnglish((was) => {
      const next = new Set(was)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
    //: Nothing stored yet, so ask — translated for reading, never burnt into the video.
    if (!s.scenes.some((x) => x.translations?.en)) s.readIn('en')
  }

  const spec = RECIPES.find((r) => r.id === s.recipe) ?? RECIPES[0]!
  const quote = useQuote(s.projectId)
  /*
   * How long the result will be, from the server.
   *
   * Summing the scenes is right for every lane that lays them end to end and wrong for the
   * one that does not: subtitling returns the video whole, while its scenes cover only the
   * moments that carry a caption. The screen showed both numbers at once — "Subtitles for 60
   * seconds of video" above "2 scenes · 5s" — and neither explained the other.
   */
  const seconds = Math.round(
    (s.durationMs || s.scenes.reduce((n, x) => n + x.durationMs, 0)) / 1000,
  )

  return (
    <View
      action={
        <>
          <div className="why">
            <span>Nothing has been made yet. Change it here if it is not what you meant.</span>
            <span className="stat-chip hot">
              <IconCoin size={14} />
              {quote.data?.credits ?? 0}
            </span>
          </div>
          <button className="btn primary block" onClick={onNext}>
            <IconSpark size={19} />
            {NEXT[spec.id] ?? 'Show me a draft'}
          </button>
        </>
      }
    >
      <Head
        kicker={spec.name}
        title={s.summary || `${s.scenes.length} scenes, about ${seconds} seconds`}
        lede={
          s.keepsWholeSource
            ? `${s.scenes.length} lines over ${seconds}s · your video comes back whole · ${quote.data?.credits ?? 0} credits`
            : `${s.scenes.length} scenes · ${seconds}s · ${quote.data?.credits ?? 0} credits to make`
        }
      />

      <div style={{ marginBottom: 22 }}>
        <Pipeline used={s.stages} state={s.progress} />
      </div>

      <div className="card pad">
        <div className="cues-head">
          <div className="label" style={{ marginTop: 0 }}>
            The script so far
          </div>
        </div>

        {/*
         * Two columns, because this screen's job is comparison.
         *
         * Stacked, the eye travels down and back for every pair and the two languages read as
         * one paragraph in two scripts. Side by side they read as what they are: the same
         * line, twice. The left is what was heard and is not editable; the right is what will
         * be burnt in, and is.
         */}
        <ol className="cues" data-compare={compare}>
          {compare ? (
            <li className="cue cue-legend" aria-hidden="true">
              <span className="cue-n" />
              <span>Original</span>
              <span>{nameOf(langs.data, s.planLanguage)}</span>
              <span className="cue-len" />
            </li>
          ) : null}
          {s.scenes.map((scene, i) => {
            //: The caption itself, in the language it is for. Falls back to the scene's own
            //: line so a project that is not a translation reads normally.
            const target = scene.translations?.[s.planLanguage] ?? scene.line
            const flipped = inEnglish.has(scene.id)
            //: What it is checked against: as spoken, or in English if this line was flipped.
            const beside = flipped ? (scene.translations?.en ?? '') : scene.line
            //: Only a line that is not already English has anything to switch to.
            const canFlip = Boolean(scene.spokenLanguage && scene.spokenLanguage !== 'en')
            return (
              <li className="cue" key={scene.id}>
                <span className="cue-n">{String(i + 1).padStart(2, '0')}</span>
                {compare ? (
                  <p
                    className="cue-ref"
                    lang={flipped ? 'en' : (scene.spokenLanguage ?? undefined)}
                  >
                    {/*
                     * The tag is the switch.
                     *
                     * It says which language this line is showing in, and pressing it swaps
                     * between as-spoken and English. Per line because that is where the need
                     * is — six lines here are Japanese and the other thirty-five are already
                     * readable.
                     */}
                    {canFlip ? (
                      <button
                        type="button"
                        className="cue-lang"
                        onClick={() => flip(scene.id)}
                        aria-pressed={flipped}
                        title={flipped ? 'Show it as spoken' : 'Show it in English'}
                      >
                        {flipped ? 'EN' : (scene.spokenLanguage ?? '').toUpperCase()}
                      </button>
                    ) : null}
                    {beside || <span className="muted">translating…</span>}
                  </p>
                ) : null}
                {/*
                 * Editable, because this is the screen where the mistake is first visible.
                 *
                 * A machine translation is a draft, and the creator is the only person who can
                 * tell that a line came out fluent and wrong. Saved on blur rather than per
                 * keystroke: one edit is one intention.
                 */}
                <Editable
                  className="cue-target"
                  lang={s.planLanguage}
                  value={target}
                  onCommit={(text) => s.setCaptionText(scene.id, text)}
                />
                <span className="cue-len">{scene.time}</span>
              </li>
            )
          })}
        </ol>
      </div>
    </View>
  )
}

/**
 * One caption, editable in place.
 *
 * A textarea that grows to fit rather than an input: captions wrap, and a box that scrolls a
 * two-line caption out of sight hides the thing being checked.
 *
 * Committed on blur and on Enter, abandoned on Escape — so a half-typed correction never
 * reaches the server, and one edit is one request rather than one per keystroke.
 */
function Editable({
  value,
  onCommit,
  className,
  lang,
}: {
  value: string
  onCommit: (text: string) => void
  className?: string
  lang?: string
}) {
  const [draft, setDraft] = useState(value)
  const box = useRef<HTMLTextAreaElement>(null)

  //: Follow the server when it changes underneath — a translation landing, or the same line
  //: edited on the other screen — but never while the creator is part way through typing.
  useEffect(() => {
    if (document.activeElement !== box.current) setDraft(value)
  }, [value])

  //: Height follows content, so a wrapped caption is fully visible while it is being fixed.
  useEffect(() => {
    const el = box.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
  }, [draft])

  return (
    <textarea
      ref={box}
      className={className}
      lang={lang}
      rows={1}
      value={draft}
      spellCheck={false}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        const text = draft.trim()
        if (text && text !== value) onCommit(text)
        else setDraft(value)
      }}
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          setDraft(value)
          box.current?.blur()
        }
        //: Enter commits; Shift+Enter is a line break the creator asked for.
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault()
          box.current?.blur()
        }
      }}
    />
  )
}

function nameOf(langs: LanguageOut[] | undefined, code: string): string {
  return langs?.find((l) => l.code === code)?.english ?? code
}

/**
 * "Work it out from the audio" — not a language, and deliberately not one.
 *
 * A file is not always in a single language: a Japanese video with an English song over it is
 * two, and naming either one mistranscribes the other. Must match `transcribe.AUTO` on the
 * server, which is where the decision actually lands.
 */
const AUTO = 'auto'

/**
 * Every video already uploaded, searchable.
 *
 * A sheet rather than a row of chips, because the row only works while the list is short: a
 * creator with a thousand videos cannot pick from the six most recent, and the one they want
 * is rarely the newest. Search over the name, which is the only thing they know it by.
 */
function Library({
  videos,
  onPick,
  onClose,
}: {
  videos: Reusable[]
  onPick: (v: Reusable) => void
  onClose: () => void
}) {
  const [find, setFind] = useState('')
  const hits = useMemo(() => {
    const needle = find.trim().toLowerCase()
    return needle ? videos.filter((v) => v.title.toLowerCase().includes(needle)) : videos
  }, [videos, find])

  /*
   * Rendered on the body, not where it is written.
   *
   * `.view` is `position: relative; z-index: 1`, which makes it a stacking context — so a
   * sheet rendered inside it is trapped there whatever its own `z-index` says, and the top
   * bar at `z-index: 3` drew straight over it, close button and all.
   */
  return createPortal(
    <>
      <div className="scrim" onClick={onClose} />
      <div className="sheet library" role="dialog" aria-label="Your videos">
        <div className="grab" />
        <div className="library-head">
          <b>From your videos</b>
          <button className="iconbtn" onClick={onClose} aria-label="Close" title="Close">
            <IconClose size={17} />
          </button>
        </div>

        <input
          className="library-find"
          value={find}
          autoFocus
          placeholder={`Search ${videos.length} videos`}
          onChange={(e) => setFind(e.target.value)}
        />

        <div className="library-list">
          {hits.map((v) => (
            <button key={v.assetId} className="library-item" onClick={() => onPick(v)}>
              <b>{v.title}</b>
              <small>{v.length}</small>
            </button>
          ))}
          {!hits.length ? <p className="muted">Nothing matches that.</p> : null}
        </div>
      </div>
    </>,
    document.body,
  )
}

/** One video already uploaded, as the picker needs it. */
type Reusable = { assetId: string; title: string; length: string }

/** Minutes and seconds, for a label that has to fit on a chip. */
function clock(ms: number): string {
  const total = Math.round(ms / 1000)
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

/** The heading, once the task is known. */
const TITLES: Record<string, string> = {
  explainer: 'What should it be about?',
  dub: 'Which video shall I dub?',
  narrate: 'What should it say?',
  clip_long_video: 'Which video shall I cut down?',
  subtitle_only: 'Which video shall I subtitle?',
}

/** What the box is for. */
const ASKS: Record<string, string> = {
  explainer: 'Describe it, paste a link, or drop an image to build it around.',
  dub: 'Drop the video you want dubbed.',
  narrate: 'Paste your script. It will be read out, word for word.',
  clip_long_video: 'Drop the long video you want cut down.',
  subtitle_only: 'Drop the video you want subtitled.',
}

/** The button, while it is still waiting. */
const WAITING: Record<string, string> = {
  explainer: 'Describe it first',
  dub: 'Drop your video first',
  narrate: 'Choose the footage first',
  clip_long_video: 'Drop your video first',
  subtitle_only: 'Drop your video first',
}

/** The button, once there is a plan. */
const NEXT: Record<string, string> = {
  clip_long_video: 'See the moments',
  subtitle_only: 'Check the words',
  dub: 'Check the words',
  narrate: 'Check the words',
}

/**
 * Recent videos, to carry on from.
 *
 * Shown here rather than behind a menu because the moment to think "this is episode two" is
 * while writing the brief, not before choosing a task or after planning.
 *
 * It was a scrolling row of grey boxes, each labelled with its lane — which is the same word
 * on every one of them, so the only thing distinguishing three episodes of a series was a
 * truncated title. Now each one shows its opening frame and when it was made, which is what
 * a person actually recognises a past video by, and there is no scrollbar: four fit.
 */
function Series({ picked, onPick }: { picked: string | null; onPick: (title: string) => void }) {
  const projects = useProjects()
  const recent = (projects.data ?? []).filter((p) => p.current_plan_id).slice(0, 4)
  if (!recent.length) return null

  return (
    <div className="series">
      <div className="label">Following on from something?</div>
      <div className="series-row">
        {recent.map((p) => {
          const Art = RECIPE_ART[p.recipe] ?? RECIPE_ART.explainer!
          const on = picked === p.title
          return (
            <button
              key={p.id}
              className="series-card"
              data-on={on}
              aria-pressed={on}
              onClick={() => onPick(p.title)}
            >
              <span className="series-poster">
                {p.poster_asset_id ? (
                  <img src={assetUrl(p.poster_asset_id)} alt="" loading="lazy" />
                ) : (
                  // Nothing drawn yet — the lane's own diagram rather than an empty grey box.
                  <Art />
                )}
              </span>
              <span className="series-what">
                <b>{p.title}</b>
                <small>{ago(p.created_at)}</small>
              </span>
              {on ? (
                <span className="series-tick">
                  <IconCheck size={15} />
                </span>
              ) : null}
            </button>
          )
        })}
      </div>
    </div>
  )
}

/** How long ago, in the unit a person would say it in. */
function ago(iso: string): string {
  const mins = Math.max(0, (Date.now() - new Date(iso).getTime()) / 60000)
  if (mins < 1) return 'Just now'
  if (mins < 60) return `${Math.floor(mins)} min ago`
  const hours = mins / 60
  if (hours < 24) return `${Math.floor(hours)} hr ago`
  const days = Math.floor(hours / 24)
  if (days === 1) return 'Yesterday'
  if (days < 7) return `${days} days ago`
  if (days < 30) return `${Math.floor(days / 7)} wk ago`
  return new Date(iso).toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
  })
}

/**
 * Turn a previous title into the opening of a brief.
 *
 * Written into the box rather than sent as hidden context, so the creator can see exactly what
 * the planner will be told and change it. A sequel where the link is invisible is one they
 * cannot correct when it goes wrong.
 */
function followUp(title: string, existing: string): string {
  const line = `A follow-up to "${title}". `
  const rest = existing.replace(SEQUEL, '')
  // Clicking the one already chosen unpicks it, so a mis-click is undoable without editing
  // the brief by hand.
  return sequelOf(existing) === title ? rest : line + rest
}

/** Which past video the brief currently says it follows on from, if any. */
const SEQUEL = /^A follow-up to "([^"]*)"\. /

function sequelOf(text: string): string | null {
  return SEQUEL.exec(text)?.[1] ?? null
}

/**
 * A look at what was attached.
 *
 * An object URL rather than a data URL: the file can be a gigabyte, and reading it into a
 * base64 string to show a 64-pixel square would freeze the tab. Revoked when it goes away,
 * because each one holds the whole file in memory until it is.
 */
function Thumb({ file }: { file: File }) {
  const [url, setUrl] = useState<string | null>(null)

  useEffect(() => {
    if (!file.type.startsWith('image/') && !file.type.startsWith('video/')) return
    const made = URL.createObjectURL(file)
    setUrl(made)
    return () => {
      URL.revokeObjectURL(made)
      setUrl(null)
    }
  }, [file])

  if (url && file.type.startsWith('video/')) {
    // Muted and preloading metadata only: this is a thumbnail, not a player, and the real
    // one is two screens away.
    return <video className="thumb" src={url} muted preload="metadata" />
  }
  if (url) return <img className="thumb" src={url} alt="" />
  return (
    <span className="thumb thumb-none">
      <IconPaperclip size={18} />
    </span>
  )
}

/** Size in the unit a person would say it in. */
function describe(file: File): string {
  const mb = file.size / 1024 / 1024
  const size = mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.max(1, Math.round(file.size / 1024))} KB`
  const kind = file.type.split('/')[0]
  return `${kind === 'image' ? 'Image' : kind === 'video' ? 'Video' : kind === 'audio' ? 'Audio' : 'File'} · ${size}`
}
