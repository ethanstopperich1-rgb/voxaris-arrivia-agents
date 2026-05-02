# Compliance — verified facts

Sources walked: 2026-05-02.

## xAI Voice (`docs.x.ai/developers/model-capabilities/audio/voice`)

Stated by xAI in their public docs:

- **SOC 2 Type II** audited controls.
- **HIPAA Eligible — BAA available.**
  *Correction to original build plan, which said "HIPAA BAA not publicly
  listed for Voice Agent API." It is. Useful leverage for the Arrivia pilot
  agreement and any downstream resort partner that wants the BAA in writing.*
- **GDPR compliant.**
- **Zero retention:** "All audio data is processed in real time and never
  stored or used for training."

## xAI Voice Agent (`/audio/voice-agent`)

Verified API surface:

- Endpoint: `wss://api.x.ai/v1/realtime?model=grok-voice-think-fast-1.0`
- Auth: `Authorization: Bearer ...` (server) or
  `xai-client-secret.{ephemeral}` (client subprotocol)
- Models: `grok-voice-think-fast-1.0` (current),
  `grok-voice-fast-1.0` (legacy, deprecated)
- Voices: `eve`, `ara`, `rex`, `sal`, `leo` + custom via `/v1/custom-voices`
- Audio formats:
  - `audio/pcm` Linear16 — 8/16/22.05/24/32/44.1/48 kHz (24 kHz default)
  - `audio/pcmu` G.711 μ-law — 8 kHz only
  - `audio/pcma` G.711 A-law — 8 kHz only
- Turn detection: `server_vad` with `threshold` (0.85 default),
  `silence_duration_ms`, `prefix_padding_ms` (333 default); or `null` for
  manual text turns.
- Tool types: `function`, `file_search`, `web_search`, `x_search`, `mcp`.
- Custom voices: `POST /v1/custom-voices` with reference clip ≤120 s,
  returns 8-char alphanumeric `voice_id`.

### OpenAI Realtime compatibility — partial, not full

Risk for the R3 fallback ("swap base URL → OpenAI Realtime if xAI is down"):

- xAI uses `response.text.delta`; OpenAI uses `response.output_text.delta`.
- Unsupported xAI client events: `conversation.item.retrieve`,
  `conversation.item.truncate`.
- Unsupported xAI server events: `conversation.item.done`, input
  transcription variants, rate-limit notifications.

A clean swap to OpenAI Realtime requires a small adapter, not a one-env-var
change. Day 11 drill should explicitly test this.

## TCPA / FCC (Feb 8 2024 Declaratory Ruling)

AI-generated voice is "artificial voice" under TCPA. Marketing calls
(timeshare preview tour qualifies) require **prior express written
consent (PEWC)** that:

1. Authorizes calls "using an artificial or prerecorded voice, including
   AI-generated voice."
2. Specifies the phone number being authorized.
3. Discloses consent is not a condition of purchase.
4. Names the calling entity.

Implementation: the `/consent` Next.js page (Phase 2 Prompt B) is the
single source of truth.

## Florida (ARDA jurisdiction)

- Two-party consent recording state.
- FTSA hours: 8 AM – 8 PM local. Demo calls only inside this window.
- Recording disclosure must come at call open AND must itself be recorded
  (handled by Twilio `<Say>` pre-roll → `/api/twilio/voice-pre`).

## CA SB 1001 (BOT Act)

Statute is written for "online" interactions; phone-call applicability is
debated. The FCC TCPA artificial-voice rule covers our case directly, so
the conservative approach is to disclose AI status in the **opening 10
seconds of every call** regardless of state.
