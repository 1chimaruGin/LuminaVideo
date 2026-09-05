import { defineConfig, type PluginOption } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'
import { fileURLToPath } from 'node:url'

const entry = (f: string) => fileURLToPath(new URL(f, import.meta.url))

/**
 * A service worker whose only job is to remove itself.
 *
 * The trap this exists to spring: the PWA plugin registers a precaching worker at `/sw.js`.
 * Once one is installed on an origin it serves its precached copy of the app to that origin —
 * including to the dev server on the same host and port. The dev server's changes never
 * arrive, however many times you reload, and it looks exactly like a broken build.
 *
 * It cannot be fixed from inside the app, because the app is what is not being served. And it
 * cannot fix itself, because Vite's dev server answers `/sw.js` with index.html — not
 * JavaScript — so the browser's update check fails to parse a replacement and keeps the old
 * worker indefinitely.
 *
 * So the dev server serves this instead: a valid worker, which the browser accepts as an
 * update, which then drops every cache, unregisters itself, and reloads whatever tabs it was
 * holding. One navigation and the origin is clean.
 */
const SELF_DESTRUCT = `
self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const names = await caches.keys()
    await Promise.all(names.map((n) => caches.delete(n)))
    await self.registration.unregister()
    const open = await self.clients.matchAll({ type: 'window' })
    for (const client of open) client.navigate(client.url)
  })())
})
`

const killStaleServiceWorker = (): PluginOption => ({
  name: 'lumina:kill-stale-service-worker',
  // Development only. The built app wants a real worker — it is what makes "notify me when
  // the render finishes" possible with the tab closed.
  apply: 'serve',
  configureServer(server) {
    server.middlewares.use((req, res, next) => {
      if ((req.url ?? '').split('?')[0] !== '/sw.js') return next()
      res.setHeader('Content-Type', 'text/javascript')
      // Never cached, or the browser could keep answering update checks from disk.
      res.setHeader('Cache-Control', 'no-store')
      res.setHeader('Service-Worker-Allowed', '/')
      res.end(SELF_DESTRUCT)
    })
  },
})

export default defineConfig({
  plugins: [
    killStaleServiceWorker(),
    react(),
    tailwindcss(),
    VitePWA({
      registerType: 'autoUpdate',
      /*
       * A new build takes over immediately instead of waiting for every tab to close.
       *
       * Without these the shipped worker keeps serving the version it precached until the
       * last tab of the app is gone — which for a tool people leave open while a render runs
       * means shipping a fix and watching users not get it. `cleanupOutdatedCaches` throws
       * away the precache of the version being replaced rather than leaving it on disk.
       */
      workbox: {
        skipWaiting: true,
        clientsClaim: true,
        cleanupOutdatedCaches: true,
      },
      // Jobs run for minutes and the tab will be closed. The service worker is what makes
      // "notify when the render finishes" possible at all.
      manifest: {
        name: 'Lumina',
        short_name: 'Lumina',
        start_url: '/',
        display: 'standalone',
        background_color: '#05060f',
        theme_color: '#05060f',
        icons: [
          { src: '/icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: '/icon-512.png', sizes: '512x512', type: 'image/png' },
          { src: '/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
    }),
  ],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  build: {
    rollupOptions: {
      // /mock.html is the design reference; it must not share the app's CSS entry.
      input: { main: entry('index.html'), mock: entry('mock.html') },
    },
  },
  server: {
    port: 5173,
    proxy: { '/api': { target: 'http://localhost:8000', changeOrigin: true } },
  },
})
