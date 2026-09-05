/**
 * Icon set.
 *
 * One geometry for all of them: a 24px box, 1.75 stroke, round caps and joins. The export's
 * icons were 16-19px at 2px stroke, which at that size reads as a hairline rather than a
 * shape — the weight has to come down as the size goes up, not the other way around.
 */
import type { SVGProps } from 'react'

type Props = SVGProps<SVGSVGElement> & { size?: number }

function Icon({ size = 20, children, ...rest }: Props) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      {children}
    </svg>
  )
}

export const IconGrid = (p: Props) => (
  <Icon {...p}>
    <rect x="3" y="3" width="7" height="7" rx="2" />
    <rect x="14" y="3" width="7" height="7" rx="2" />
    <rect x="3" y="14" width="7" height="7" rx="2" />
    <rect x="14" y="14" width="7" height="7" rx="2" />
  </Icon>
)

export const IconFilm = (p: Props) => (
  <Icon {...p}>
    <rect x="2.5" y="4" width="19" height="16" rx="2.5" />
    <path d="M8 4v16M16 4v16M2.5 12h19M2.5 8h5.5M2.5 16h5.5M16 8h5.5M16 16h5.5" />
  </Icon>
)

export const IconSend = (p: Props) => (
  <Icon {...p}>
    <path d="M21 3 10.5 13.5" />
    <path d="M21 3l-6.5 18-4-8-8-4L21 3z" />
  </Icon>
)

export const IconWand = (p: Props) => (
  <Icon {...p}>
    <path d="M4 20 16 8" />
    <path d="m18 2-1 3-3 1 3 1 1 3 1-3 3-1-3-1-1-3zM6 9l-.7 2L3 11.7l2.3.7L6 15l.7-2.3L9 11.7 6.7 11 6 9z" />
  </Icon>
)

export const IconCoin = (p: Props) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7.5v9M9.5 10a2.5 2.5 0 0 1 2.5-2.5h.5a2 2 0 0 1 0 4h-1a2 2 0 0 0 0 4h.5a2.5 2.5 0 0 0 2.5-2.5" />
  </Icon>
)

/** Listening — a soundwave, for the stage that reads speech out of a video. */
export const IconEar = (p: Props) => (
  <Icon {...p}>
    <path d="M4 10.5v3M8 7v10M12 4.5v15M16 8v8M20 10.5v3" />
  </Icon>
)

export const IconMic = (p: Props) => (
  <Icon {...p}>
    <rect x="9" y="2.5" width="6" height="11" rx="3" />
    <path d="M5 11a7 7 0 0 0 14 0M12 18v3.5" />
  </Icon>
)

/**
 * A speaker with sound coming out of it — the *voice* stage.
 *
 * Not a microphone. Nothing in this pipeline ever listens through one: the voice stage
 * synthesizes speech, it does not record it, and a mic on that step reads as "we are about to
 * capture your audio" to everyone who has used any other app.
 */
export const IconSpeak = (p: Props) => (
  <Icon {...p}>
    <path d="M4 9.5h3.5L12 5.5v13L7.5 14.5H4z" />
    <path d="M15.5 9.5a4 4 0 0 1 0 5M18 7a7.5 7.5 0 0 1 0 10" />
  </Icon>
)

export const IconPlus = (p: Props) => (
  <Icon {...p}>
    <path d="M12 5v14M5 12h14" />
  </Icon>
)

export const IconPlay = (p: Props) => (
  <Icon {...p}>
    <path d="M7 4.5v15l13-7.5-13-7.5z" />
  </Icon>
)

export const IconMore = (p: Props) => (
  <Icon {...p}>
    <circle cx="5" cy="12" r="1.4" fill="currentColor" stroke="none" />
    <circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none" />
    <circle cx="19" cy="12" r="1.4" fill="currentColor" stroke="none" />
  </Icon>
)

export const IconCheck = (p: Props) => (
  <Icon {...p}>
    <path d="m4.5 12.5 5 5 10-11" />
  </Icon>
)

export const IconChevron = (p: Props) => (
  <Icon {...p}>
    <path d="m9 5 7 7-7 7" />
  </Icon>
)

export const IconSpark = (p: Props) => (
  <Icon {...p}>
    <path d="M12 2.5 14.2 9 21 11.2 14.2 13.4 12 20l-2.2-6.6L3 11.2 9.8 9 12 2.5z" />
  </Icon>
)

export const IconPaperclip = (p: Props) => (
  <Icon {...p}>
    <path d="M20 11.5 12 19.5a5 5 0 0 1-7-7l8.5-8.5a3.4 3.4 0 0 1 4.8 4.8L9.7 17.3a1.8 1.8 0 0 1-2.5-2.5l7.8-7.8" />
  </Icon>
)

export const IconInfo = (p: Props) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 11v5.5M12 7.6v.1" />
  </Icon>
)

export const IconDownload = (p: Props) => (
  <Icon {...p}>
    <path d="M12 3.5v11M7.5 10.5 12 15l4.5-4.5M4 19h16" />
  </Icon>
)

export const IconRefresh = (p: Props) => (
  <Icon {...p}>
    <path d="M20.5 12a8.5 8.5 0 1 1-2.6-6.1M20.5 4v5h-5" />
  </Icon>
)

export const IconTrash = (p: Props) => (
  <Icon {...p}>
    <path d="M4 6.5h16M9.5 6.5V4.8A1.3 1.3 0 0 1 10.8 3.5h2.4a1.3 1.3 0 0 1 1.3 1.3v1.7M6.5 6.5 7.4 20a1.5 1.5 0 0 0 1.5 1.4h6.2a1.5 1.5 0 0 0 1.5-1.4l.9-13.5" />
  </Icon>
)

export const IconType = (p: Props) => (
  <Icon {...p}>
    <path d="M4 6.5V4.5h16v2M12 4.5v15M8.5 19.5h7" />
  </Icon>
)

export const IconBack = (p: Props) => (
  <Icon {...p}>
    <path d="M15 5l-7 7 7 7" />
  </Icon>
)

export const IconMenu = (p: Props) => (
  <Icon {...p}>
    <path d="M4 7h16M4 12h16M4 17h16" />
  </Icon>
)

export const IconClose = (p: Props) => (
  <Icon {...p}>
    <path d="M6 6l12 12M18 6L6 18" />
  </Icon>
)

export const IconUser = (p: Props) => (
  <Icon {...p}>
    <circle cx="12" cy="8" r="4" />
    <path d="M4.5 20a7.5 7.5 0 0 1 15 0" />
  </Icon>
)

export const IconUpload = (p: Props) => (
  <Icon {...p}>
    <path d="M12 20.5v-11M7.5 13.5 12 9l4.5 4.5M4 4.5h16" />
  </Icon>
)

export const IconClock = (p: Props) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7v5.5l3.5 2" />
  </Icon>
)

export const IconLayers = (p: Props) => (
  <Icon {...p}>
    <path d="m12 3 9 5-9 5-9-5 9-5zM3 13l9 5 9-5M3 17.5l9 5 9-5" />
  </Icon>
)
