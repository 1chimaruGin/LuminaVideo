/**
 * The design mockup, running locally.
 *
 * Default view is the product UI only — the sidebar and the screen, filling the window.
 * The export wraps that in two layers of presentation scaffolding that would never ship:
 *
 *   - the review harness (screen nav + the Backdrop / Start input / Scene card / Cost
 *     variant switchers), which exists so a reviewer can flip between design options; and
 *   - a fake browser window (traffic lights and a lumina.app URL) drawn at a fixed
 *     1300x820 on a fixed artboard, which is why the raw export letterboxes on a wide
 *     screen and clips on a short one.
 *
 * Both are dropped here. Every screen is still reachable through the product's own
 * navigation: the sidebar covers Projects, Storyboard, Deliver, Identity kit, Credits and
 * Voice setup; "New short" opens Start; Start's Continue opens Recipe; and the account row
 * opens Profile.
 *
 * The app-only view also runs the export's styles through a refined type, colour and icon
 * ramp (see ./refine.ts) — the artboard's 9-12.5px text and 16px hairline icons do not hold
 * up at real display sizes.
 *
 * `?frame=1` restores the export exactly as designed, with no refinement and no layout
 * changes. That view stays pixel-identical to Lumina-Web.html, so it is the one to use when
 * checking fidelity rather than judging the UI.
 */
import { useCallback, useEffect, useState } from 'react'

import { useLuminaMock } from './useLuminaMock'
import { PrototypeShell } from './PrototypeShell'
import { PROTO_CSS } from './proto.css'
import { refineStyle } from './refine'
import { DESK_SCREEN_STYLE, STARS_STYLE, THEME_CSS } from './theme'
import { setStyleTransform, useProtoStylesheet } from '../lib/sx'

/**
 * Drops the scaffolding and lets the app fill the viewport.
 *
 * Targets the `data-lum` landmarks the converter tags, not DOM positions — positional
 * selectors would silently stop matching the first time the design export changes shape.
 */
const APP_ONLY_CSS = `
[data-lum='harness'] { display: none !important; }
[data-lum='titlebar'] { display: none !important; }

#root > div {
  padding: 0 !important;
  min-height: 100vh !important;
  display: flex !important;
  flex-direction: column !important;
}
#root > div > div:last-child {
  padding-top: 0 !important;
  flex: 1 1 auto !important;
  min-height: 0 !important;
}

/*
 * Layout layer.
 *
 * The export is a fixed artboard: a 1300px frame holding a 232px rail and a 680px column.
 * Used as a layout it fails at both ends — stranded space on a wide monitor, overflow on a
 * narrow one. These rules give each screen a layout appropriate to the space it has.
 */

/* Fill the viewport. Capping and centring the app just moves the dead space to the edges. */
[data-lum='window'] {
  border: 0 !important;
  border-radius: 0 !important;
  width: 100% !important;
  max-width: none !important;
}

/* The rail earns a little width on a large screen, but stays a rail. */
[data-lum='sidebar'] { width: clamp(196px, 14vw, 264px) !important; }

/*
 * Two kinds of screen, and they want opposite things.
 *
 * List screens (storyboard, projects, identity, credits, deliver, profile) have enough
 * content to fill the height, so they read top-down and take a wide column.
 */
[data-lum='col'] { max-width: min(1240px, 100%) !important; }

/*
 * Focused screens (start, voice setup, recipe) are a single decision each. Pinned to the
 * top of a tall viewport they leave a void underneath and read as unfinished — the Start
 * screen especially, which the brief calls the whole first impression. They get a narrower
 * measure and sit in the vertical centre.
 */
[data-screen='start'] [data-lum='col'],
[data-screen='onboard'] [data-lum='col'],
[data-screen='recipe'] [data-lum='col'] { max-width: min(760px, 100%) !important; }

/*
 * Let each screen fill the scroll area.
 *
 * The export sizes screen bodies with min-height:100%, which silently resolves to
 * nothing because the slot's own height is auto — so a short screen stops at its content
 * and the storyboard's sticky action bar floats in the middle of the page instead of
 * sitting at the bottom. A flex column gives the child a real height to grow into.
 */
#lum-slot-desktop { display: flex !important; flex-direction: column !important; }
#lum-slot-desktop > div { flex: 1 1 auto !important; min-height: 0 !important; }

/* Focused screens centre their content instead of stretching it. */
[data-screen='start'] #lum-slot-desktop,
[data-screen='onboard'] #lum-slot-desktop,
[data-screen='recipe'] #lum-slot-desktop { justify-content: center !important; }
[data-screen='start'] #lum-slot-desktop > div,
[data-screen='onboard'] #lum-slot-desktop > div,
[data-screen='recipe'] #lum-slot-desktop > div {
  flex: 0 0 auto !important;
  width: 100% !important;
}

/*
 * Storyboard: two columns once there is room.
 *
 * A scene card is a thumbnail, two lines of text, and a full-width action button. Stretched
 * to 1080px that button becomes a 940px bar and the card stops reading as a card. Flowing
 * the cards into an auto-fill grid keeps each one near the width it was designed at, halves
 * the scrolling, and uses the space a wide monitor actually has.
 *
 * The list is selected by its own style rather than :nth-child — it is the third of five
 * children, so a positional selector would break the moment a sibling is added.
 *
 * Tradeoff worth naming: design-brief.md calls the storyboard "a vertical list", and drag
 * reordering is less obvious in two dimensions than one. Below 1180px it stays a single
 * column, which is the case the brief actually specifies (mobile-first).
 */
[data-screen='board'] [data-lum='col'] { max-width: min(1280px, 100%) !important; }
[data-screen='board'] [data-lum='col'] > div[style*='gap: 11px'] {
  display: grid !important;
  grid-template-columns: repeat(auto-fill, minmax(520px, 1fr)) !important;
  align-items: start !important;
  gap: 12px !important;
}

/*
 * Icons.
 *
 * The export draws them at 16-19px with a 2px stroke, which at that size reads as a hairline
 * rather than a shape. Sizing up and thinning the stroke slightly gives them presence
 * without making them heavy. CSS wins over SVG presentation attributes, so the sizes in the
 * generated markup do not need touching.
 */
#root svg { stroke-width: 1.75 !important; }
#root svg[width='16'] { width: 19px !important; height: 19px !important; }
#root svg[width='17'] { width: 20px !important; height: 20px !important; }
#root svg[width='19'] { width: 22px !important; height: 22px !important; }

/*
 * Density.
 *
 * Larger type needs more room around it, or the gain in legibility is spent on crowding.
 * The rail's rows and the account card get the space back.
 */
[data-lum='sidebar'] button { padding: 11px 13px !important; gap: 12px !important; }
[data-lum='sidebar'] > div { gap: 3px !important; }

/* Interaction: the export defines hover only in places, and never a focus ring. */
[data-lum='sidebar'] button { transition: background .15s ease, color .15s ease; }
#root button:focus-visible,
#root textarea:focus-visible {
  outline: 2px solid rgba(255,195,107,.75) !important;
  outline-offset: 2px !important;
}

/* Below the artboard width the fixed rail is what breaks first. */
@media (max-width: 1100px) {
  [data-lum='sidebar'] { width: 196px !important; }
}
@media (max-width: 920px) {
  [data-lum='sidebar'] { width: 168px !important; }
}
`

const APP_ONLY_SHELL = 'width:100%;height:100vh;flex:none;box-shadow:none'

/** Screen padding, opened up so the title sits inside the horizon rather than against it. */
const APP_ONLY_PAD = 'padding:54px 48px 64px'
const APP_ONLY_BOARD_PAD = 'flex:1;padding:44px 44px 24px'

/**
 * The sticky action bar's inner width comes from the logic, not the markup, so it cannot be
 * reached by a landmark selector. It must track the column or the bar's contents sit at
 * 680px while the cards above them are 900px wide.
 */
/**
 * The sticky action bar's inner width comes from the logic, not the markup, so no landmark
 * selector can reach it. It tracks the storyboard column — the only screen that shows it —
 * or its contents sit at 680px beneath much wider cards.
 */
const APP_ONLY_BAR =
  'max-width:min(1280px, 100%);margin:0 auto;display:flex;gap:12px;align-items:center'

/** `?frame=1` shows the raw export; anything else shows the product UI. */
function useFrameMode(): [boolean, () => void] {
  const [framed, setFramed] = useState(
    () => new URLSearchParams(window.location.search).get('frame') === '1',
  )
  const toggle = useCallback(() => {
    setFramed((on) => {
      const next = !on
      const url = new URL(window.location.href)
      if (next) url.searchParams.set('frame', '1')
      else url.searchParams.delete('frame')
      window.history.replaceState(null, '', url) // shareable, and survives reload
      return next
    })
  }, [])
  return [framed, toggle]
}

const SCREENS = [
  'onboard', 'start', 'projects', 'recipe', 'board', 'deliver', 'identity', 'credits', 'profile',
] as const

/** Which screen the mock is on, as a `data-screen` attribute CSS can target. */
function useActiveScreen(v: Record<string, unknown>, enabled: boolean): void {
  const active = SCREENS.find((k) => v[`is${k[0]!.toUpperCase()}${k.slice(1)}`])
  useEffect(() => {
    const root = document.documentElement
    if (!enabled || !active) {
      delete root.dataset.screen
      return
    }
    root.dataset.screen = active
    return () => {
      delete root.dataset.screen
    }
  }, [active, enabled])
}

export function PrototypeScreen() {
  const v = useLuminaMock()
  const [framed, toggleFrame] = useFrameMode()

  // Applied during render, not in an effect: `sx` reads it while the tree below is being
  // built, so an effect would leave the first frame after a toggle using the old ramp.
  // `setStyleTransform` no-ops when the function is unchanged, so the style cache survives.
  setStyleTransform(framed ? null : refineStyle)

  useActiveScreen(v, !framed)
  useProtoStylesheet(PROTO_CSS)
  useProtoStylesheet(framed ? '' : THEME_CSS)
  useEffect(() => {
    if (framed) return
    const el = document.createElement('style')
    el.textContent = APP_ONLY_CSS
    document.head.appendChild(el)
    return () => el.remove()
  }, [framed])

  return (
    <>
      <PrototypeShell
        v={
          framed
            ? v
            : {
                ...v,
                shellStyle: APP_ONLY_SHELL,
                barInnerStyle: APP_ONLY_BAR,
                padStyle: APP_ONLY_PAD,
                boardPadStyle: APP_ONLY_BOARD_PAD,
                starsStyle: STARS_STYLE,
                deskScreenStyle: DESK_SCREEN_STYLE,
              }
        }
      />
      <button
        data-testid="mock-mode-toggle"
        onClick={toggleFrame}
        title={
          framed
            ? 'Show the product UI only, filling the window'
            : 'Show the raw design export, including the review harness'
        }
        style={{
          // Top-right: at narrow widths the sticky action bar owns the bottom corners,
          // and this button would sit on top of "Save this draft".
          position: 'fixed',
          right: '8px',
          top: '8px',
          zIndex: 99999,
          padding: '7px 11px',
          borderRadius: '9px',
          cursor: 'pointer',
          opacity: 0.55,
          border: '1px solid rgba(150,170,230,.24)',
          background: 'rgba(18,22,42,.86)',
          color: '#aab4d4',
          font: "500 11px/1.4 'IBM Plex Sans', system-ui, sans-serif",
          backdropFilter: 'blur(8px)',
        }}
      >
        {framed ? 'App only' : 'Design export'}
      </button>
    </>
  )
}
