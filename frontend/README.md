# LinguaFlow Frontend

Next.js App Router frontend for authentication and real-time multilingual chat.

## Commands

```bash
npm install
npm run dev
npm run lint
npx tsc --noEmit --incremental false
npm run build
```

The development app is available at `http://localhost:3000`; the chat route is
`/chat`.

## Source layout

```text
src/
  app/                 Route definitions and layouts
  config/              Public runtime configuration
  features/auth/       Authentication UI, API client and session helpers
  features/chat/       Chat UI, REST/WebSocket clients and styles
```
Keep route files thin. Feature-specific code belongs under its feature.

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | Yes for production builds | Absolute backend base URL, e.g. `https://api.example.com` |
| `NEXT_PUBLIC_GOOGLE_OAUTH_CLIENT_ID` | Optional | Google OAuth 2.0 Web Client ID, must match `GOOGLE_OAUTH_CLIENT_ID` in backend |

Google Sign-In uses the Google Identity Services ID-token flow. Add the site's
origin to **Authorized JavaScript origins** in Google Cloud Console. The backend
verifies the token and resolves accounts by the immutable `google_sub` value.
## Docker

The production image is a Next.js standalone server. Build and run it with:

```bash
NEXT_PUBLIC_API_URL=http://localhost:8000 docker compose up --build
```

`NEXT_PUBLIC_API_URL` must point to the API address available from the visitor's
browser, not a Docker-only service hostname. A production build fails fast when
this value is empty, malformed, or does not use HTTP(S).
