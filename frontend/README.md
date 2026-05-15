# BEN frontend

React + Vite UI for BEN chat and council.

## Environment

Copy `.env.example` to `.env.local`:

| Variable | Required | Description |
|----------|----------|-------------|
| `VITE_CLERK_PUBLISHABLE_KEY` | No | When set, enables Clerk sign-in and sends `Authorization: Bearer` on `/chat` and `/council`. |
| `VITE_BEN_API_BASE` | No | API origin without trailing slash. Unset: production Railway URL in builds; same-origin (Vite proxy) in `npm run dev`. |

Tokens are obtained via Clerk only; the app does not log or store bearer tokens.

## Commands

```bash
npm install
npm run dev    # http://localhost:5173 — proxies /chat and /council to Railway
npm run build
```

Backend auth enforcement remains off until explicitly enabled (`ENFORCE_AUTH=false`).
