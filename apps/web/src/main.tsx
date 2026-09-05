/**
 * App entry.
 *
 * The app talks to the real API. There is no mock path any more: fake rows in the real app
 * are how you ship a demo that does not work, and the design export still lives at
 * /mock.html for anyone reviewing the visual design rather than the product.
 */
import React from 'react'
import ReactDOM from 'react-dom/client'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import { App } from './app/Shell'

/**
 * In development, evict any service worker that is still installed.
 *
 * The PWA plugin precaches the built app, and a worker registered from an earlier production
 * build keeps serving that build to this origin — the dev server's changes never arrive. It
 * is a trap that closes behind you: `/sw.js` on the dev server falls through to index.html,
 * so the browser cannot fetch a valid replacement script and the stale worker stays installed
 * indefinitely. The symptom is an app that never updates however many times you reload, and
 * looks like the build is broken.
 *
 * Unregistering from the page rather than fixing the worker, because the page always runs:
 * the worker cannot fix itself once its own script no longer parses.
 *
 * Production is untouched — see the `workbox` options in `vite.config.ts`, which make the
 * shipped worker replace itself rather than needing this.
 */
if (import.meta.env.DEV && 'serviceWorker' in navigator) {
  void (async () => {
    const workers = await navigator.serviceWorker.getRegistrations()
    if (!workers.length) return
    await Promise.all(workers.map((w) => w.unregister()))
    if ('caches' in window) {
      const names = await caches.keys()
      await Promise.all(names.map((n) => caches.delete(n)))
    }
    // The page currently on screen was served by the worker that just went away, so it is
    // still the stale one. One reload, and only when something was actually removed, so this
    // cannot loop.
    location.reload()
  })()
}

/**
 * Retry is off.
 *
 * Most failures here are a 401 before sign-in or a 409 the user needs to see — retrying
 * either just delays an answer the screen already knows how to show. The long-running work
 * is polled explicitly by the queries that care, not retried blindly by all of them.
 */
const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={qc}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>,
)
