/**
 * Reading what the API said when it refused.
 *
 * One place, because the shape is not obvious and every screen that guessed it got it wrong.
 * `@hey-api/client-fetch` with `throwOnError` throws the **parsed response body itself** —
 * so a 401 arrives as `{detail: "..."}`, not wrapped in an `error` field. Three screens
 * assumed the wrapper and silently fell through to "check your connection", which turned
 * every deliberate, useful refusal into a network error.
 */

/** FastAPI's `detail`, whatever shape this particular error used. */
function detailOf(error: unknown): unknown {
  if (!error || typeof error !== 'object') return undefined
  const body = error as Record<string, unknown>
  // The thrown value is the body. `body.error` is checked too so a change in the client's
  // contract degrades to the old behaviour rather than to silence.
  const inner = body.error as Record<string, unknown> | undefined
  return body.detail ?? inner?.detail
}

/**
 * A sentence to show the person, or null if the error is not one they can act on.
 *
 * The server's own wording is used verbatim where it has any: "There is already an account
 * with that email" is exactly right, and replacing it with something generic would throw away
 * the only part of the response that helps.
 */
export function messageOf(error: unknown, fallback = 'Something went wrong. Try again.'): string | null {
  if (!error) return null
  const detail = detailOf(error)

  if (typeof detail === 'string') return detail

  if (Array.isArray(detail)) {
    // FastAPI's validation shape: a list of {msg, loc}. The first is the actionable one, and
    // its "Value error, " prefix is a pydantic implementation detail, not a message.
    const first = detail[0] as { msg?: string } | undefined
    if (first?.msg) return first.msg.replace(/^Value error, /, '')
  }

  if (detail && typeof detail === 'object' && 'message' in detail) {
    return String((detail as Record<string, unknown>).message)
  }

  return fallback
}

/** How far short of affording something the user is. */
export type Shortfall = { needed: number; available: number; short_by: number }

/**
 * Read a 402 into numbers a screen can place next to the button that was pressed.
 *
 * The server answers with the three figures rather than a sentence precisely so the interface
 * can word it — and so it can be worded in the creator's language rather than in English.
 * Anything that is not a shortfall returns null and is handled as an ordinary error.
 */
export function shortfallOf(error: unknown): Shortfall | null {
  const detail = detailOf(error)
  if (detail && typeof detail === 'object' && 'short_by' in detail) {
    const d = detail as Record<string, number>
    return {
      needed: Number(d.needed ?? 0),
      available: Number(d.available ?? 0),
      short_by: Number(d.short_by ?? 0),
    }
  }
  return null
}
