/**
 * Recipe pictograms.
 *
 * Each recipe is drawn as what-you-give → what-you-get, because that is the actual decision
 * and it is one a picture makes faster than a sentence. The audience is multilingual, often
 * on a phone, often moving fast; a screen that has to be *read* to be understood excludes
 * people who read the interface language poorly or not at all.
 *
 * Deliberately literal rather than abstract: a long bar becoming three short ones says
 * "clipping" to someone who has never seen this product. A clever mark does not.
 */
import type { ReactElement, SVGProps } from 'react'

type Props = SVGProps<SVGSVGElement>

const W = 132
const H = 60

function Frame({ children, ...rest }: Props) {
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      fill="none"
      aria-hidden="true"
      // Sized in CSS, not with attributes: `height="auto"` is a valid CSS value but not a
      // valid SVG length, so the attribute form is rejected and the intrinsic ratio is lost.
      style={{ display: 'block', width: '100%', height: 'auto' }}
      {...rest}
    >
      {children}
    </svg>
  )
}

/** The arrow between input and output. Same in every pictogram, so it reads as "becomes". */
const Arrow = () => (
  <g stroke="var(--ink-4)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M58 30h14" />
    <path d="M68 26l4 4-4 4" />
  </g>
)

/** A phone-shaped result. Amber, because the output is always the thing you get back. */
const Phone = ({ x = 86, cap = false, play = false }) => (
  <g>
    <rect
      x={x}
      y="10"
      width="26"
      height="40"
      rx="5"
      fill="rgba(255,195,107,.12)"
      stroke="var(--accent)"
      strokeWidth="1.5"
    />
    {play && <path d={`M${x + 10} 25l7 5-7 5z`} fill="var(--accent)" />}
    {cap && (
      <g fill="var(--accent)">
        <rect x={x + 5} y="36" width="16" height="3.5" rx="1.75" />
        <rect x={x + 8} y="42" width="10" height="3.5" rx="1.75" />
      </g>
    )}
  </g>
)

export const ArtExplainer = (p: Props) => (
  <Frame {...p}>
    {/* a document or link, becoming a video */}
    <g stroke="var(--ink-3)" strokeWidth="1.5" strokeLinecap="round">
      <rect x="14" y="12" width="30" height="36" rx="4" />
      <path d="M20 22h18M20 28h18M20 34h11" />
    </g>
    <Arrow />
    <Phone play />
  </Frame>
)

export const ArtNarrate = (p: Props) => (
  <Frame {...p}>
    {/* written lines becoming a voice over a frame */}
    <g stroke="var(--ink-3)" strokeWidth="1.5" strokeLinecap="round">
      <path d="M12 18h20M12 24h20M12 30h13" />
    </g>
    <g stroke="var(--accent)" strokeWidth="2" strokeLinecap="round">
      <path d="M14 44v-4M19 46v-8M24 43v-2M29 47v-10M34 44v-4" />
    </g>
    <Arrow />
    <Phone play />
  </Frame>
)

export const ArtDub = (p: Props) => (
  <Frame {...p}>
    {/* a video whose sound is replaced: the same frame, a new waveform under it */}
    <rect x="8" y="14" width="44" height="24" rx="4" fill="rgba(160,180,235,.14)" />
    <path d="M25 21l10 5-10 5z" fill="var(--ink-3)" />
    <g stroke="var(--accent)" strokeWidth="2" strokeLinecap="round">
      <path d="M14 46v-5M20 48v-9M26 45v-3M32 49v-11M38 46v-5M44 47v-7" />
    </g>
    <Arrow />
    <Phone play />
  </Frame>
)

export const ArtClip = (p: Props) => (
  <Frame {...p}>
    {/* one long timeline, with the good parts lit, becoming several short ones */}
    <rect x="8" y="24" width="44" height="12" rx="3" fill="rgba(160,180,235,.14)" />
    <rect x="13" y="24" width="9" height="12" rx="3" fill="var(--accent)" />
    <rect x="29" y="24" width="7" height="12" rx="3" fill="var(--accent)" />
    <rect x="42" y="24" width="6" height="12" rx="3" fill="var(--accent)" />
    <Arrow />
    <g>
      <rect
        x="80"
        y="16"
        width="12"
        height="28"
        rx="3"
        fill="rgba(255,195,107,.14)"
        stroke="var(--accent)"
        strokeWidth="1.4"
      />
      <rect
        x="95"
        y="16"
        width="12"
        height="28"
        rx="3"
        fill="rgba(255,195,107,.14)"
        stroke="var(--accent)"
        strokeWidth="1.4"
      />
      <rect
        x="110"
        y="16"
        width="12"
        height="28"
        rx="3"
        fill="rgba(255,195,107,.14)"
        stroke="var(--accent)"
        strokeWidth="1.4"
      />
    </g>
  </Frame>
)

export const ArtSubtitles = (p: Props) => (
  <Frame {...p}>
    {/* the same video, now with words on it — nothing else changes, which is the point */}
    <rect
      x="16"
      y="10"
      width="26"
      height="40"
      rx="5"
      fill="rgba(160,180,235,.1)"
      stroke="var(--ink-3)"
      strokeWidth="1.5"
    />
    <path d="M26 25l7 5-7 5z" fill="var(--ink-3)" />
    <Arrow />
    <Phone cap />
  </Frame>
)

export const RECIPE_ART: Record<string, (p: Props) => ReactElement> = {
  explainer: ArtExplainer,
  narrate: ArtNarrate,
  dub: ArtDub,
  clip_long_video: ArtClip,
  subtitle_only: ArtSubtitles,
}
