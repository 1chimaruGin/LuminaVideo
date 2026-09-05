/**
 * Sign in and sign up.
 *
 * One screen, two modes. The difference between them is a heading and a name field, so
 * splitting them into separate destinations would only cost the user a navigation to
 * discover they were in the wrong one.
 *
 * The mark runs at full size here because this is the first thing anyone sees, and often the
 * only place the product gets to introduce itself.
 */
import { useEffect, useState } from 'react'

import { API_BASE, setToken } from '../../api/client'
import { messageOf } from '../../api/errors'
import { useProviders, useSignIn, useSignUp } from '../../api/queries'
import markUrl from '../../assets/lumina-disc.png'
import { View } from '../Shell'
import { MarkApple, MarkGitHub, MarkGoogle } from '../brand-icons'
import { IconChevron } from '../icons'

export function Auth({ onDone }: { onDone: () => void }) {
  const [mode, setMode] = useState<'in' | 'up'>('in')
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const up = mode === 'up'

  const signUp = useSignUp()
  const signIn = useSignIn()
  const providers = useProviders()
  const busy = signUp.isPending || signIn.isPending

  /**
   * Pick up the result of a provider sign-in.
   *
   * The callback redirects back here with the outcome in the URL fragment — a fragment
   * because it is never sent to a server, so the token stays out of access logs and Referer
   * headers. It is read once and wiped from the address bar immediately, so a copied URL or a
   * screenshot does not carry a live session.
   */
  const [fromProvider, setFromProvider] = useState<string | null>(null)
  useEffect(() => {
    const hash = new URLSearchParams(window.location.hash.slice(1))
    const token = hash.get('token')
    const failed = hash.get('error')
    if (!token && !failed) return

    window.history.replaceState(null, '', window.location.pathname + window.location.search)
    if (token) {
      setToken(token)
      onDone()
    } else if (failed) {
      setFromProvider(failed)
    }
  }, [onDone])

  /**
   * Two operations, not one.
   *
   * They used to be the same call, which meant typing any address into the sign-in box signed
   * you in as its owner. They also fail differently on purpose: signing up with an address
   * that already exists says so, because the person needs to know they should sign in
   * instead — while a bad sign-in never says whether it was the address or the password,
   * because that difference is an oracle for which addresses are registered.
   */
  const submit = () => {
    const address = email.trim()
    if (!address || password.length < MIN_PASSWORD) return

    if (up) {
      signUp.mutate(
        { email: address, password, display_name: name.trim() || undefined },
        { onSuccess: onDone },
      )
    } else {
      signIn.mutate({ email: address, password }, { onSuccess: onDone })
    }
  }

  const problem = messageOf(
    up ? signUp.error : signIn.error,
    'Could not reach Lumina. Check your connection and try again.',
  )
  const ready = email.trim().length > 0 && password.length >= MIN_PASSWORD

  return (
    <View
      center
      mid
      action={
        <button className="btn primary block" onClick={submit} disabled={!ready || busy}>
          {busy ? 'One moment…' : up ? 'Create account' : 'Sign in'}
          <IconChevron size={18} />
        </button>
      }
    >
      <div className="auth">
        <img className="mark-hero" src={markUrl} alt="Lumina" />
        <h1>{up ? 'Make your first short' : 'Welcome back'}</h1>
        <p className="lede">
          {up
            ? 'Describe what you want to post. Lumina plans it, makes it and hands it back.'
            : 'Pick up where you left off.'}
        </p>

        {/* Shown whether or not the server can use them.
            A button that vanishes leaves someone wondering whether they misremembered; one
            that is visibly off, with a reason, tells them — and tells whoever is deploying
            this exactly what is missing. */}
        <div className="oauth">
          {ORDER.map((id) => {
            const Mark = MARKS[id]
            const provider = providers.data?.find((p) => p.id === id)
            const enabled = provider?.enabled ?? false
            return (
              <button
                key={id}
                className="btn block"
                disabled={!enabled || busy}
                title={enabled ? undefined : `${NAMES[id]} sign-in is not set up on this server`}
                onClick={() => {
                  // A full navigation, not fetch: the provider's consent screen has to be
                  // rendered by the browser, and it refuses to be framed or XHR'd.
                  //
                  // `next` is this page's own origin, so the callback returns to wherever the
                  // app was actually opened. Without it the server sends everyone to one
                  // configured address — and `localhost` and `127.0.0.1` are different
                  // origins to a browser, so the token would be stored under one and read
                  // under the other, completing the handshake and still looking signed out.
                  const back = encodeURIComponent(window.location.origin)
                  window.location.href = `${API_BASE}/auth/oauth/${id}/start?next=${back}`
                }}
              >
                <Mark />
                Continue with {NAMES[id]}
                {!enabled && providers.isSuccess ? <em className="off">not set up</em> : null}
              </button>
            )
          })}
        </div>

        <div className="divider">or</div>

        <form
          onSubmit={(e) => {
            e.preventDefault()
            submit()
          }}
        >
          {up ? (
            <label className="field">
              <span>Name</span>
              <input
                type="text"
                name="name"
                autoComplete="name"
                placeholder="Sam Rivera"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
          ) : null}
          <label className="field">
            <span>Email</span>
            <input
              type="email"
              name="email"
              autoComplete="email"
              placeholder="you@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>
          <label className="field">
            <span>Password</span>
            <input
              type="password"
              name="password"
              autoComplete={up ? 'new-password' : 'current-password'}
              placeholder="••••••••"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              minLength={MIN_PASSWORD}
            />
            {/* Only while signing up. Telling someone signing in that their password is too
                short is telling them about the password they just typed, not about ours. */}
            {up && password.length > 0 && password.length < MIN_PASSWORD ? (
              <small className="hint">At least {MIN_PASSWORD} characters.</small>
            ) : null}
          </label>
        </form>

        {fromProvider ? (
          <p className="fineprint" role="alert" data-bad>
            {PROVIDER_ERRORS[fromProvider] ?? 'That sign-in did not complete. Try again.'}
          </p>
        ) : null}

        {problem ? (
          <p className="fineprint" role="alert" data-bad>
            {problem}
          </p>
        ) : null}

        <p className="swap">
          {up ? 'Already have an account? ' : 'New here? '}
          <button
            onClick={() => {
              setMode(up ? 'in' : 'up')
              signUp.reset()
              signIn.reset()
            }}
          >
            {up ? 'Sign in' : 'Create an account'}
          </button>
        </p>

        {up ? (
          <p className="fineprint">
            Free every month. No card needed, and credits never expire.
          </p>
        ) : null}
      </div>
    </View>
  )
}

/** Matches the server. Enforced there too — this only saves a round trip. */
const MIN_PASSWORD = 8

/** The order the buttons appear in. Google first: it is what most creators actually have. */
const ORDER = ['google', 'apple', 'github'] as const

const NAMES: Record<string, string> = { google: 'Google', apple: 'Apple', github: 'GitHub' }

const MARKS: Record<(typeof ORDER)[number], typeof MarkGoogle> = {
  google: MarkGoogle,
  apple: MarkApple,
  github: MarkGitHub,
}

/**
 * What came back in the fragment, in words.
 *
 * `email_taken` earns its own sentence because it is the only one with an obvious next step:
 * the address already has a password account, so the person should use it.
 */
const PROVIDER_ERRORS: Record<string, string> = {
  access_denied: 'Sign-in was cancelled.',
  email_taken:
    'That email already has an account here. Sign in with your password instead.',
  no_email: 'That provider did not share an email address, so there is nothing to sign in as.',
  signin_failed: 'That sign-in did not complete. Try again.',
  incomplete: 'That sign-in did not complete. Try again.',
}
