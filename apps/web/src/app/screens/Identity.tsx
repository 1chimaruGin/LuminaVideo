/**
 * Identity kit.
 *
 * Set once, inherited by everything after. The voice leads because the brief calls it the
 * strongest retention hook; the rest is cheap to change and sits below it.
 */
import { useRef } from 'react'

import { assetUrl } from '../../api/client'
import { useLanguages } from '../../api/queries'
import { CAPTION_STYLES } from '../data'
import type { Studio } from '../live'
import { Head, View } from '../Shell'
import { IconCheck, IconMic, IconPlay, IconRefresh } from '../icons'

const LOOK: [string, string][] = [
  ['Type', 'Space Grotesk · IBM Plex Sans'],
  ['Intro / outro', '2s sting · subscribe card'],
]

/**
 * Whose mark goes on the finished video.
 *
 * Ours by default, because a video that leaves here unbranded is a video nobody can trace
 * back. The other two are the same question answered differently — "whose mark, if any" — so
 * they are one control rather than a toggle and a picker.
 */
const MARKS: { id: 'lumina' | 'own' | 'none'; name: string; note: string }[] = [
  { id: 'lumina', name: 'Lumina', note: 'Our mark, bottom right' },
  { id: 'own', name: 'Your logo', note: 'Upload a PNG with transparency' },
  { id: 'none', name: 'None', note: 'Nothing burnt in' },
]
const SWATCHES = ['#f2f5ff', '#5b8cff', '#9a7bff', '#ffc36b']

export function Identity({ s }: { s: Studio }) {
  const languages = useLanguages()
  const logo = useRef<HTMLInputElement>(null)
  return (
    <View mid>
      <Head
        kicker="Identity kit"
        title="Set this once"
        lede="Every video inherits it — voice, look, captions and language."
      />

      <div className="stack">
        <div className="card lit pad">
          <div className="spread">
            <span style={{ display: 'flex', alignItems: 'center', gap: 13 }}>
              <span
                style={{
                  width: 42,
                  height: 42,
                  display: 'grid',
                  placeItems: 'center',
                  borderRadius: 13,
                  background: 'var(--accent-soft)',
                  color: 'var(--accent)',
                }}
              >
                <IconMic size={21} />
              </span>
              <span>
                <b style={{ display: 'block', font: '600 16.5px/1.3 var(--ui)' }}>Your voice</b>
                <span className="muted" style={{ font: '400 13.5px/1.3 var(--ui)' }}>
                  {s.voiceReady
                    ? 'Used in every video you make'
                    : 'Record 60 seconds once and every video uses it'}
                </span>
              </span>
            </span>
            {/* Honest about whether a voice has actually been recorded. Claiming "Ready"
                before one exists makes the first video that uses it a surprise. */}
            {s.voiceReady ? (
              <span className="tag good">
                <IconCheck size={13} />
                Ready
              </span>
            ) : (
              <span className="tag">Not recorded</span>
            )}
          </div>
          <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
            {s.voiceReady ? (
              <>
                <button className="btn sm" disabled title="Playback is not connected yet">
                  <IconPlay size={15} />
                  Hear it
                </button>
                <button className="btn sm" onClick={() => s.go('voice')}>
                  <IconRefresh size={15} />
                  Re-record
                </button>
              </>
            ) : (
              <button className="btn sm" onClick={() => s.go('voice')}>
                <IconMic size={15} />
                Record it now
              </button>
            )}
          </div>
        </div>

        <section>
          <div className="label">Look</div>
          <div className="card">
            <div className="rowline">
              <span>Palette</span>
              <span style={{ display: 'flex', gap: 6 }}>
                {SWATCHES.map((c) => (
                  <i
                    key={c}
                    style={{
                      width: 20,
                      height: 20,
                      borderRadius: 6,
                      background: c,
                      border: '1px solid var(--line-2)',
                    }}
                  />
                ))}
              </span>
            </div>
            {/*
             * The watermark, which is a real setting rather than a line of copy.
             *
             * It sits at the top of the look because it is the only part of this card that
             * changes what leaves the product.
             */}
            <div className="rowline mark-row">
              <span>Watermark</span>
              <span className="ref-pick" role="group" aria-label="Watermark">
                {MARKS.map((m) => (
                  <button
                    key={m.id}
                    type="button"
                    aria-pressed={s.watermark === m.id}
                    title={m.note}
                    onClick={() => {
                      //: Choosing "your logo" with nothing uploaded opens the picker instead
                      //: of selecting an option that would silently fall back to ours.
                      if (m.id === 'own' && !s.logoAssetId) logo.current?.click()
                      else s.setWatermark(m.id)
                    }}
                  >
                    {m.name}
                  </button>
                ))}
              </span>
            </div>

            {s.watermark === 'own' ? (
              <div className="rowline">
                <span className="muted">Your logo</span>
                <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  {s.logoAssetId ? (
                    <img className="mark-swatch" src={assetUrl(s.logoAssetId)} alt="" />
                  ) : null}
                  <button className="btn ghost" onClick={() => logo.current?.click()}>
                    {s.logoAssetId ? 'Replace' : 'Upload'}
                  </button>
                </span>
              </div>
            ) : null}

            <input
              ref={logo}
              type="file"
              accept="image/png,image/svg+xml,image/webp"
              hidden
              onChange={(e) => {
                const file = e.target.files?.[0]
                //: Cleared so choosing the same file twice still fires a change event.
                e.target.value = ''
                if (file) void s.setLogo(file)
              }}
            />

            {LOOK.map(([k, v]) => (
              <div className="rowline" key={k}>
                <span>{k}</span>
                <span className="muted">{v}</span>
              </div>
            ))}
          </div>
        </section>

        <section>
          <div className="label">Caption style</div>
          <div className="tiles">
            {CAPTION_STYLES.map((c) => (
              <button
                key={c.id}
                className="tile"
                aria-pressed={s.captionStyle === c.id}
                onClick={() => s.setCaptionStyle(c.id)}
              >
                <span style={cssToObject(c.css)}>Light is fast</span>
                <em>{c.name}</em>
              </button>
            ))}
          </div>
        </section>

        <section>
          <div className="label">Video language</div>
          {/*
           * From the server, not from a list in the app. This offered Japanese, Korean,
           * Chinese, Vietnamese and Tagalog — none of which have a language pack, so choosing
           * any of them made every project fail with a 400 the picker itself had invited.
           * A language is available exactly when something can render it.
           */}
          <div className="chips">
            {(languages.data ?? []).map((l) => (
              <button
                key={l.code}
                className="chip"
                aria-pressed={s.language === l.code}
                onClick={() => s.setLanguage(l.code)}
                title={l.english}
                style={
                  s.language === l.code
                    ? {
                        borderColor: 'var(--accent)',
                        color: 'var(--accent)',
                        background: 'var(--accent-soft)',
                      }
                    : undefined
                }
              >
                {l.name}
              </button>
            ))}
          </div>
          {languages.data && !languages.data.some((l) => l.can_listen) ? (
            <p className="fineprint" style={{ marginTop: 10 }}>
              This server cannot read speech from a video yet. Subtitle and Clip still work —
              attach the video&rsquo;s .srt or its script and they will use that.
            </p>
          ) : null}
        </section>

        <div className="card pad">
          <div className="spread">
            <span>
              <b style={{ display: 'block', font: '500 15.5px/1.4 var(--ui)' }}>Series memory</b>
              <span className="muted" style={{ font: '400 13.5px/1.4 var(--ui)' }}>
                Keep the next video looking like this one
              </span>
            </span>
            <button
              role="switch"
              aria-checked={s.seriesMemory}
              onClick={() => s.setSeriesMemory(!s.seriesMemory)}
              style={{
                width: 50,
                height: 29,
                flex: 'none',
                padding: 3,
                borderRadius: 999,
                display: 'flex',
                justifyContent: s.seriesMemory ? 'flex-end' : 'flex-start',
                border: '1px solid var(--line-2)',
                background: s.seriesMemory
                  ? 'linear-gradient(135deg,var(--accent),var(--accent-2))'
                  : 'var(--panel-solid)',
                transition: 'background .18s',
              }}
            >
              <span
                style={{
                  width: 21,
                  height: 21,
                  borderRadius: 999,
                  background: s.seriesMemory ? '#2a1206' : 'var(--ink-3)',
                }}
              />
            </button>
          </div>
        </div>
      </div>
    </View>
  )
}

/** Caption samples carry literal CSS so they preview as the real caption styles. */
function cssToObject(css: string): Record<string, string> {
  return Object.fromEntries(
    css
      .split(';')
      .map((d) => d.split(/:(.+)/))
      .filter((p) => p.length > 1)
      .map(([k, v]) => [
        k!.trim().replace(/-([a-z])/g, (_, c: string) => c.toUpperCase()),
        v!.trim(),
      ]),
  )
}
