/**
 * Recording the creator's voice.
 *
 * `design-brief.md` calls the voice profile "the strongest retention hook", and it is the one
 * part of voice that needs no model behind it: the browser records, we store the audio, and it
 * attaches to the channel. Turning that into text-to-speech is a provider's job; capturing it
 * is ours, and it was the piece that did not exist.
 *
 * Deliberately a plain object rather than a hook: a MediaRecorder outlives React's render
 * cycle, and a microphone left open because an effect re-ran is a light on someone's laptop
 * that should not be on.
 */

/** How long a voice profile needs to be. Enough for a model to have something to work with. */
export const TARGET_SECONDS = 60

export type Level = number

export class Recorder {
  private stream: MediaStream | null = null
  private recorder: MediaRecorder | null = null
  private chunks: Blob[] = []
  private audio: AudioContext | null = null
  private analyser: AnalyserNode | null = null
  private frame = 0

  /** Whether this browser can record at all. Safari on http, and any insecure origin, cannot. */
  static supported(): boolean {
    return (
      typeof navigator !== 'undefined' &&
      !!navigator.mediaDevices?.getUserMedia &&
      typeof MediaRecorder !== 'undefined'
    )
  }

  /**
   * Start recording, and report the level about 20 times a second.
   *
   * The level is measured from the real signal rather than animated, because a waveform that
   * moves whether or not the microphone is working is worse than none: it is the only feedback
   * saying "we can hear you", and a fake one says it when we cannot.
   */
  async start(onLevel: (level: Level) => void): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
    })

    this.chunks = []
    this.recorder = new MediaRecorder(this.stream, { mimeType: pickMime() })
    this.recorder.ondataavailable = (e) => {
      if (e.data.size) this.chunks.push(e.data)
    }
    this.recorder.start(250)

    this.audio = new AudioContext()
    this.analyser = this.audio.createAnalyser()
    this.analyser.fftSize = 512
    this.audio.createMediaStreamSource(this.stream).connect(this.analyser)

    const buffer = new Uint8Array(this.analyser.frequencyBinCount)
    const tick = () => {
      if (!this.analyser) return
      this.analyser.getByteTimeDomainData(buffer)
      // Root mean square around the 128 midpoint: loudness, not a single sample, so a
      // waveform crossing zero does not read as silence.
      let sum = 0
      for (const v of buffer) sum += (v - 128) ** 2
      onLevel(Math.min(1, Math.sqrt(sum / buffer.length) / 40))
      this.frame = requestAnimationFrame(tick)
    }
    tick()
  }

  /** Stop, and hand back what was recorded. */
  async stop(): Promise<Blob> {
    const recorder = this.recorder
    if (!recorder) throw new Error('not recording')

    const done = new Promise<Blob>((resolve) => {
      recorder.onstop = () => resolve(new Blob(this.chunks, { type: recorder.mimeType }))
    })
    recorder.stop()
    const blob = await done
    this.release()
    return blob
  }

  /**
   * Give the microphone back.
   *
   * Every track, the animation frame and the AudioContext. Missing any one leaves the
   * recording indicator lit, which people reasonably read as still being listened to.
   */
  release(): void {
    cancelAnimationFrame(this.frame)
    this.stream?.getTracks().forEach((t) => t.stop())
    void this.audio?.close()
    this.stream = null
    this.recorder = null
    this.analyser = null
    this.audio = null
  }
}

/**
 * The first container this browser will actually record.
 *
 * Chrome and Firefox give webm/opus; Safari only ever gives mp4. Passing a mimeType it does
 * not support throws, and passing none leaves the choice undefined — so it is asked.
 */
function pickMime(): string {
  const wanted = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg']
  return wanted.find((m) => MediaRecorder.isTypeSupported(m)) ?? ''
}

/** What to call the uploaded file, from what the browser chose to record. */
export function fileFor(blob: Blob): File {
  const ext = blob.type.includes('mp4') ? 'm4a' : blob.type.includes('ogg') ? 'ogg' : 'webm'
  return new File([blob], `voice.${ext}`, { type: blob.type || 'audio/webm' })
}
