/**
 * The live view model.
 *
 * Presents the same shape `useStudio` did, so the sixteen screens did not need rewriting —
 * but every field behind it is the real API now. That equivalence is the point: the mock was
 * built against the backend's Plan/Scene model precisely so this swap would be a data change
 * rather than a redesign.
 *
 * Three things this deliberately does not do:
 *
 *   - It does not fall back to mock rows when a request fails. Fake data in the real app is
 *     how you ship a demo that does not work; a failed load says it failed.
 *   - It does not keep its own copy of server state. A job that takes minutes can change a
 *     scene while the user is looking at it, so anything the server owns is read through
 *     TanStack Query and invalidated after a mutation.
 *   - It does not assume a mutation succeeded. Jobs outlive the tab: an action enqueues, and
 *     the next read tells the truth.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { useQueryClient } from '@tanstack/react-query'

import type { RenderOut, SceneOut } from '@lumina/client'

import { assetUrl, setToken } from '../api/client'
import { messageOf } from '../api/errors'
import { shortfallOf, type Shortfall } from '../api/errors'
import {
  useAddScene,
  useCredits,
  useDeleteScene,
  useDraft,
  useEditScene,
  useShiftCues,
  useTracks,
  usePlan,
  useChannel,
  useEditChannel,
  useUpload,
  useProjects,
  useRecipes,
  useRegenerate,
  useRenders,
  useSession,
  useStartRender,
  useUpgrade,
} from '../api/queries'
import { screenFor, stepsFor } from './data'
import type { Beat, Scene, SceneStatus, Screen } from './data'

/** Which project was open, and which screen. Both survive a reload. */
const PROJECT_KEY = 'lumina.project'
const SCREEN_KEY = 'lumina.screen'

function remembered(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    // Private windows and blocked site data both throw. Nothing is kept; nothing breaks.
    return null
  }
}

/**
 * Screens that mean nothing without a project.
 *
 * Restoring one of these with no project open would show an editor with no video in it, so
 * they fall back to Start. Everything else — the project list, the identity kit, credits —
 * stands on its own and can be returned to directly.
 */
const NEEDS_PROJECT = new Set<Screen>([
  'recipe',
  'script',
  'board',
  'scene',
  'preview',
  'clips',
  'captions',
  'deliver',
])

/**
 * Where to open.
 *
 * The screen you were on, if there is one to go back to. A reload in the middle of a
 * forty-cue subtitle pass should not cost you your place — jobs run for minutes and
 * `design-brief.md` makes surviving a closed tab a hard requirement, which is worth very
 * little if reopening dumps you somewhere else.
 *
 * A first visit, or a remembered screen whose project is gone, opens on Start: the one screen
 * that is right when there is no context to return to.
 */
function opening(): Screen {
  const screen = remembered(SCREEN_KEY) as Screen | null
  if (!screen || screen === 'auth') return 'start'
  if (NEEDS_PROJECT.has(screen) && !remembered(PROJECT_KEY)) return 'start'
  return screen
}

/** A neutral plate for a scene whose picture has not landed yet. */
const PENDING = 'linear-gradient(150deg,#1b2350,#0b0f22 72%)'

/** The tier a "finish this scene" upgrade buys. Never implicit — the user picks the scenes. */
const UPGRADE_TIER = 'mid_video'

function secondsOf(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`
}

/**
 * One API scene, as the storyboard sees it.
 *
 * `media` is a CSS background-image value because that is what the card renders. A finished
 * scene shows its final asset and an unfinished one its preview — never the other way round,
 * since the final never overwrote the preview it was upgraded from.
 */
function toScene(row: SceneOut, selected: boolean): Scene {
  const asset = row.final_asset_id ?? row.preview_asset_id
  return {
    id: row.id,
    line: row.script_line,
    //: Which language this line is actually in. A file may change language part way through,
    //: so the project's one `sourceLanguage` is only the commonest answer.
    spokenLanguage: row.spoken_language ?? null,
    caption: row.caption ?? '',
    //: The same line in the other burnt-in languages. Empty for a single-language video.
    translations: row.translations ?? {},
    time: secondsOf(row.duration_ms),
    durationMs: row.duration_ms,
    hasVoice: Boolean(row.vo_asset_id),
    sourceStartMs: row.source_start_ms ?? null,
    media: asset ? `url("${assetUrl(asset)}")` : PENDING,
    //: What this beat's picture actually is. A still is drawn, a clip is played — and the
    //: asset URL says nothing either way, so the server tells us.
    pictureUrl: asset ? assetUrl(asset) : null,
    pictureIsClip: row.picture_is_clip ?? false,
    prompt: row.prompt,
    pace: 1,
    emphasis: [],
    status: (row.state === 'final' ? 'final' : 'preview') satisfies SceneStatus,
    selected,
    // Both in-flight states, so the card spins for an upgrade exactly as it does for a
    // first draft. The user does not distinguish them; they are both "this one is working".
    busy: row.state === 'previewing' || row.state === 'upgrading',
    loaded: Boolean(asset),
  }
}

/**
 * A plan's scenes as script beats.
 *
 * The labels follow the shape of nearly every short — first line is the hook, last is the
 * call to action, the one before it is the payoff — which is the same rule the server's
 * `split_beats` uses. Kept in step deliberately: two different answers to "which beat is
 * this" would show one label here and another in the timing panel.
 */
function beatsOf(scenes: SceneOut[]): Beat[] {
  const n = scenes.length
  return scenes.map((row, i) => ({
    id: i + 1,
    sceneId: row.id,
    kind:
      i === 0
        ? 'hook'
        : i === 1 && n >= 4
          ? 'setup'
          : i === n - 1 && n >= 3
            ? 'cta'
            : i === n - 2 && n >= 5
              ? 'payoff'
              : 'turn',
    text: row.script_line,
  }))
}

/** The full pipeline, for before the server's answer arrives. */
/**
 * What to draw before the server has said which stages this lane runs.
 *
 * Nothing. This was Explainer's five stages, used as the fallback for every lane, so during
 * the moment before `/projects/recipes` lands a Dub project drew PLAN and PICTURE — two
 * stages it never runs — and a creator watching for progress saw a pipeline belonging to
 * someone else's video. An empty strip says "not known yet"; a wrong one says something
 * false, and says it confidently.
 */
const DEFAULT_STAGES: readonly string[] = []

export type StageState = 'todo' | 'now' | 'done'

/**
 * Stage progress, read off the work itself.
 *
 * `voice` and `captions` have no per-scene flag of their own, so they are inferred from what
 * they produce — narration attaches to the scenes, and captions only exist once a render has
 * been asked for. Inferring is better than a separate counter that can drift, and honest
 * about the fact that these are whole-plan stages rather than per-scene ones.
 */
function progressOf(
  scenes: Scene[],
  renders: RenderOut[] | undefined,
): Record<string, StageState> {
  const withPicture = scenes.filter((x) => x.loaded).length
  const working = scenes.some((x) => x.busy)
  const rendering = (renders ?? []).some((r) => r.status !== 'ready')
  const rendered = (renders ?? []).some((r) => r.status === 'ready')

  return {
    /*
     * Both already finished by the time there is anything to draw.
     *
     * A plan exists only because a file was taken in and listened to — the transcript is
     * what it was made from. They were missing from this map entirely, so the two stages a
     * subtitling creator actually waits through were the two that never lit up.
     */
    ingest: scenes.length ? 'done' : 'todo',
    transcribe: scenes.length ? 'done' : 'todo',
    plan: scenes.length ? 'done' : 'todo',
    generate: working ? 'now' : withPicture && withPicture === scenes.length ? 'done' : 'todo',
    voice: scenes.some((x) => x.hasVoice) ? 'done' : working ? 'now' : 'todo',
    /*
     * In-flight beats finished.
     *
     * These asked "is any render ready" first, so the moment the 9:16 landed the strip went
     * green — while the 16:9 was still being made and the screen beside it said "rendering".
     * A stage is done when there is nothing left working on it, not when the first one lands.
     */
    captions: rendering ? 'now' : rendered ? 'done' : 'todo',
    compose: rendering ? 'now' : rendered ? 'done' : 'todo',
  }
}

/** Render status per aspect, in the shape the Deliver screen already draws. */
function formatsOf(rows: RenderOut[] | undefined): Record<string, 'idle' | 'working' | 'ready'> {
  const out: Record<string, 'idle' | 'working' | 'ready'> = {
    '9:16': 'idle',
    '1:1': 'idle',
    '16:9': 'idle',
  }
  for (const row of rows ?? []) {
    // Newest first from the API, so an earlier row must not overwrite a later one.
    if (out[row.aspect] === 'idle') out[row.aspect] = row.status === 'ready' ? 'ready' : 'working'
  }
  return out
}

export function useLiveStudio() {
  const [screen, setScreen] = useState<Screen>(opening)
  const [history, setHistory] = useState<Screen[]>([])
  //: How many history entries this session pushed. Zero means the browser has nothing of ours
  //: to go back to — a reload straight onto a deep screen — and `back` must not call
  //: `history.back()`, which would leave the site.
  const pushes = useRef(0)
  const [projectId, setProjectIdRaw] = useState<string | null>(() => remembered(PROJECT_KEY))
  const [openScene, setOpenScene] = useState<string | null>(null)
  const [chosen, setRecipe] = useState('explainer')
  const [seriesMemory, setSeriesMemory] = useState(true)
  const [beats, setBeats] = useState<Beat[]>([])
  /** The plan the beats were seeded from, so an edit is never clobbered by a refetch. */
  const seeded = useRef<string | null>(null)
  /** Which scenes are marked to finish. Local: the server has no opinion about a selection. */
  const [picked, setPicked] = useState<ReadonlySet<string>>(() => new Set())
  /** The last "not enough credits", so a screen can say how far short rather than "failed". */
  const [shortfall, setShortfall] = useState<Shortfall | null>(null)

  const qc = useQueryClient()
  const session = useSession()
  const projects = useProjects()
  //: Loaded before the lane is worked out, because it is what answers that question.
  const plan = usePlan(projectId)

  /**
   * Which lane this session is in.
   *
   * The *project's* answer whenever there is a project, and only otherwise the one picked on
   * the Start screen. It used to be local state alone, so a page reload reset it to
   * "explainer" while the loaded project was still a subtitle job — and since the funnel is
   * chosen by lane, the creator was sent to the Script screen of a lane they were not in,
   * showing their subtitle lines as beats to rewrite.
   *
   * The *plan* is asked first, and that ordering is the fix for the second version of the
   * same bug. Reading only the project list meant the lane was unknown until a request that
   * has nothing to do with the open project came back — and until it did, `chosen` answered
   * "explainer" for every project on a fresh load. A Dub project therefore rendered as the
   * Subtitle editor, headed "Subtitles", offering to burn in captions, with no voice
   * controls, for as long as a list of every project on the channel took to arrive. The plan
   * is the request this screen is already waiting on, so its answer arrives with the words
   * it describes.
   */
  const recipe = useMemo(
    () =>
      (projectId ? plan.data?.recipe : null) ??
      projects.data?.find((p) => p.id === projectId)?.recipe ??
      chosen,
    [plan.data, projects.data, projectId, chosen],
  )

  //: The remembered screen is restored before the project has loaded, so it can be a step
  //: from a different lane. Corrected once the lane is known rather than left stranded.
  useEffect(() => {
    setScreen((was) => screenFor(was, recipe))
  }, [recipe])
  const credits = useCredits()
  const renders = useRenders(projectId)
  const lanes = useRecipes()
  const channel = useChannel()
  const editChannel = useEditChannel()
  const uploadAsset = useUpload()

  const addSceneMutation = useAddScene(projectId)
  const deleteSceneMutation = useDeleteScene(projectId)
  /**
   * Language and caption style live on the channel, not in component state.
   *
   * This is the identity kit — the whole promise is that setting it once makes episode two
   * inherit from episode one. A `useState` here would be a settings screen that appears to
   * save and forgets on reload, which is worse than not offering the control.
   */
  const language = channel.data?.language ?? 'en'
  const captionStyle = String(channel.data?.identity?.caption_style ?? 'pop')
  /**
   * Whose mark goes on the finished video.
   *
   * `lumina` by default — a channel that has said nothing gets ours. `own` uses the logo on
   * this kit; `none` leaves the frame clean. The render honours whatever is set here, so the
   * paywall belongs in front of *this control*, not inside the render.
   */
  const watermark = String(channel.data?.identity?.watermark ?? 'lumina')
  const logoAssetId = channel.data?.logo_asset_id ?? null
  const voiceReady = Boolean(channel.data?.voice_profile_asset_id)

  const setLanguage = useCallback(
    (code: string) => editChannel.mutate({ language: code }),
    [editChannel],
  )
  const setWatermark = useCallback(
    (choice: 'lumina' | 'own' | 'none') =>
      //: Merged server-side into the existing document, so choosing a mark cannot drop the
      //: caption style sitting beside it.
      editChannel.mutate({ identity: { watermark: choice } }),
    [editChannel],
  )
  /**
   * Upload a logo and select it in one action.
   *
   * Two requests, one intention: a creator who picks a file has already decided they want it
   * used, and making them press a second button to say so would be a form rather than a
   * choice.
   */
  const setLogo = useCallback(
    async (file: File) => {
      const asset = await uploadAsset.mutateAsync(file)
      editChannel.mutate({ logo_asset_id: asset.asset_id, identity: { watermark: 'own' } })
    },
    [uploadAsset, editChannel],
  )
  const setCaptionStyle = useCallback(
    (id: string) => editChannel.mutate({ identity: { caption_style: id } }),
    [editChannel],
  )
  /**
   * Where captions sit, as a share of the frame up from the bottom.
   *
   * On the identity kit rather than the project, alongside the style: it is part of how a
   * channel's videos look, and a creator who moves the captions off their face once means it
   * for every episode. `design-brief.md` calls that inheritance the point of the kit.
   */
  /**
   * Words that must survive translation exactly as written.
   *
   * "Computer" in a Burmese sentence stays "Computer" — that is how it is said, and the
   * dictionary equivalent reads as stilted or plain wrong. Product names, brands, acronyms
   * and most technical vocabulary behave the same way in every language here.
   *
   * On the identity kit rather than the project because a creator's technical vocabulary is
   * stable across everything they make: re-typing it per video is not a thing anyone will do.
   */
  const glossary = useMemo(() => {
    const raw = channel.data?.identity?.glossary
    return Array.isArray(raw) ? raw.map(String).filter(Boolean) : []
  }, [channel.data?.identity?.glossary])

  const setGlossary = useCallback(
    (terms: string[]) => {
      // Deduplicated case-insensitively: the list goes into every translation prompt, and
      // "Computer" twice is a wasted instruction rather than a stronger one.
      const seen = new Map<string, string>()
      for (const t of terms.map((x) => x.trim()).filter(Boolean)) {
        if (!seen.has(t.toLowerCase())) seen.set(t.toLowerCase(), t)
      }
      editChannel.mutate({ identity: { glossary: [...seen.values()].slice(0, 200) } })
    },
    [editChannel],
  )

  const captionBottom = Number(channel.data?.identity?.caption_bottom ?? 0.16)
  //: Caption size as a multiplier on the default (5% of the frame's short edge).
  const captionScale = Number(channel.data?.identity?.caption_scale ?? 1)
  /*
   * Whether captions highlight word by word, or hold each line whole.
   *
   * On the identity kit rather than per render: it is a property of how the channel looks,
   * the same as the style and the size, and a creator who wants still captions wants them on
   * every video rather than remembering it at each export.
   */
  const captionKaraoke = channel.data?.identity?.caption_karaoke !== false
  const setCaptionKaraoke = useCallback(
    (on: boolean) => editChannel.mutate({ identity: { caption_karaoke: on } }),
    [editChannel],
  )
  const setCaptionScale = useCallback(
    (v: number) =>
      editChannel.mutate({
        identity: { caption_scale: Math.min(1.8, Math.max(0.6, Number(v.toFixed(2)))) },
      }),
    [editChannel],
  )
  const setCaptionBottom = useCallback(
    (v: number) =>
      editChannel.mutate({
        identity: { caption_bottom: Math.min(0.85, Math.max(0.02, Number(v.toFixed(3)))) },
      }),
    [editChannel],
  )

  const editScene = useEditScene(projectId)
  const shift = useShiftCues(projectId)
  const tracks = useTracks(projectId)
  const regenerate = useRegenerate(projectId)
  const upgradeScene = useUpgrade(projectId)
  const draft = useDraft(projectId)
  const startRender = useStartRender(projectId)

  const scenes = useMemo(
    () => (plan.data?.scenes ?? []).map((row) => toScene(row, picked.has(row.id))),
    [plan.data, picked],
  )

  /** Which stages this recipe runs, from the server rather than a table that can drift. */
  const stages = useMemo(
    () => lanes.data?.find((l) => l.id === recipe)?.stages ?? DEFAULT_STAGES,
    [lanes.data, recipe],
  )

  /**
   * How far the work has got, per stage.
   *
   * Derived from the scenes and the renders rather than tracked separately: a progress
   * indicator with its own state is a progress indicator that can disagree with the thing it
   * is describing, and this one is read at a glance while jobs are landing.
   */
  const progress = useMemo(
    () => progressOf(scenes, renders.data),
    [scenes, renders.data],
  )

  /**
   * Start the preview tier as soon as there is a plan to run it on.
   *
   * "Every scene is first generated cheaply — the user sees the whole video in seconds, for
   * almost nothing." That is the product's central mechanic, and it is not something to make
   * the creator ask for: landing on a storyboard of empty cards with a button labelled
   * "generate" puts a decision in front of them that has already been made.
   *
   * Guarded by plan id rather than a boolean, so switching projects drafts the new one and
   * re-rendering never re-enqueues. The enqueue is idempotent server-side too, but relying on
   * that alone would send a request per render.
   */
  const drafted = useRef<string | null>(null)
  const currentPlan = plan.data
  useEffect(() => {
    if (!currentPlan || drafted.current === currentPlan.id) return
    const untouched = currentPlan.scenes.every(
      (row) => row.state === 'draft' && !row.preview_asset_id,
    )
    if (!untouched) return
    drafted.current = currentPlan.id
    draft.mutate(undefined, { onError: (err) => setShortfall(shortfallOf(err)) })
  }, [currentPlan, draft])

  /**
   * Seed the beats from the plan, once.
   *
   * Once, and not on every refetch: the plan query polls while scenes are landing, and
   * re-deriving the beats each time would wipe whatever the creator was in the middle of
   * typing. They are seeded when the plan first arrives and are theirs from then on.
   */
  useEffect(() => {
    if (!currentPlan || seeded.current === currentPlan.id) return
    seeded.current = currentPlan.id
    setBeats(beatsOf(currentPlan.scenes))
  }, [currentPlan])

  /** Push the edited script back onto the scenes it came from. */
  const saveBeats = useCallback(() => {
    for (const beat of beats) {
      if (!beat.sceneId) continue
      const scene = currentPlan?.scenes.find((row) => row.id === beat.sceneId)
      if (scene && scene.script_line !== beat.text) {
        editScene.mutate({ sceneId: beat.sceneId, patch: { script_line: beat.text } })
      }
    }
  }, [beats, currentPlan, editScene])

  /**
   * Remember which project is open, across reloads.
   *
   * "The app must survive a closed tab" is a hard requirement in `design-brief.md`, and jobs
   * run for minutes — so closing the tab while a draft renders is the *expected* behaviour,
   * not an edge case. The id is kept so the storyboard, the editor and Deliver all resolve to
   * the right project when they are opened; the app no longer navigates into it by itself.
   */
  const setProjectId = useCallback((id: string | null) => {
    setProjectIdRaw(id)
    try {
      if (id) localStorage.setItem(PROJECT_KEY, id)
      else localStorage.removeItem(PROJECT_KEY)
    } catch {
      /* private windows and blocked site data both throw; the project is simply not kept */
    }
  }, [])

  const go = useCallback((next: Screen) => {
    try {
      localStorage.setItem(SCREEN_KEY, next)
    } catch {
      /* nothing is kept; the app still works, it just reopens on Start */
    }
    setScreen((current) => {
      setHistory((h) => [...h, current])
      return next
    })
    /*
     * A real history entry per screen.
     *
     * The app kept its own stack and never touched the browser's, so the back button did what
     * it does on a page with one entry: it left the site. A creator three screens into their
     * video pressed back and landed on whatever they had been reading before — which reads as
     * the app throwing their work away.
     *
     * Pushed rather than replaced, so forward works too.
     */
    try {
      window.history.pushState({ screen: next }, '')
      pushes.current += 1
    } catch {
      /* a sandboxed frame can refuse; in-app navigation still works */
    }
  }, [])

  const back = useCallback(() => {
    /*
     * Through the browser when this session put something there, so its back button and the
     * app's are one action rather than two stacks that drift apart.
     *
     * Not when it did not: a reload lands straight on a remembered screen with no entries of
     * ours behind it, and `history.back()` would then take the creator off the site — which is
     * exactly the complaint this is fixing, only from the app's own button.
     */
    if (pushes.current > 0) {
      //: Not decremented here — `popstate` does it, and that fires for the browser's own back
      //: button too. Counting in both places would drift the moment a creator used either.
      window.history.back()
      return
    }
    setHistory((h) => {
      //: Nothing of ours in the browser and nothing on our own stack: fall back a step in the
      //: funnel, which is where "back" means something on a screen reached by reloading.
      const steps = stepsFor(recipe)
      const at = steps.indexOf(screen)
      const previous = h[h.length - 1] ?? (at > 0 ? steps[at - 1] : undefined)
      if (previous) setScreen(previous)
      return h.slice(0, -1)
    })
  }, [recipe, screen])

  /*
   * The browser's back and forward, driving the same screen state.
   *
   * The entry pushed by `go` carries the screen it moved to; an entry from before the app
   * loaded carries nothing, and the app's own stack answers instead.
   */
  useEffect(() => {
    const onPop = (e: PopStateEvent) => {
      pushes.current = Math.max(0, pushes.current - 1)
      const to = (e.state as { screen?: Screen } | null)?.screen
      setHistory((h) => {
        const previous = to ?? h[h.length - 1]
        if (previous) {
          setScreen(previous)
          try {
            localStorage.setItem(SCREEN_KEY, previous)
          } catch {
            /* nothing is kept */
          }
        }
        return to ? h : h.slice(0, -1)
      })
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  //: Label the entry the app opened on, so going forward back into it restores the screen
  //: rather than falling through to the app's own stack.
  useEffect(() => {
    try {
      window.history.replaceState({ screen: opening() }, '')
    } catch {
      /* a sandboxed frame can refuse */
    }
  }, [])

  const toggleScene = useCallback(
    (id: string) =>
      setPicked((current) => {
        const next = new Set(current)
        if (next.has(id)) next.delete(id)
        else next.add(id)
        return next
      }),
    [],
  )

  /**
   * Redo one scene.
   *
   * Fires and returns. The scene's own state moves to `previewing` server-side and the plan
   * query polls until it settles, which is what makes progress per-scene rather than one
   * global spinner.
   */
  const regen = useCallback(
    (id: string) => {
      regenerate.mutate(
        { sceneId: id },
        { onError: (err) => setShortfall(shortfallOf(err)) },
      )
    },
    [regenerate],
  )

  /** Promote the marked scenes to real video generation. The upsell, per scene. */
  const upgrade = useCallback(() => {
    for (const id of picked) {
      upgradeScene.mutate(
        { sceneId: id, tier: UPGRADE_TIER },
        { onError: (err) => setShortfall(shortfallOf(err)) },
      )
    }
    setPicked(new Set())
  }, [picked, upgradeScene])

  /**
   * Render one shape, or several.
   *
   * Several go in one request because the endpoint already takes a list. Calling a single
   * `useMutation` three times in a loop fires three requests whose pending and error state
   * all land on the same hook — so two of the three answers are unobservable, and a partial
   * failure looks exactly like a success.
   */
  const render = useCallback(
    (aspect: string | string[]) => {
      startRender.mutate({
        aspects: Array.isArray(aspect) ? aspect : [aspect],
        caption_style: captionStyle,
        caption_bottom: captionBottom,
        caption_scale: captionScale,
        caption_karaoke: captionKaraoke,
      })
    },
    [startRender, captionStyle, captionBottom, captionScale, captionKaraoke],
  )

  /** Add an empty card. The user fills it in; demanding a line up front makes this a form. */
  const addScene = useCallback(() => {
    const planId = plan.data?.id
    if (planId) addSceneMutation.mutate({ planId })
  }, [plan.data?.id, addSceneMutation])

  const removeScene = useCallback(
    (id: string) => deleteSceneMutation.mutate({ sceneId: id }),
    [deleteSceneMutation],
  )

  /**
   * Add a caption between two existing ones.
   *
   * The new line takes the *gap* between its neighbours — that is the only span it can have
   * without pushing something else out of sync, and it is what "put a line here" means when
   * every cue is pinned to a moment in the video. Where there is no gap it borrows a second
   * from the end of the line before, which is preferable to refusing: a creator who wants a
   * line there wants a line there, and the edges are draggable afterwards.
   *
   * `after` is the index to insert behind, or -1 for the very start.
   */
  const insertCue = useCallback(
    (after: number) => {
      const planId = plan.data?.id
      if (!planId) return
      const rows = plan.data?.scenes ?? []
      const before = rows[after]
      const next = rows[after + 1]

      const opens = before ? (before.source_start_ms ?? 0) + before.duration_ms : 0
      const closes = next?.source_start_ms ?? opens + 2000
      //: A second borrowed from the previous line when the cues already touch.
      const gap = closes - opens
      const startMs = gap >= 400 ? opens : Math.max(0, closes - 1000)
      const durationMs = Math.max(400, Math.min(gap > 0 ? gap : 1000, 6000))

      addSceneMutation.mutate({
        planId,
        index: after + 1,
        duration_ms: durationMs,
        source_start_ms: startMs,
      })
    },
    [plan.data?.id, plan.data?.scenes, addSceneMutation],
  )

  const setPrompt = useCallback(
    (id: string, prompt: string) => editScene.mutate({ sceneId: id, patch: { prompt } }),
    [editScene],
  )
  const setLine = useCallback(
    (id: string, line: string) => editScene.mutate({ sceneId: id, patch: { script_line: line } }),
    [editScene],
  )
  /**
   * Correct the caption itself, in whichever language it is written in.
   *
   * One action rather than two, because from the creator's side it is one thing: they are
   * fixing the words that will be burnt into the video. Where those words *live* differs —
   * a translation is a scene's `translations` entry and an untranslated caption is its own
   * `script_line` — and that is a storage detail they should never have to know.
   */
  const setCaptionText = useCallback(
    (id: string, text: string) => {
      const spoken = plan.data?.source_language
      const target = plan.data?.language ?? language
      editScene.mutate({
        sceneId: id,
        patch:
          spoken && spoken !== target
            ? { translations: { [target]: text } }
            : { script_line: text },
      })
    },
    [editScene, plan.data?.source_language, plan.data?.language, language],
  )
  /**
   * When a cue appears and how long it stays.
   *
   * Both in one call because they are one edit: dragging a cue's end changes its duration,
   * dragging its start changes both, and sending them separately makes the list flicker
   * through a state the creator never asked for.
   */
  const setCueTime = useCallback(
    (id: string, startMs: number, durationMs: number) =>
      editScene.mutate({
        sceneId: id,
        patch: {
          source_start_ms: Math.max(0, Math.round(startMs)),
          duration_ms: Math.max(200, Math.round(durationMs)),
        },
      }),
    [editScene],
  )
  /**
   * Turn a burnt-in caption language on or off.
   *
   * Adding one translates every line into it; removing one keeps the translations, so putting
   * a track back does not pay for the same words twice.
   */
  const setTrack = useCallback(
    (language: string, on: boolean) => {
      if (projectId) tracks.mutate({ projectId, language, on })
    },
    [tracks, projectId],
  )
  /**
   * Translate a language for *reading*, not for burning in.
   *
   * The editor shows one reference line beside each caption so a creator can check the
   * translation. When the video was spoken in a language they do not read — Chinese into
   * Burmese — the reference has to be a third language, and asking for it must not put it
   * on screen. Hence `burn: false`.
   */
  const readIn = useCallback(
    (code: string) => {
      if (projectId) tracks.mutate({ projectId, language: code, on: true, burn: false })
    },
    [tracks, projectId],
  )
  const shiftCues = useCallback(
    (byMs: number) => {
      if (plan.data?.id) shift.mutate({ planId: plan.data.id, byMs })
    },
    [shift, plan.data?.id],
  )
  /**
   * Pace is not persisted.
   *
   * There is no column for it and inventing one client-side would be a control that appears
   * to save and does not. It stays a no-op until voice direction has somewhere to live.
   */
  const setPace = useCallback((_id: string, _pace: number) => {}, [])

  const signIn = useCallback(() => {
    setHistory([])
    setScreen('start')
  }, [])

  const signOut = useCallback(() => {
    setToken(null)
    setProjectId(null)
    setHistory([])
    setScreen('auth')
    // Clearing the cache is what actually signs you out. Dropping the token alone leaves the
    // session query holding its last successful answer, so `signedIn` stays true and the app
    // carries on showing the previous account's projects until something happens to refetch.
    qc.clear()
  }, [qc])

  const setBeat = useCallback(
    (id: number, text: string) =>
      setBeats((b) => b.map((x) => (x.id === id ? { ...x, text } : x))),
    [],
  )
  const addBeat = useCallback(
    (after: number) =>
      setBeats((b) => {
        const i = b.findIndex((x) => x.id === after)
        const next = [...b]
        next.splice(i + 1, 0, { id: Date.now(), kind: 'turn', text: '' })
        return next
      }),
    [],
  )
  const removeBeat = useCallback(
    (id: number) => setBeats((b) => b.filter((x) => x.id !== id)),
    [],
  )

  return {
    screen,
    go,
    back,
    canGoBack: history.length > 0,
    signedIn: session.isSuccess,

    // Surfaced so screens can be honest about a slow or failed load instead of drawing an
    // empty storyboard that looks like a project with no scenes in it.
    loading: plan.isLoading || projects.isLoading,
    error: (plan.error ?? projects.error ?? credits.error) as Error | null,

    // Set when the API refused something for want of credits. Its own field rather than an
    // error, because it is not a failure the user should read as one — it is a price they
    // have not met, and the numbers say by how much.
    shortfall,
    clearShortfall: () => setShortfall(null),

    projectId,
    setProjectId,
    /*
     * What to call this video.
     *
     * The plan's own title when it has one — a written lane authors a real one. A transcribed
     * lane does not: it used to name itself after its first caption, so a Japanese video was
     * called 心を燃やせ。 at the top of every screen and never changed. The name the creator
     * typed, or the file they uploaded, is the one they recognise.
     */
    project:
      plan.data?.title ||
      projects.data?.find((p) => p.id === projectId)?.title ||
      'Untitled',
    moments: plan.data?.moments ?? [],
    /**
     * The file this project was made from, for the lanes that keep the creator's footage.
     * The player shows this until a render exists — without it, someone uploads a video and
     * then cannot watch the video they uploaded.
     */
    sourceVideo: plan.data?.source_asset_id ? assetUrl(plan.data.source_asset_id) : null,
    //: How long the finished video will be, and whether the source comes back whole. Both
    //: from the server: summing the scenes is wrong for subtitling, whose scenes cover only
    //: the captioned moments while its output is the whole video.
    durationMs: plan.data?.duration_ms ?? 0,
    keepsWholeSource: plan.data?.keeps_whole_source ?? false,
    //: Which languages are burnt in, primary first.
    captionLanguages: plan.data?.caption_languages ?? [],
    //: What this project's captions come out as, and what was spoken — from the plan, not
    //: the channel default, because a project keeps the pair it was created with.
    planLanguage: plan.data?.language ?? language,
    //: `null` when the project is not a translation: the scene's own line is already the
    //: language being written, and there is no "original" distinct from it.
    sourceLanguage: plan.data?.source_language ?? null,
    readIn,
    translating: tracks.isPending,
    //: Why the last track change failed, in the server's own words. Surfaced because the
    //: common reason — no translation engine configured — is one the creator can act on, and
    //: a button that silently does nothing is the worst possible way to say so.
    trackError: tracks.isError ? messageOf(tracks.error, 'That did not work.') : null,
    summary: plan.data?.summary ?? '',
    recipe,
    //: Which voice this dub is spoken in, so the preview screen can show what was chosen and
    //: play it. Read from the project rather than held in the screen: a refresh must not
    //: silently un-choose a voice the render is going to use.
    voiceId: projects.data?.find((p) => p.id === projectId)?.voice_id ?? null,
    setRecipe,
    scenes,
    balance: credits.data?.available ?? 0,
    beats,
    setBeats,
    saveBeats,
    setBeat,
    addBeat,
    removeBeat,
    openScene,
    setOpenScene,
    setPrompt,
    setLine,
    setCueTime,
    shiftCues,
    setTrack,
    setCaptionText,
    setPace,
    signIn,
    signOut,
    language,
    setLanguage,
    watermark,
    setWatermark,
    logoAssetId,
    setLogo,
    captionStyle,
    captionKaraoke,
    setCaptionKaraoke,
    captionBottom,
    setCaptionBottom,
    captionScale,
    setCaptionScale,
    glossary,
    setGlossary,
    setCaptionStyle,
    seriesMemory,
    setSeriesMemory,
    voiceReady,
    channel: channel.data ?? null,
    formats: formatsOf(renders.data),
    renders: renders.data ?? [],
    stages,
    progress,
    toggleScene,
    regen,
    addScene,
    insertCue,
    removeScene,
    upgrade,
    render,
    //: Why the last render request was refused, in the server's own words. Surfaced because
    //: the button used to fire and ignore the answer: a refusal left the screen unchanged,
    //: which reads as a broken button rather than as a reason.
    renderError: startRender.isError
      ? messageOf(startRender.error, 'That did not work.')
      : null,
    draft,
    startRender,
    // The Credits screen's purchase button. Real payments are the Platform plane's job; until
    // Stripe is wired this refetches rather than pretending the balance changed.
    buy: (_n: number) => credits.refetch(),
  }
}

export type Studio = ReturnType<typeof useLiveStudio>
