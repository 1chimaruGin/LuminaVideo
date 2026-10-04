# Turning on Continue with Google / GitHub / Apple

The buttons are always shown and **enabled only when this server has credentials for that
provider**. A greyed one reading "NOT SET UP" is not broken — nothing has been registered for
it yet. `GET /auth/providers` is what the screen reads to decide.

Credentials belong to you and cannot be shipped in a repository, so all three are off until
you do this.

---

## Before you start: which address will you use?

Every provider checks the redirect URI **character for character**. Decide now, because you
have to type the same string in two places.

| You open the app at | Set `PUBLIC_BASE_URL` to |
|---|---|
| `http://localhost:5173` (browser on this machine) | `http://localhost:8000` |
| `http://192.168.223.205:5173` (from Windows, or a phone) | `http://192.168.223.205:8000` |

Google and GitHub both accept `http://localhost`. **Neither accepts a bare IP address**, and
Apple requires HTTPS and a real domain — so for anything other than localhost you need a
tunnel (`cloudflared tunnel --url http://localhost:8000` or `ngrok http 8000`) and you use
that HTTPS URL as `PUBLIC_BASE_URL`.

---

## 1. Google  (~5 minutes, free)

1. Go to <https://console.cloud.google.com/apis/credentials>.
2. Create a project if you have none.
3. **OAuth consent screen** → External → fill in app name and your email → Save.
   While it is in "Testing", add your own address under **Test users**, or sign-in is refused.
4. **Credentials → Create credentials → OAuth client ID → Web application**.
5. Under **Authorised redirect URIs** add exactly:
   ```
   http://localhost:8000/auth/oauth/google/callback
   ```
6. Copy the client ID and client secret into `.env`:
   ```bash
   GOOGLE_CLIENT_ID=1234567890-abc.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=GOCSPX-...
   PUBLIC_BASE_URL=http://localhost:8000
   WEB_BASE_URL=http://localhost:5173
   ```

## 2. GitHub  (~2 minutes, free — the easiest to test with)

1. Go to <https://github.com/settings/developers> → **New OAuth App**.
2. Homepage URL: `http://localhost:5173`
3. Authorization callback URL:
   ```
   http://localhost:8000/auth/oauth/github/callback
   ```
4. Register, then **Generate a new client secret**.
5. Into `.env`:
   ```bash
   GITHUB_CLIENT_ID=Iv1.abc123
   GITHUB_CLIENT_SECRET=...
   ```

## 3. Apple  (hardest — needs a paid account)

Requires the Apple Developer Program (US$99/year), an App ID with Sign In with Apple enabled,
a Services ID, and a `.p8` key. Apple's client secret is **not a password**: it is a
short-lived ES256 JWT you generate from that key and rotate at least every six months.

```bash
APPLE_CLIENT_ID=com.yourcompany.lumina   # the Services ID, not the App ID
APPLE_CLIENT_SECRET=eyJhbGciOiJFUzI1NiIs...   # pre-generated JWT
```

Apple also refuses plain HTTP, so localhost will not work — you need the tunnel.

---

## 4. Restart and check

`.env` is read once at startup and is **not** watched by the reloader.

```bash
make api          # restart it
curl -s localhost:8000/auth/providers
```

```json
[{"id":"google","name":"Google","enabled":true},
 {"id":"github","name":"GitHub","enabled":true},
 {"id":"apple","name":"Apple","enabled":false}]
```

Reload the sign-in page: the enabled ones are now bright and clickable.

---

## When it does not work

| What you see | Cause |
|---|---|
| Button still greyed, "NOT SET UP" | `.env` not loaded — did you restart the API? Check `/auth/providers`. |
| `redirect_uri_mismatch` at Google | The URI at the provider differs from `{PUBLIC_BASE_URL}/auth/oauth/google/callback`. A trailing slash counts. |
| `invalid_client` | Wrong client id or secret, or the secret was regenerated at the provider. |
| Back at sign-in with `#error=email_taken` | That address already has a password account here. Sign in with the password — see the linking rule below. |
| Back with `#error=no_email` | The provider shared no address. On GitHub, check the app requests `user:email`. |
| Google says "access blocked" | Consent screen is in Testing and your address is not a test user. |

---

## The linking rule

A provider sign-in adopts an existing account **only when the provider says the address is
verified**. Otherwise anyone could register your address at a provider that does not check it,
press the button, and inherit your account, projects and credits.

When the address is already taken and the provider will not vouch for it, the sign-in is
refused (`email_taken`) rather than adopting or duplicating — the screen tells the person to
sign in with their password instead.

Signing in with Google and later with GitHub on the **same verified address** lands in the
same account; identities are keyed on the provider's own subject, so changing your email at
the provider does not turn you into a stranger.
