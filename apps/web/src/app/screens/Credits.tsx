/**
 * Credits.
 *
 * The brief's rule is that any button which spends credits says what it will spend. This is
 * the other half: what things cost, stated plainly, away from the moment of spending.
 */
import type { LedgerEntryOut } from '@lumina/client'

import { useHistory } from '../../api/queries'
import { MONTHLY_CREDITS, PACKS } from '../data'
import { Head, View } from '../Shell'

const PRICES: [string, string, boolean][] = [
  ['See a draft of the whole video', '4', true],
  ['Finish one scene properly', '15', false],
  ['Save it for one platform', '2', false],
  ['Try a scene again', 'Free', true],
]

/**
 * What one ledger entry means, in words.
 *
 * The ledger is double-entry, so a refund and a commit both discharge a reserve and both
 * appear as their own row. Collapsing them into a net figure would hide the difference
 * between "this cost you 15" and "this failed and you got it back", which is exactly what
 * someone checking their balance after a bad run needs to see.
 */
function describe(entry: LedgerEntryOut): { what: string; delta: number } {
  const reason = entry.reason ?? ''
  switch (entry.kind) {
    case 'grant':
      return { what: reason === 'welcome' ? 'Welcome credits' : 'Credits added', delta: entry.amount }
    case 'reserve':
      return { what: 'Set aside for work', delta: -entry.amount }
    case 'refund':
      return {
        what: reason === 'policy_rejection' ? 'Refunded — request refused' : 'Refunded',
        delta: entry.amount,
      }
    default:
      return { what: 'Spent', delta: 0 }
  }
}

/** Relative, because "3 days ago" reads faster than a date and needs no locale. */
function when(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime()
  const mins = Math.round(ms / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.round(hours / 24)}d ago`
}

export function Credits({ balance, onBuy }: { balance: number; onBuy: (n: number) => void }) {
  const history = useHistory()

  return (
    <View mid>
      <Head
        kicker="Credits"
        title={String(balance)}
        lede={`${MONTHLY_CREDITS} more arrive in 11 days. No subscription — credits never expire.`}
      />

      <div style={{ margin: '-8px 0 26px' }}>
        <div className="meter">
          <i style={{ width: `${Math.min(100, (balance / MONTHLY_CREDITS) * 100)}%` }} />
        </div>
      </div>

      <div className="stack">
        <section>
          <div className="label">Top up</div>
          {/* Disabled, and saying so. Payments are the Platform plane's job and Stripe is not
              wired; a tile that silently does nothing is worse than one that is visibly not
              ready, because the user presses it twice and then doubts the balance. */}
          <p className="hint" style={{ margin: '0 0 10px' }}>
            Card payments are not connected yet.
          </p>
          <div className="tiles">
            {PACKS.map((p) => (
              <button
                key={p.credits}
                className="tile"
                disabled
                title="Card payments are not connected yet"
                onClick={() => onBuy(p.credits)}
                style={
                  p.best
                    ? {
                        borderColor: 'rgba(110,231,168,.4)',
                        background:
                          'linear-gradient(150deg, rgba(110,231,168,.08), var(--panel) 70%)',
                      }
                    : undefined
                }
              >
                <span style={{ font: '600 26px/1 var(--display)' }}>
                  {p.credits.toLocaleString()}
                </span>
                <em style={{ marginTop: 8 }}>
                  {p.price}
                  {p.best ? ' · best value' : ''}
                </em>
              </button>
            ))}
          </div>
        </section>

        <section>
          <div className="label">What things cost</div>
          <div className="card">
            {PRICES.map(([what, cost, good]) => (
              <div className="rowline" key={what}>
                <span>{what}</span>
                <span className={`tag ${good ? 'good' : ''}`}>
                  {cost === 'Free' ? 'Free' : `${cost} CR`}
                </span>
              </div>
            ))}
          </div>
        </section>

        <section>
          <div className="label">Recent</div>
          <div className="card">
            {(history.data ?? []).slice(0, 12).map((entry) => {
              const { what, delta } = describe(entry)
              return (
                <div className="rowline" key={entry.id}>
                  <span>
                    {what}
                    <span className="muted" style={{ marginInlineStart: 10, fontSize: 13 }}>
                      {when(entry.created_at)}
                    </span>
                  </span>
                  <span
                    style={{
                      font: '600 14px/1 var(--mono)',
                      color: delta > 0 ? 'var(--good)' : 'var(--ink-2)',
                    }}
                  >
                    {delta > 0 ? `+${delta}` : delta}
                  </span>
                </div>
              )
            })}
            {!history.isLoading && !(history.data ?? []).length ? (
              <div className="rowline">
                <span className="muted">Nothing spent yet.</span>
              </div>
            ) : null}
          </div>
        </section>
      </div>
    </View>
  )
}
