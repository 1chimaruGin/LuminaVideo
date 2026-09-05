/**
 * Choosing how a dub sounds, and hearing it before committing to it.
 *
 * This lives on the preview screen rather than on the brief, and the reason is the whole
 * point of the control: a voice cannot be judged in the abstract. It was asked for up front,
 * beside the target language, which put the question before the creator had anything to judge
 * it against — no transcript, no footage, no sense of the pace. Here they can hear it over
 * their own video, change it, and hear it again.
 *
 * It was a row of seven cards, and that was wrong twice over. It took the width of the whole
 * editor for a single choice, pushing the caption controls down; and seven equal buttons read
 * as seven things to do rather than as one question with seven answers. A select says "pick
 * one" in a shape everybody already knows, sits in the same row as the controls it belongs
 * with, and matches the language pickers one screen back — which are the same kind of
 * decision. The play button beside it is separate on purpose: auditioning a voice and
 * committing to it must not be the same click.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { voiceSampleUrl } from '../api/client'
import { useChooseVoice, useVoices } from '../api/queries'

export function Voices({
  projectId,
  language,
  picked,
}: {
  projectId: string
  /** The language being dubbed *into* — what the sample should read. */
  language: string
  picked: string | null
}) {
  const voices = useVoices(language)
  const choose = useChooseVoice(projectId)
  const [playing, setPlaying] = useState(false)
  const audio = useRef<HTMLAudioElement | null>(null)

  //: Stop the sample if this screen goes away mid-play. Without it the voice keeps talking
  //: over the next screen, which is a startling way to leave a page.
  useEffect(() => () => audio.current?.pause(), [])

  const play = useCallback(() => {
    if (!picked) return
    audio.current?.pause()
    const el = new Audio(voiceSampleUrl(picked, language))
    audio.current = el
    el.onended = () => setPlaying(false)
    el.onerror = () => setPlaying(false)
    setPlaying(true)
    void el.play().catch(() => setPlaying(false))
  }, [picked, language])

  if (!voices.data?.length) return null

  const usable = voices.data.filter((v) => !v.unavailable)
  const blocked = voices.data.filter((v) => v.unavailable)

  return (
    <label className="lang voices">
      <span>Spoken by</span>
      <span className="voices-pick">
        <select
          value={picked ?? ''}
          disabled={choose.isPending}
          onChange={(e) => choose.mutate(e.target.value || null)}
        >
          <option value="">Choose a voice…</option>
          {usable.map((v) => (
            <option key={v.id} value={v.id}>
              {v.character}
            </option>
          ))}
          {/*
           * Listed, and listed as unpickable with the reason attached. Someone who recorded
           * sixty seconds of themselves for this product will come looking for it, and an
           * option that is simply absent reads as a bug where one that says why reads as a
           * roadmap.
           */}
          {blocked.length ? (
            <optgroup label="Not available yet">
              {blocked.map((v) => (
                <option key={v.id} value={v.id} disabled>
                  {v.character} — {v.unavailable}
                </option>
              ))}
            </optgroup>
          ) : null}
        </select>
        <button
          type="button"
          className="voice-play"
          disabled={!picked}
          aria-label="Hear a sample of this voice"
          title={picked ? 'Hear this voice read a line' : 'Choose a voice first'}
          data-on={playing}
          onClick={play}
        >
          {playing ? '■' : '▶'}
        </button>
      </span>
    </label>
  )
}
