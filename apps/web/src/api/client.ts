/**
 * API access.
 *
 * The types and request functions come from `@lumina/client`, generated from the running
 * API's OpenAPI schema — so a rename in a Pydantic model becomes a TypeScript error here
 * rather than a runtime surprise. This file configures that client and nothing else.
 */
import { client } from '@lumina/client'

/**
 * Where the API is.
 *
 * `VITE_API_BASE_URL` wins when set. Otherwise it is derived from the page's own address
 * rather than hardcoded to 127.0.0.1 — because "localhost" means *the browser's* machine, and
 * the browser is not always on the same machine as the server. Under WSL, opening the app
 * from Windows at the WSL address made the app call the Windows loopback; from a phone on the
 * same wifi it would call the phone. Same host, API port, is right in every one of those.
 */
function apiBase(): string {
  const configured = import.meta.env.VITE_API_BASE_URL
  if (configured) return String(configured).replace(/\/$/, '')

  const { protocol, hostname } = window.location
  return `${protocol}//${hostname}:${API_PORT}`
}

/** Where `make api` serves. Override with VITE_API_BASE_URL if you move it. */
const API_PORT = 8000

const BASE = apiBase()

/**
 * Where the API is, for the things a fetch cannot do.
 *
 * Provider sign-in is a full browser navigation — the consent screen has to be rendered by
 * the browser, and it refuses to be framed or fetched — so that link is built by hand rather
 * than through the generated client.
 */
export const API_BASE = BASE

/**
 * The session token.
 *
 * `POST /auth/signup` and `POST /auth/session` both return one; it goes back on every request
 * as `Authorization: Bearer`. It is deliberately not the user id — that is a database key, and
 * it turns up in logs, tickets and error reports, so handing it out as the credential would
 * make every one of those a credential leak.
 *
 * Kept in `localStorage` so a closed tab does not sign the creator out mid-project. That is a
 * real tradeoff: anything that can run script on this origin can read it. The alternative is
 * an httpOnly cookie, which needs the API and the app on one origin — worth doing, and a
 * change to this file and the CORS config rather than to any screen.
 */
const TOKEN_KEY = 'lumina.token'

export function currentToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null // private windows and blocked site data both throw here
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* storage unavailable; the header goes unset and the API answers 401 */
  }
  applyIdentity()
}

/**
 * Push the token into the client config.
 *
 * Called explicitly rather than expressed as a getter on the headers object: the config is
 * read once when it is set, so a lazy getter is evaluated at configuration time and captures
 * whatever the value was then — which is empty on first load, and produces a 401 that looks
 * like an auth bug rather than an ordering one.
 */
function applyIdentity(): void {
  const token = currentToken()
  client.setConfig({
    baseUrl: BASE,
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
}

applyIdentity()

/**
 * Where an asset's bytes are.
 *
 * Built here rather than in a screen because it is the one place that knows the API's base
 * URL. The endpoint serves byte ranges, so this URL works as a `<video src>` that can seek,
 * not only as an image source.
 */
export function assetUrl(assetId: string): string {
  return `${BASE}/assets/${assetId}`
}

/**
 * The same asset, asked for as a file to save rather than as something to display.
 *
 * `<a download="...">` is ignored unless the link is same-origin, and the app and the API are
 * not — so pressing Download navigated the tab to the video instead of saving it. The
 * disposition and the filename have to come from the server, which is what this asks for.
 *
 * `name` is a suggestion: the server strips it to the characters a filename may contain and
 * puts the extension on from the mime type it recorded.
 */
export function downloadUrl(assetId: string, name: string): string {
  return `${BASE}/assets/${assetId}?download=${encodeURIComponent(name)}`
}

/**
 * A voice reading this language's sample sentence, as something `<audio>` can play.
 *
 * A plain URL rather than a fetch because an `<audio src>` cannot carry an Authorization
 * header, and this endpoint needs none: the sentence is fixed, the voice is the engine's,
 * and nothing about either belongs to the creator asking for it.
 */
export function voiceSampleUrl(voiceId: string, language: string): string {
  return `${BASE}/projects/voices/${encodeURIComponent(voiceId)}/sample?language=${encodeURIComponent(language)}`
}

/**
 * The opening of a project's dub, in one voice, downloaded and turned into something
 * `<audio>` can play.
 *
 * Fetched and wrapped in a blob URL rather than handed to the element as a plain address,
 * because unlike the voice samples this audio is the creator's own video and the endpoint
 * checks who is asking. An `<audio src>` cannot carry an Authorization header, and the usual
 * workaround — putting the session token in the query string — writes a live credential into
 * browser history, referrers and every access log between here and the server.
 *
 * The caller owns the returned URL and must `URL.revokeObjectURL` it, or the audio stays in
 * memory for the life of the tab.
 */
export async function fetchDubPreview(projectId: string, voice: string): Promise<string> {
  const token = localStorage.getItem(TOKEN_KEY)
  const res = await fetch(
    `${BASE}/projects/${projectId}/dub-preview?voice=${encodeURIComponent(voice)}`,
    { headers: token ? { Authorization: `Bearer ${token}` } : {} },
  )
  if (!res.ok) {
    /*
     * Carry the server's own sentence, not a status code.
     *
     * The API distinguishes these carefully — a quota that refills in a minute, a voice that
     * does not exist, no voice chosen yet, no engine configured — and every one of them was
     * being collapsed into "That voice could not be prepared. Try another." Telling someone
     * to try another voice when the real answer is "your key's speech quota ran out, it
     * refills every minute" sends them round a loop that cannot work.
     */
    const said = await res
      .clone()
      .json()
      .then((body: { detail?: string }) => body.detail)
      .catch(() => null)
    throw new Error(said || `The voice could not be prepared (${res.status}).`)
  }
  return URL.createObjectURL(await res.blob())
}

export { client }
