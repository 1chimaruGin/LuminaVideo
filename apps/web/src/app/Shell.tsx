/**
 * The flow.
 *
 * Three fixed regions: context on top, the work in the middle, the one action at the bottom
 * in the thumb zone. Navigation is progress through the funnel — Start, Recipe, Storyboard,
 * Deliver — shown as four segments rather than a menu. Anything outside the funnel opens as
 * a sheet, so it stays a detour instead of becoming a rail.
 */
import { useState, type ReactNode } from 'react'

import { afterBrief, homeFor, stepsFor, type Screen } from './data'
import { useLiveStudio, type Studio } from './live'
import { Cosmos } from './Cosmos'
import markUrl from '../assets/lumina-disc.png'
import { Menu } from './screens/Menu'
import { Start } from './screens/Start'
import { Brief } from './screens/Brief'
import { Board } from './screens/Board'
import { Deliver } from './screens/Deliver'
import { Projects } from './screens/Projects'
import { Identity } from './screens/Identity'
import { Credits } from './screens/Credits'
import { Voice } from './screens/Voice'
import { Auth } from './screens/Auth'
import { Profile } from './screens/Profile'
import { Script } from './screens/Script'
import { Preview } from './screens/Preview'
import { SceneDetail } from './screens/Scene'
import { Clips } from './screens/Clips'
import { Captions } from './screens/Captions'
import { Jobs } from './screens/Jobs'
import { IconBack, IconCoin, IconMenu, IconRefresh } from './icons'
import './app.css'

export function App() {
  const s = useLiveStudio()
  const [menu, setMenu] = useState(false)
  const [jobs, setJobs] = useState(false)

  // Per lane, because the lanes do not walk the same screens.
  const steps = stepsFor(s.recipe)
  const step = steps.indexOf(s.screen)
  const inFunnel = step >= 0

  if (!s.signedIn) {
    return (
      <div className="shell">
        <Cosmos />
        <div />
        <Auth onDone={s.signIn} />
      </div>
    )
  }

  return (
    <div className="shell">
      <Cosmos />

      <header className="topbar">
        {/*
          * Back and the mark, not back OR the mark.
          *
          * The brand used to be swapped out for the back button, so it appeared on exactly
          * one screen and vanished the moment anyone navigated — which reads as a different
          * app rather than a page of the same one. The wordmark drops away on a narrow
          * screen; the mark never does.
          */}
        <div className="lead">
          {s.canGoBack ? (
            <button className="iconbtn" onClick={s.back} aria-label="Back">
              <IconBack size={20} />
            </button>
          ) : null}
          <button className="brand" onClick={() => s.go('start')} aria-label="Lumina — home">
            <img src={markUrl} alt="" />
            <b>Lumina</b>
          </button>
        </div>

        {/*
          * The project's name, once there is a project *and* you are working on it.
          *
          * Before that it said "Untitled" — the placeholder the studio falls back to — which
          * put a meaningless word at the top of the first screen anyone sees. A screen with
          * nothing to name says what it is instead.
          *
          * Not on Start, even when a project is remembered. Start is the first step of every
          * funnel, so `inFunnel` is true there, and the id survives a reload — which put the
          * *previous* video's title above the screen where you choose the next one. Picking a
          * task clears it, but reading the screen comes first, and until then the header was
          * naming something you had already moved on from.
          */}
        <div className="who">
          <b>
            {inFunnel && s.projectId && s.screen !== 'start' ? s.project : TITLES[s.screen]}
          </b>
          <span>{inFunnel ? `Step ${step + 1} of ${steps.length}` : 'Lumina'}</span>
        </div>


        <button
          className="iconbtn"
          onClick={() => setJobs(true)}
          aria-label="Work in progress"
          title="Work in progress"
        >
          <IconRefresh size={19} />
        </button>
        <button className="pill" onClick={() => s.go('credits')} title="Credits">
          <IconCoin size={16} />
          {s.balance}
        </button>
        <button className="iconbtn" onClick={() => setMenu(true)} aria-label="Menu">
          <IconMenu size={20} />
        </button>
      </header>

      {inFunnel ? (
        <div className="steps" aria-hidden="true">
          {steps.map((id, i) => (
            <span key={id} className={`step ${i < step ? 'done' : i === step ? 'now' : ''}`}>
              <i />
            </span>
          ))}
        </div>
      ) : (
        <div />
      )}

      <Screen s={s} />

      {menu ? <Menu s={s} onClose={() => setMenu(false)} /> : null}
      {jobs ? <Jobs onClose={() => setJobs(false)} projectId={s.projectId} /> : null}
    </div>
  )
}

const TITLES: Record<Screen, string> = {
  preview: 'Preview',
  auth: 'Lumina',
  profile: 'Account',
  script: 'Script',
  scene: 'Scene',
  clips: 'Moments',
  captions: 'Subtitles',
  start: 'New short',
  recipe: 'Your brief',
  board: 'Storyboard',
  deliver: 'Deliver',
  projects: 'Your videos',
  identity: 'Identity kit',
  credits: 'Credits',
  voice: 'Voice setup',
}

function Screen({ s }: { s: Studio }) {
  switch (s.screen) {
    case 'start':
      return <Start s={s} onNext={() => s.go('recipe')} />
    case 'recipe':
      return (
        //: The lane's own next step, from its funnel — see `afterBrief`.
        <Brief s={s} onNext={() => s.go(afterBrief(s.recipe))} />
      )
    case 'script':
      return <Script s={s} />
    case 'preview':
      return <Preview s={s} />
    case 'scene':
      return <SceneDetail s={s} />
    case 'clips':
      return <Clips s={s} onNext={() => s.go('board')} />
    case 'captions':
      return <Captions s={s} />
    case 'deliver':
      return <Deliver s={s} />
    case 'projects':
      return (
        <Projects
          onOpen={(id, recipe) => {
            s.setProjectId(id)
            //: Its own lane's working screen, not the storyboard — see `homeFor`.
            s.go(homeFor(recipe))
          }}
          onNew={() => s.go('start')}
        />
      )
    case 'identity':
      return <Identity s={s} />
    case 'credits':
      return <Credits balance={s.balance} onBuy={s.buy} />
    case 'voice':
      return <Voice s={s} />
    case 'auth':
      return <Auth onDone={s.signIn} />
    case 'profile':
      return <Profile s={s} onSignOut={s.signOut} />
    default:
      return <Board s={s} />
  }
}

/** Shared scaffold so every screen gets the same scroll region and action bar. */
export function View({
  children,
  center = false,
  mid = false,
  wide = false,
  full = false,
  action,
}: {
  children: ReactNode
  center?: boolean
  mid?: boolean
  /** Two working panels side by side. The reading measure is wrong for an editor. */
  wide?: boolean
  /**
   * An editor that owns the window: full width, full height, and it does its own scrolling.
   *
   * A reading measure and a page scroll are right for a screen you read and wrong for one you
   * work in — they leave the thing being worked on small and stranded, and give the page a
   * scrollbar next to the panel's own.
   */
  full?: boolean
  action?: ReactNode
}) {
  return (
    <>
      <div className={`view${center ? ' center' : ''}${full ? ' full' : ''}`}>
        <div className={`view-inner${mid ? ' mid' : ''}${wide ? ' wide' : ''}${full ? ' full' : ''}`}>
          {children}
        </div>
      </div>
      {action ? <div className="actionbar">{action}</div> : <div />}
    </>
  )
}

export function Head({
  kicker,
  title,
  lede,
}: {
  kicker: string
  title: string
  lede?: string
}) {
  return (
    <div className="hd">
      <div className="kicker">{kicker}</div>
      <h1 className="big">{title}</h1>
      {lede ? <p className="lede">{lede}</p> : null}
    </div>
  )
}
