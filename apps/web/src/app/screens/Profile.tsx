/**
 * Account.
 *
 * Deliberately quiet. These are settings that must exist and should never compete with the
 * work — so they read as a list, and the only emphasis is on the thing a creator actually
 * checks here, which is what they have made.
 */
import type { Screen } from '../data'
import type { Studio } from '../live'
import { View } from '../Shell'
import { IconChevron } from '../icons'

/**
 * Account rows.
 *
 * `to` names the screen a row opens. A row without one is not built yet and is shown as
 * unavailable rather than as a chevron that does nothing — a settings list where half the
 * rows silently fail teaches people to stop trusting the other half.
 */
const ROWS: { label: string; value: string; to?: Screen }[] = [
  { label: 'Credits', value: '', to: 'credits' },
  { label: 'Identity kit', value: 'Voice, look, captions', to: 'identity' },
  { label: 'Your videos', value: '', to: 'projects' },
  { label: 'Connected accounts', value: 'Not connected' },
  { label: 'Notifications', value: 'When a render is done' },
  { label: 'Privacy and data', value: '' },
  { label: 'Help', value: '' },
]

export function Profile({ s, onSignOut }: { s: Studio; onSignOut: () => void }) {
  return (
    <View mid>
      <div className="profile-head">
        <span className="avatar">SR</span>
        <span>
          <b>Sam Rivera</b>
          <span>@samrivera</span>
        </span>
      </div>

      <div className="stats">
        <div className="stat">
          <b>47</b>
          <span>Published</span>
        </div>
        <div className="stat">
          <b>{s.balance}</b>
          <span>Credits</span>
        </div>
        <div className="stat">
          <b>2</b>
          <span>Series</span>
        </div>
      </div>

      <div className="card">
        {ROWS.map((row) => (
          <button
            key={row.label}
            className="rowline"
            style={{ width: '100%', textAlign: 'left' }}
            disabled={!row.to}
            title={row.to ? undefined : 'Not built yet'}
            onClick={() => row.to && s.go(row.to)}
          >
            <span>{row.label}</span>
            <span className="muted" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              {row.value || (row.to ? '' : 'Soon')}
              {row.to ? <IconChevron size={16} /> : null}
            </span>
          </button>
        ))}
      </div>

      <button
        className="btn block"
        onClick={onSignOut}
        style={{ marginTop: 16, color: '#ff9c8f' }}
      >
        Sign out
      </button>
    </View>
  )
}
