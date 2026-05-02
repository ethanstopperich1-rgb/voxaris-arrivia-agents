# Voxaris VBA — Build Plan

The full plan lives in the original briefing. This file tracks day-by-day exit
criteria as the build progresses. Update at the end of each day.

## Phase 1 — Voice agent backbone (Days 1–3)

- [x] **Day 1 PM, Prompt A** — Hello-World worker scaffolded
      ([apps/agent/voxaris_agent/worker.py](../apps/agent/voxaris_agent/worker.py))
- [ ] Day 1 AM — Twilio prod + payments sub-accounts created, Business PCP filed,
      DID purchased (Florida area code), LiveKit project provisioned, xAI key
      smoke-tested via wscat
- [ ] Day 2 AM, Prompt B — qualification state machine
      (`voxaris_agent/state.py`, 8 unit tests)
- [ ] Day 2 PM — μ-law tuning, server VAD, noise cancellation, <1s TTFA verified
- [ ] Day 3 AM, Prompt C — 8 stubbed tools (`voxaris_agent/tools.py`)
- [ ] Day 3 PM — end-to-end happy path with mock data, recorded via Egress

## Phase 2 — Web layer & data (Days 4–6)

- [ ] Day 4 AM, Prompt A — Supabase schema + RLS + seed
- [ ] Day 4 PM, Prompt B — `/consent` Next.js page
- [ ] Day 5 AM, Prompt C — `/api/dial` + LiveKit webhook
- [ ] Day 5 PM — QR card printed, full QR→call flow
- [ ] Day 6 AM — Twilio TwiML pre-roll (`/api/twilio/voice-pre`)
- [ ] Day 6 PM — 5 successful E2E runs, LiveKit upgraded to Ship plan

## Phase 3 — Qualification, booking, Stripe, persistence (Days 7–9)

- [ ] Day 7 AM, Prompt A — real `record_answer`, transcript persistence
- [ ] Day 7 PM, Prompt B — calendar lookup + booking with race-safe RPC
- [ ] Day 8 AM — payments sub-account PCI Mode ON, Stripe Pay Connector
- [ ] Day 8 PM, Prompt C — `<Pay>` hand-off + SIP rejoin loop
- [ ] Day 9 AM, Prompt D — voicemail detection + `transfer_to_human` (SIP REFER)
- [ ] Day 9 PM — 20 mock-call defect run

## Phase 4 — Demo polish (Days 10–11)

- [ ] Day 10 AM, Prompt A — live dashboard with Realtime channels
- [ ] Day 10 PM — voice tuning (default `eve`; only swap to custom voice if
      Stacey supplies a 120s clip and A/B is clean)
- [ ] Day 11 AM — pre-recorded backup video (1080p OBS)
- [ ] Day 11 PM — risk drills R1–R4 from the risk register

## Phase 5 — Demo (Day 12)

- [ ] Dress rehearsal at venue
- [ ] Preflight script all-green (`apps/agent/scripts/preflight.py`)
- [ ] 90s pitch + 4–5 min live call rehearsed
