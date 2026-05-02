# Voxaris VBA — Virtual Booking Agent

LiveKit Agents (Python) + xAI Grok Voice + Twilio Elastic SIP + Stripe `<Pay>` + Vercel/Next.js + Supabase.

Target: live demo at ARDA in 12 days.

## Layout

```
apps/
  agent/        Python LiveKit worker (livekit-agents + livekit-plugins-xai)
  web/          Next.js 15 (consent page, dial trigger, dashboard, webhooks)
packages/
  shared/       Generated Supabase types + shared TS types
supabase/
  migrations/   SQL migrations
docs/           Build plan, runbook, compliance scripts
```

## Why split hosts

- **Vercel cannot host the agent worker** — Vercel Functions don't support persistent WebSocket servers. The LiveKit worker holds long-lived sockets to the LiveKit SFU and to xAI Realtime. It runs on Fly.io or Railway.
- **Vercel is fine for**: consent page, `/api/dial`, Twilio voice webhook, Stripe webhook, dashboard SSR, LiveKit webhook receiver.

## Quickstart

```bash
# 1. Install Python deps for the agent
cd apps/agent
python -m venv .venv && source .venv/bin/activate
pip install -e .

# 2. Scaffold the web app (run once, then commit)
cd ../../apps
pnpm create next-app@latest web --typescript --app --tailwind --eslint --src-dir=false --import-alias="@/*"

# 3. Local dev (agent)
cd apps/agent
cp .env.example .env  # fill in LiveKit + xAI keys
python -m voxaris_agent.worker dev
```

## Env vars (master list)

See [docs/env.md](docs/env.md).

## Build phases

- **Phase 1 (Days 1–3)**: voice agent backbone — `apps/agent` greets a SIP caller via Grok.
- **Phase 2 (Days 4–6)**: web layer — QR → consent page → `/api/dial` triggers a real call.
- **Phase 3 (Days 7–9)**: qualification persistence, booking, Stripe `<Pay>`, SMS.
- **Phase 4 (Days 10–11)**: dashboard, voice tuning, drills.
- **Phase 5 (Day 12)**: dress rehearsal + ARDA stage.

Full plan: [docs/build-plan.md](docs/build-plan.md).

## Hard rules

1. No outbound to non-consented numbers. Ever.
2. AI disclosure in the first 10 seconds of every call (Twilio `<Say>` pre-roll + AI restated).
3. Recording consent captured on the QR page (FL is two-party).
4. PCI Mode lives on a **separate Twilio sub-account**. Do not enable it on the prod sub-account.
5. Stripe demo uses Test Mode + `4242 4242 4242 4242`. Real captures only post-conference.
