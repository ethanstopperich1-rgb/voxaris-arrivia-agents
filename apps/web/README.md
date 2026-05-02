# apps/web — Voxaris VBA control plane

Next.js 15 (App Router). Hosts:

- `/consent` — QR-code landing page (TCPA PEWC + recording + AI disclosure)
- `/calling` — post-consent status polling page
- `/dashboard` — live demo dashboard (transcript + qualification + booking + deposit)
- `/transfer/[contextId]` — specialist screen-pop (post-MVP)
- `POST /api/qr-scan` — log scan, set sealed cookie
- `POST /api/consent` — write consent_events, mint signed token
- `POST /api/dial` — verify token, dispatch LiveKit agent, create SIP participant
- `GET /api/calls/status` — poll call state
- `POST /api/livekit/webhook` — room/participant events
- `POST /api/twilio/voice-pre` — pre-roll TwiML (FCC AI disclosure + press-9 opt-out)
- `POST /api/twilio/pay-action` — Stripe PaymentIntent + rejoin TwiML
- `POST /api/stripe/webhook` — payment confirmation
- `GET /api/health` — liveness for preflight script

## Bootstrap (run once)

```bash
cd apps
pnpm create next-app@latest web \
  --typescript --app --tailwind --eslint \
  --src-dir=false --import-alias="@/*" --turbopack
```

Then add deps:

```bash
cd web
pnpm add @supabase/supabase-js @supabase/ssr \
        livekit-server-sdk \
        twilio stripe \
        zod \
        react-phone-number-input libphonenumber-js
pnpm add -D @types/node
```

## Runtime split

- Most `/api/*` routes use the **Node runtime** (need `livekit-server-sdk`,
  `twilio`, `stripe`).
- `/consent` and `/calling` can use Edge.
- LiveKit webhook receiver MUST use Node (signature verification uses
  `WebhookReceiver` from `livekit-server-sdk`).

## Why not Vercel for the agent

Vercel Functions don't host WebSocket servers
(<https://vercel.com/guides/do-vercel-serverless-functions-support-websocket-connections>).
The LiveKit agent worker lives in `apps/agent` on Fly.io.
