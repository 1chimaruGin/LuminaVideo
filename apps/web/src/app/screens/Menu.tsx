/**
 * The menu sheet.
 *
 * Everything outside the funnel lives here. It is a sheet rather than a rail so it cannot
 * become the flat tool menu the brief rules out — you open it deliberately, do one thing,
 * and it goes away.
 */
import { useEffect } from 'react'

import { useProjects } from '../../api/queries'
import type { Studio } from '../live'
import { IconClose, IconCoin, IconGrid, IconMic, IconUser, IconWand } from '../icons'

/**
 * The rows, and what each one shows on the right.
 *
 * `after` is a function of the real state rather than a string, because it used to be a set
 * of literals — "4" videos, "Ready", "Sam Rivera" — that were true of nobody. A menu that
 * confidently reports someone else's name is worse than one that reports nothing.
 */
const ITEMS = [
  { id: 'projects', label: 'Your videos', Icon: IconGrid },
  { id: 'identity', label: 'Identity kit', Icon: IconWand },
  { id: 'voice', label: 'Voice setup', Icon: IconMic },
  { id: 'credits', label: 'Credits', Icon: IconCoin },
  { id: 'profile', label: 'Account', Icon: IconUser },
] as const

export function Menu({ s, onClose }: { s: Studio; onClose: () => void }) {
  const projects = useProjects()
  const who = s.channel?.name?.trim() || 'Your channel'
  const made = projects.data?.length ?? 0
  const after: Record<string, string> = {
    projects: made ? String(made) : '',
    voice: s.channel?.voice_profile_asset_id ? 'Ready' : 'Not set up',
    credits: String(s.balance),
    profile: who,
  }

  // Escape closes: a sheet that can only be dismissed by pointer is a trap on desktop.
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose])

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="sheet" role="dialog" aria-label="Menu">
        <div className="grab" />
        <div className="spread" style={{ marginBottom: 20 }}>
          <b style={{ font: '600 20px/1 var(--display)' }}>Lumina</b>
          <button className="iconbtn" onClick={onClose} aria-label="Close">
            <IconClose size={18} />
          </button>
        </div>

        <div className="stack" style={{ gap: 2 }}>
          {ITEMS.map(({ id, label, Icon }) => (
            <button
              key={id}
              className="menu-item"
              onClick={() => {
                s.go(id)
                onClose()
              }}
            >
              {/* An icon tile rather than a glyph beside a word: at a glance the shape is
                  what distinguishes the rows, not the label. */}
              <span className="tile-icon">
                <Icon size={19} />
              </span>
              {label}
              <span className="after">{after[id] ?? ''}</span>
            </button>
          ))}
        </div>

        <button
          className="card pad"
          style={{ marginTop: 22, width: '100%', textAlign: 'left' }}
          onClick={() => {
            s.go('profile')
            onClose()
          }}
        >
          <div className="spread" style={{ alignItems: 'baseline' }}>
            <span className="label" style={{ margin: 0 }}>
              {who}
            </span>
            <span className="tag">Free plan</span>
          </div>
          <p className="muted" style={{ margin: '10px 0 0', font: '400 14px/1.5 var(--ui)' }}>
            {made === 1 ? '1 video' : `${made} videos`} · {s.balance} credits
          </p>
        </button>
      </aside>
    </>
  )
}
