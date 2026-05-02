# Environment variables — master list

Three deployment targets, three env stores. Keep them in sync.

| Var | apps/agent (Fly) | apps/web (Vercel) | Notes |
|---|---|---|---|
| `LIVEKIT_URL` | ✅ | ✅ | `wss://*.livekit.cloud` |
| `LIVEKIT_API_KEY` | ✅ | ✅ | |
| `LIVEKIT_API_SECRET` | ✅ | ✅ | |
| `LIVEKIT_SIP_OUTBOUND_TRUNK_ID` | ✅ | ✅ | from `lk sip outbound create` |
| `LIVEKIT_WEBHOOK_KEY` | — | ✅ | LiveKit project settings |
| `XAI_API_KEY` | ✅ | — | Voice scope |
| `SUPABASE_URL` | ✅ | ✅ | |
| `SUPABASE_SERVICE_ROLE_KEY` | ✅ | ✅ (Node routes only) | server-only |
| `NEXT_PUBLIC_SUPABASE_URL` | — | ✅ | dashboard Realtime |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | — | ✅ | dashboard Realtime |
| `TWILIO_ACCOUNT_SID` | — | ✅ | **prod** sub-account, NOT payments |
| `TWILIO_AUTH_TOKEN` | — | ✅ | prod sub-account |
| `TWILIO_VOICE_NUMBER` | — | ✅ | E.164, FL area code |
| `TWILIO_MESSAGING_SERVICE_SID` | — | ✅ | for SMS confirmations |
| `TWILIO_PAY_ACCOUNT_SID` | — | ✅ | **payments** sub-account (PCI Mode ON) |
| `TWILIO_PAY_AUTH_TOKEN` | — | ✅ | payments sub-account |
| `STRIPE_SECRET_KEY` | — | ✅ | Test Mode for the demo |
| `STRIPE_WEBHOOK_SECRET` | — | ✅ | for `/api/stripe/webhook` |
| `CONSENT_SIGNING_SECRET` | — | ✅ | HMAC for consent tokens, 32+ bytes |
| `LIVE_AGENT_NUMBER` | ✅ | ✅ | E.164 fallback for `transfer_to_human` |
| `OPS_PHONE` | — | ✅ | preflight SMS target |
| `LOG_LEVEL` | ✅ | ✅ | `INFO` default |

## Setting them

**Fly (agent):**
```bash
fly secrets set --app voxaris-vba-agent LIVEKIT_URL=... XAI_API_KEY=...
```

**Vercel (web):**
```bash
vercel env add LIVEKIT_URL production
vercel env pull .env.local  # for local dev
```

## Hard rule

`TWILIO_PAY_*` credentials are for the **separate** payments sub-account that
has PCI Mode enabled (one-way switch — confirmed in Twilio docs). All call
logs on that sub-account are redacted. Never put `TWILIO_PAY_AUTH_TOKEN` and
`TWILIO_AUTH_TOKEN` in the same code path.
