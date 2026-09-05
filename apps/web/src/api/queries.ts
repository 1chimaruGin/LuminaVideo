/**
 * Server state.
 *
 * TanStack Query owns everything that lives on the server; component state owns only what is
 * local to a screen. The split matters here because jobs outlive the tab: a mutation cannot
 * assume it knows the outcome, it can only invalidate and let the next read tell the truth.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import type {
  BalanceOut,
  BeatsOut,
  ChannelOut,
  Hooks,
  JobOut,
  LedgerEntryOut,
  ProviderOut,
  PlanOut,
  PostCopy,
  ProjectOut,
  QuoteOut,
  LanguageOut,
  VoiceOut,
  NewProject,
  NewRender,
  RecipeOut,
  SceneEdit,
  RenderOut,
  SceneOut,
  SessionOut,
  Timing,
  UploadOut,
} from '@lumina/client'
import {
  authProviders,
  authSignIn,
  authSignUp,
  authWhoami,
  channelEditChannel,
  channelGetChannel,
  creditsBalance,
  creditsHistory,
  creditsQuote,
  jobsListJobs,
  jobsRetry,
  projectsCreateProject,
  projectsDraft,
  projectsGetPlan,
  projectsListProjects,
  projectsAddTrack,
  projectsDeleteProject,
  projectsDropTrack,
  projectsLanguages,
  projectsChooseVoice,
  projectsFixTiming,
  projectsVoices,
  projectsRecipes,
  rendersGetRender,
  rendersListRenders,
  rendersStartRender,
  scenesAddScene,
  scenesDeleteScene,
  scenesEditScene,
  scenesShift,
  scenesMove,
  scenesRegenerate,
  scenesRevert,
  scenesUpgrade,
  scriptBeats,
  scriptHooks,
  scriptPostCopy,
  scriptReadTiming,
  scriptRewrite,
  uploadsUpload,
} from '@lumina/client'

import './client'
import { setToken } from './client'

/**
 * The generated SDK's response generic widens `.data` to a union of the payload's field
 * types — the installed `@hey-api/client-fetch` and the generator disagree on that generic.
 * The schema types themselves are correct, so each call is pinned to the one it returns.
 * This is the only place that knowledge lives; if the versions align later, these go away.
 */
const as = <T,>(value: unknown): T => value as T

export const keys = {
  session: ['session'] as const,
  channel: ['channel'] as const,
  recipes: ['recipes'] as const,
  languages: ['languages'] as const,
  voices: (language: string) => ['voices', language] as const,
  projects: ['projects'] as const,
  plan: (id: string) => ['plan', id] as const,
  credits: ['credits'] as const,
  history: ['credits', 'history'] as const,
  quote: (id: string) => ['quote', id] as const,
  renders: (id: string) => ['renders', id] as const,
  jobs: (id: string | null) => ['jobs', id ?? 'all'] as const,
}

/**
 * How often to re-read while work is in flight.
 *
 * `design-brief.md` asks for per-scene progress and never one global spinner, so the
 * storyboard polls itself rather than blocking on a single request. Two seconds is slow
 * enough not to hammer the API and fast enough that a scene landing feels immediate.
 */
const WHILE_WORKING = 2000

/**
 * Scene states that mean "something is still coming".
 *
 * `failed` is settled: it will not change without the user retrying, so polling it forever
 * is a request every two seconds for a scene that is never going to move on its own.
 */
const UNSETTLED = new Set(['draft', 'previewing', 'upgrading'])

// ---------------------------------------------------------------- identity

export function useSession() {
  return useQuery({
    queryKey: keys.session,
    retry: false,
    queryFn: async () => as<SessionOut>((await authWhoami({ throwOnError: true })).data),
  })
}

/**
 * What both auth calls do once they succeed.
 *
 * The token is stored *before* invalidating, or every refetch fires without the header and
 * comes back 401 — which reads as a failed sign-in rather than as an ordering mistake.
 */
function onSignedIn(qc: ReturnType<typeof useQueryClient>) {
  return (session: SessionOut) => {
    setToken(session.token)
    qc.invalidateQueries()
  }
}

export function useSignUp() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: {
      email: string
      password: string
      display_name?: string
      language?: string
    }) => as<SessionOut>((await authSignUp({ body, throwOnError: true })).data),
    onSuccess: onSignedIn(qc),
  })
}

export function useSignIn() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: { email: string; password: string }) =>
      as<SessionOut>((await authSignIn({ body, throwOnError: true })).data),
    onSuccess: onSignedIn(qc),
  })
}

export function useSignOut() {
  const qc = useQueryClient()
  return () => {
    setToken(null)
    qc.clear()
  }
}

/**
 * Which sign-in providers this server can actually use.
 *
 * Cached for the session: it changes when the deployment changes, not while someone is
 * looking at the screen. Unconfigured ones come back too, so the button can be shown greyed
 * with a reason rather than silently missing.
 */
export function useProviders() {
  return useQuery({
    queryKey: ['providers'],
    staleTime: Infinity,
    retry: false,
    queryFn: async () => as<ProviderOut[]>((await authProviders({ throwOnError: true })).data),
  })
}

export function useChannel() {
  return useQuery({
    queryKey: keys.channel,
    queryFn: async () => as<ChannelOut>((await channelGetChannel({ throwOnError: true })).data),
  })
}

export function useEditChannel() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: {
      name?: string
      language?: string
      voice_profile_asset_id?: string | null
      logo_asset_id?: string | null
      identity?: Record<string, unknown>
    }) => as<ChannelOut>((await channelEditChannel({ body, throwOnError: true })).data),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.channel }),
  })
}

// ---------------------------------------------------------------- recipes and projects

export function useRecipes() {
  return useQuery({
    queryKey: keys.recipes,
    // The lanes change only when the backend ships. Refetching them on every mount is pure
    // noise, so this is cached for the session.
    staleTime: Infinity,
    queryFn: async () => as<RecipeOut[]>((await projectsRecipes({ throwOnError: true })).data),
  })
}

/**
 * Which languages this server can actually work in.
 *
 * From the server, because a language is renderable only if it has a pack — its font, its
 * reading speed, its unit segmentation. A hardcoded list here offered Japanese, Korean,
 * Chinese, Vietnamese and Tagalog, none of which have one, so choosing any of them made every
 * project fail with a 400 the picker had invited.
 */
export function useLanguages() {
  return useQuery({
    queryKey: keys.languages,
    staleTime: Infinity,
    queryFn: async () => as<LanguageOut[]>((await projectsLanguages({ throwOnError: true })).data),
  })
}

/**
 * The voices a dub can be spoken in, for one target language.
 *
 * Keyed by language because the answer genuinely differs — a language no engine here speaks
 * has none — and cached for the session like the other two capability lists. Skipped
 * entirely when there is no language yet, so the picker does not flash an empty state while
 * the creator is still choosing what to dub into.
 */
export function useVoices(language: string | null) {
  return useQuery({
    queryKey: keys.voices(language ?? ''),
    enabled: Boolean(language),
    staleTime: Infinity,
    queryFn: async () =>
      as<VoiceOut[]>(
        (await projectsVoices({ query: { language: language ?? '' }, throwOnError: true })).data,
      ),
  })
}

/**
 * Committing to a voice.
 *
 * Stored on the project rather than held in the screen, because the render reads it and a
 * choice that lives only in a component is a choice that a refresh loses. Invalidates the
 * project list so the picker and the preview agree about what was chosen.
 */
export function useChooseVoice(projectId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (voiceId: string | null) =>
      as<ProjectOut>(
        (
          await projectsChooseVoice({
            path: { project_id: projectId, voice_id: voiceId ?? '-' },
            throwOnError: true,
          })
        ).data,
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.projects }),
  })
}

/**
 * Giving every caption enough time to be read.
 *
 * Invalidates the plan, because it changes both the cue timings and — when the lines are too
 * long for the time available at all — the words themselves.
 */
export function useFixTiming(projectId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (condense: boolean) =>
      as<PlanOut>(
        (
          await projectsFixTiming({
            path: { project_id: projectId },
            query: { condense },
            throwOnError: true,
          })
        ).data,
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.plan(projectId) }),
  })
}

export function useProjects() {
  return useQuery({
    queryKey: keys.projects,
    queryFn: async () =>
      as<ProjectOut[]>((await projectsListProjects({ throwOnError: true })).data),
  })
}

export function usePlan(projectId: string | null) {
  return useQuery({
    queryKey: keys.plan(projectId ?? ''),
    enabled: Boolean(projectId),
    queryFn: async () =>
      as<PlanOut>(
        (await projectsGetPlan({ path: { project_id: projectId! }, throwOnError: true })).data,
      ),
    // Poll while anything is still coming, and stop once everything has settled — a
    // storyboard nothing is happening to is static until the user acts, and polling it is
    // wasted work.
    //
    // `draft` has to count as unsettled. It means "queued, not yet picked up by a worker",
    // and leaving it out made the storyboard stop refetching the instant it loaded: every
    // scene was still `draft`, so it looked finished, and the previews landed into a page
    // that had already stopped asking.
    refetchInterval: (query) => {
      const plan = query.state.data as PlanOut | undefined
      return plan?.scenes.some((s) => UNSETTLED.has(s.state)) ? WHILE_WORKING : false
    },
  })
}

export function useCreateProject() {
  const qc = useQueryClient()
  return useMutation({
    // The generated request type, not a copy of it. The hand-written version silently went
    // stale the moment the server grew a field, and the compiler could not say so — it just
    // rejected the new one as unknown.
    mutationFn: async (body: NewProject) =>
      as<PlanOut>((await projectsCreateProject({ body, throwOnError: true })).data),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.projects }),
  })
}

/**
 * Move every cue by the same amount.
 *
 * Its own mutation rather than a loop of edits: a forty-cue track is forty requests, forty
 * cache invalidations and a visibly stuttering list, and it is the fix creators reach for
 * first — a subtitle file being a second out is the most ordinary thing wrong with one.
 */
export function useShiftCues(projectId: string | null) {
  return useSceneMutation(projectId, async (args: { planId: string; byMs: number }) =>
    as<SceneOut[]>(
      (
        await scenesShift({
          path: { plan_id: args.planId },
          query: { by_ms: args.byMs },
          throwOnError: true,
        })
      ).data,
    ),
  )
}

/** Add or remove a burnt-in caption language. Returns the plan, so the list re-renders. */
export function useTracks(projectId: string | null) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (args: {
      projectId: string
      language: string
      on: boolean
      /**
       * Whether the language goes on screen, or is only translated so it can be read.
       *
       * A creator subtitling Chinese into Burmese cannot check the Burmese against a source
       * they do not read, so the editor offers English alongside — a language to check
       * *against*, which is not one they want burnt into the video.
       */
      burn?: boolean
    }) => {
      const path = { project_id: args.projectId, language: args.language }
      if (!args.on) {
        return as<PlanOut>((await projectsDropTrack({ path, throwOnError: true })).data)
      }
      const query = args.burn === false ? { burn: false } : undefined
      return as<PlanOut>((await projectsAddTrack({ path, query, throwOnError: true })).data)
    },
    onSuccess: () => {
      if (projectId) void qc.invalidateQueries({ queryKey: keys.plan(projectId) })
    },
  })
}

/**
 * Throw a project away.
 *
 * The server refunds whatever the project was still holding before deleting it — see
 * `DELETE /projects/{id}` — so the balance is refetched too, not just the list.
 */
export function useDeleteProject() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (projectId: string) => {
      await projectsDeleteProject({ path: { project_id: projectId }, throwOnError: true })
      return projectId
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.projects })
      void qc.invalidateQueries({ queryKey: keys.credits })
    },
  })
}

export function useCredits() {
  return useQuery({
    queryKey: keys.credits,
    queryFn: async () => as<BalanceOut>((await creditsBalance({ throwOnError: true })).data),
  })
}

export function useHistory() {
  return useQuery({
    queryKey: keys.history,
    queryFn: async () =>
      as<LedgerEntryOut[]>((await creditsHistory({ throwOnError: true })).data),
  })
}

export function useQuote(projectId: string | null) {
  return useQuery({
    queryKey: keys.quote(projectId ?? ''),
    enabled: Boolean(projectId),
    queryFn: async () =>
      as<QuoteOut>(
        (await creditsQuote({ path: { project_id: projectId! }, throwOnError: true })).data,
      ),
  })
}

export function useDraft(projectId: string | null) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () =>
      (await projectsDraft({ path: { project_id: projectId! }, throwOnError: true })).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.plan(projectId ?? '') }),
  })
}

// ---------------------------------------------------------------- one scene at a time

/** Everything a scene action needs to invalidate, in one place so none of them forgets one. */
function useSceneMutation<A>(
  projectId: string | null,
  run: (args: A) => Promise<unknown>,
  { costs = false }: { costs?: boolean } = {},
) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: run,
    // Refetch rather than patching the cache by hand: the server is the only thing that
    // knows whether a worker also changed this scene while the request was in flight.
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.plan(projectId ?? '') })
      if (costs) {
        qc.invalidateQueries({ queryKey: keys.credits })
        qc.invalidateQueries({ queryKey: keys.quote(projectId ?? '') })
      }
    },
  })
}

export function useEditScene(projectId: string | null) {
  return useSceneMutation(
    projectId,
    async (args: {
      sceneId: string
      // The generated type, not a copy: the hand-written one went stale the moment the
      // server grew a field, and the compiler could only say the new one was unknown.
      patch: SceneEdit
    }) =>
      (
        await scenesEditScene({
          path: { scene_id: args.sceneId },
          body: args.patch,
          throwOnError: true,
        })
      ).data,
  )
}

export function useRegenerate(projectId: string | null) {
  return useSceneMutation(
    projectId,
    async (args: { sceneId: string; tier?: string }) =>
      (
        await scenesRegenerate({
          path: { scene_id: args.sceneId },
          query: args.tier ? { tier: args.tier } : undefined,
          throwOnError: true,
        })
      ).data,
    { costs: true },
  )
}

export function useUpgrade(projectId: string | null) {
  return useSceneMutation(
    projectId,
    async (args: { sceneId: string; tier?: string }) =>
      (
        await scenesUpgrade({
          path: { scene_id: args.sceneId },
          query: args.tier ? { tier: args.tier } : undefined,
          throwOnError: true,
        })
      ).data,
    { costs: true },
  )
}

export function useRevert(projectId: string | null) {
  return useSceneMutation(
    projectId,
    async (args: { sceneId: string }) =>
      as<SceneOut>(
        (await scenesRevert({ path: { scene_id: args.sceneId }, throwOnError: true })).data,
      ),
  )
}

export function useAddScene(projectId: string | null) {
  return useSceneMutation(
    projectId,
    async (args: {
      planId: string
      index?: number
      script_line?: string
      /** How long the new cue runs. */
      duration_ms?: number
      /**
       * Where it sits in the source video.
       *
       * Required for a subtitle track: without it the caption stage falls back to the running
       * total of the scenes before this one, which on a lane that hands the video back whole
       * is not a position in the video at all — the line would appear at the wrong moment.
       */
      source_start_ms?: number
    }) =>
      as<SceneOut>(
        (
          await scenesAddScene({
            path: { plan_id: args.planId },
            body: {
              index: args.index ?? null,
              script_line: args.script_line ?? '',
              ...(args.duration_ms ? { duration_ms: args.duration_ms } : {}),
              ...(args.source_start_ms !== undefined
                ? { source_start_ms: args.source_start_ms }
                : {}),
            },
            throwOnError: true,
          })
        ).data,
      ),
  )
}

export function useDeleteScene(projectId: string | null) {
  return useSceneMutation(
    projectId,
    async (args: { sceneId: string }) =>
      (await scenesDeleteScene({ path: { scene_id: args.sceneId }, throwOnError: true })).data,
  )
}

export function useMoveScene(projectId: string | null) {
  return useSceneMutation(
    projectId,
    async (args: { sceneId: string; index: number }) =>
      as<SceneOut[]>(
        (
          await scenesMove({
            path: { scene_id: args.sceneId },
            body: { index: args.index },
            throwOnError: true,
          })
        ).data,
      ),
  )
}

// ---------------------------------------------------------------- deliver

export function useRenders(projectId: string | null) {
  return useQuery({
    queryKey: keys.renders(projectId ?? ''),
    enabled: Boolean(projectId),
    queryFn: async () =>
      as<RenderOut[]>(
        (await rendersListRenders({ path: { project_id: projectId! }, throwOnError: true })).data,
      ),
    refetchInterval: (query) => {
      /*
       * Poll while anything is unfinished, not only while it is `queued`.
       *
       * A render row moves queued -> ready, but the check ran on the *cached* list: if the
       * page was opened before the render existed, that list was empty, `some` was false, and
       * the query never polled again. The row landed on the server and the screen went on
       * saying "not made yet" until something else happened to invalidate it.
       */
      const rows = query.state.data as RenderOut[] | undefined
      return rows?.some((r) => r.status !== 'ready') ? WHILE_WORKING : false
    },
  })
}

export function useRender(renderId: string | null) {
  return useQuery({
    queryKey: ['render', renderId ?? ''],
    enabled: Boolean(renderId),
    queryFn: async () =>
      as<RenderOut>(
        (await rendersGetRender({ path: { render_id: renderId! }, throwOnError: true })).data,
      ),
  })
}

export function useStartRender(projectId: string | null) {
  const qc = useQueryClient()
  return useMutation({
    // The generated type, so a field added on the server is usable here without
    // the compiler rejecting it as unknown.
    mutationFn: async (body: NewRender) =>
      as<RenderOut[]>(
        (
          await rendersStartRender({
            path: { project_id: projectId! },
            body,
            throwOnError: true,
          })
        ).data,
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.renders(projectId ?? '') }),
  })
}

// ---------------------------------------------------------------- jobs

export function useJobs(projectId: string | null) {
  return useQuery({
    queryKey: keys.jobs(projectId),
    queryFn: async () =>
      as<JobOut[]>(
        (
          await jobsListJobs({
            query: projectId ? { project_id: projectId } : undefined,
            throwOnError: true,
          })
        ).data,
      ),
    refetchInterval: (query) => {
      const rows = query.state.data as JobOut[] | undefined
      return rows?.some((j) => j.status === 'queued' || j.status === 'running')
        ? WHILE_WORKING
        : false
    },
  })
}

export function useRetryJob(projectId: string | null) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (jobId: string) =>
      as<JobOut>((await jobsRetry({ path: { job_id: jobId }, throwOnError: true })).data),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.jobs(projectId) }),
  })
}

// ---------------------------------------------------------------- script studio

/**
 * Read-aloud length.
 *
 * Debounced by the caller, not here: this is arithmetic over the language pack's reading
 * speed with no model call behind it, so the only reason to debounce is the round trip.
 */
export function useTiming(text: string, language: string, targetMs: number) {
  return useQuery({
    queryKey: ['timing', text, language, targetMs],
    enabled: text.trim().length > 0,
    queryFn: async () =>
      as<Timing>(
        (
          await scriptReadTiming({
            body: { text, language, target_ms: targetMs },
            throwOnError: true,
          })
        ).data,
      ),
  })
}

export function useBeats(text: string, language: string, targetMs: number) {
  return useQuery({
    queryKey: ['beats', text, language, targetMs],
    enabled: text.trim().length > 0,
    queryFn: async () =>
      as<BeatsOut>(
        (await scriptBeats({ body: { text, language, target_ms: targetMs }, throwOnError: true }))
          .data,
      ),
  })
}

export function useHooks() {
  return useMutation({
    mutationFn: async (body: { text: string; language?: string }) =>
      as<Hooks>((await scriptHooks({ body, throwOnError: true })).data),
  })
}

export function useRewrite() {
  return useMutation({
    mutationFn: async (body: { line: string; operation: string; language?: string }) =>
      as<{ line: string }>((await scriptRewrite({ body, throwOnError: true })).data),
  })
}

/**
 * Post copy, per platform.
 *
 * A query rather than a mutation even though it POSTs: it is a read of "what should this
 * video's caption be", it has no side effect, and the Deliver screen wants it cached for as
 * long as the script has not changed. Keyed by the script so an edit invalidates it.
 */
export function usePostCopy(title: string, script: string, language: string) {
  return useQuery({
    queryKey: ['post-copy', title, script, language],
    enabled: script.trim().length > 0,
    staleTime: Infinity,
    queryFn: async () =>
      as<PostCopy[]>(
        (await scriptPostCopy({ body: { title, script, language }, throwOnError: true })).data,
      ),
  })
}

// ---------------------------------------------------------------- uploads

export function useUpload() {
  return useMutation({
    mutationFn: async (file: File) =>
      as<UploadOut>(
        (
          await uploadsUpload({
            // The SDK serialises this through `formDataBodySerializer`, so a File is exactly
            // what belongs here at runtime. The declared type is `string` only because
            // OpenAPI's `format: binary` has no better TypeScript mapping — the cast is
            // against the generator's limitation, not against the API.
            body: { file: file as unknown as string },
            throwOnError: true,
          })
        ).data,
      ),
  })
}
