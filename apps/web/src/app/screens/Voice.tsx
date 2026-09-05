/**
 * Voice setup.
 *
 * Sixty seconds, once. `design-brief.md` calls this the strongest retention hook, and it is
 * the one part of voice that needs no model behind it — the browser records, the audio is
 * stored, and it attaches to the channel so every later video can use it.
 *
 * The waveform is measured from the real signal. It used to be `Math.sin()`, which moved
 * whether or not the microphone was working — and this bar is the only thing telling someone
 * "we can hear you", so a decorative one says it when we cannot.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { useEditChannel, useUpload } from '../../api/queries'
import { Recorder, TARGET_SECONDS, fileFor } from '../recorder'
import type { Studio } from '../live'
import { Head, View } from '../Shell'
import { IconCheck, IconMic } from '../icons'

const BARS = Array.from({ length: 40 }, (_, i) => i)

type Stage = 'idle' | 'recording' | 'saving' | 'done' | 'blocked'

export function Voice({ s }: { s: Studio }) {
  const [stage, setStage] = useState<Stage>(s.voiceReady ? 'done' : 'idle')
  const [seconds, setSeconds] = useState(0)
  const [level, setLevel] = useState(0)
  const [problem, setProblem] = useState<string | null>(null)

  const recorder = useRef(new Recorder())
  const upload = useUpload()
  const editChannel = useEditChannel()
  const supported = Recorder.supported()

  /** Give the microphone back if this screen goes away mid-recording. */
  useEffect(() => {
    const active = recorder.current
    return () => active.release()
  }, [])

  useEffect(() => {
    if (stage !== 'recording') return
    const tick = setInterval(() => setSeconds((n) => n + 1), 1000)
    return () => clearInterval(tick)
  }, [stage])

  const finish = useCallback(async () => {
    setStage('saving')
    try {
      const blob = await recorder.current.stop()
      const asset = await upload.mutateAsync(fileFor(blob))
      // Onto the channel, which is what makes it inherit into every later project.
      await editChannel.mutateAsync({ voice_profile_asset_id: asset.asset_id })
      setStage('done')
    } catch {
      setProblem('That recording could not be saved. Try again.')
      setStage('idle')
      setSeconds(0)
    }
  }, [upload, editChannel])

  // Stop on its own at a minute. Holding someone's microphone open past what was asked for
  // is not something to leave to them noticing.
  useEffect(() => {
    if (stage === 'recording' && seconds >= TARGET_SECONDS) void finish()
  }, [stage, seconds, finish])

  const begin = async () => {
    setProblem(null)
    setSeconds(0)
    try {
      await recorder.current.start(setLevel)
      setStage('recording')
    } catch (err) {
      // Refused, or no microphone. Both are the same to the person: nothing will happen.
      const denied = (err as { name?: string })?.name === 'NotAllowedError'
      setProblem(
        denied
          ? 'Lumina needs permission to use your microphone. Allow it in your browser, then try again.'
          : 'No microphone found.',
      )
      setStage('blocked')
    }
  }

  const done = stage === 'done'
  const recording = stage === 'recording'
  const busy = stage === 'saving'

  return (
    <View
      center
      mid
      action={
        <button className="btn block" onClick={() => s.go('identity')} disabled={busy}>
          {done ? 'Done' : 'Use a stock voice instead'}
        </button>
      }
    >
      <Head
        kicker="One minute"
        title={done ? 'Your voice is saved' : 'Read this out loud'}
        lede={
          done
            ? 'Every video can be narrated in it from now on. Record again any time.'
            : 'Then every video can be narrated in your own voice, without recording again.'
        }
      />

      <div className="card lit pad" style={{ textAlign: 'center' }}>
        <p
          style={{
            margin: '10px 0 26px',
            font: '500 clamp(17px, 2vw, 21px)/1.6 var(--display)',
            textWrap: 'balance',
          }}
        >
          “I make short videos about space, and I want them to sound like me — not like a robot
          reading a script.”
        </p>

        <div
          aria-hidden="true"
          style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 3, height: 44 }}
        >
          {BARS.map((i) => (
            <i
              key={i}
              style={{
                width: 3,
                borderRadius: 2,
                background: 'linear-gradient(180deg,var(--accent),rgba(255,138,61,.35))',
                // Measured, not animated. The bars nearest the middle react most, so the
                // shape reads as a voice rather than a bar chart.
                height: recording
                  ? `${14 + level * 86 * (0.45 + 0.55 * Math.cos(((i - 20) / 20) * 1.3))}%`
                  : '14%',
                opacity: recording ? 1 : 0.32,
                transition: 'height .09s linear',
              }}
            />
          ))}
        </div>

        <button
          onClick={() => {
            if (busy) return
            if (recording) void finish()
            else void begin()
          }}
          disabled={!supported || busy}
          aria-label={recording ? 'Stop recording' : 'Start recording'}
          style={{
            width: 82,
            height: 82,
            margin: '26px auto 0',
            borderRadius: 999,
            display: 'grid',
            placeItems: 'center',
            color: 'var(--accent-ink)',
            background: 'linear-gradient(140deg,var(--accent),var(--accent-2))',
            boxShadow: '0 0 0 8px rgba(255,195,107,.08), 0 10px 34px -8px rgba(255,138,61,.7)',
            opacity: supported && !busy ? 1 : 0.5,
          }}
        >
          {done ? <IconCheck size={30} /> : recording ? <StopMark /> : <IconMic size={30} />}
        </button>

        <div style={{ marginTop: 18, font: '600 20px/1 var(--mono)', color: 'var(--accent)' }}>
          {clock(seconds)} / {clock(TARGET_SECONDS)}
        </div>
        <p className="muted" style={{ margin: '10px 0 0', font: '400 14px/1.5 var(--ui)' }}>
          {!supported
            ? 'This browser cannot record audio. Try Chrome, or open Lumina over https.'
            : busy
              ? 'Saving your voice…'
              : done
                ? 'Got it — your voice is ready.'
                : recording
                  ? 'Listening… tap again when you are done.'
                  : 'Tap to start recording.'}
        </p>
        {problem ? (
          <p className="fineprint" data-bad role="alert" style={{ marginTop: 10 }}>
            {problem}
          </p>
        ) : null}
      </div>

      <p
        className="muted"
        style={{ margin: '18px 0 0', textAlign: 'center', font: '400 13.5px/1.6 var(--ui)' }}
      >
        Only you can use this voice. Delete it any time.
      </p>
    </View>
  )
}

function clock(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

/** No stop glyph in the icon set, and one square does not earn a file. */
function StopMark() {
  return (
    <svg width="26" height="26" viewBox="0 0 26 26" aria-hidden="true">
      <rect x="7" y="7" width="12" height="12" rx="2.5" fill="currentColor" />
    </svg>
  )
}
