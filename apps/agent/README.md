# apps/agent — Voxaris VBA worker

LiveKit agent worker (Python). Holds a long-lived WebSocket to LiveKit Cloud
and to xAI Realtime. **Cannot run on Vercel** — must run on Fly.io / Railway /
Render / Cloud Run.

## Local dev

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # fill in keys

# Register with LiveKit and wait for dispatch
python -m voxaris_agent.worker dev
```

In another shell, dispatch a job and create a SIP participant:

```bash
lk dispatch create --agent-name vba-qualifier --room test-room
lk sip participant create \
  --room test-room \
  --trunk $LIVEKIT_SIP_OUTBOUND_TRUNK_ID \
  --to "+1YOURPHONE" \
  --identity caller
```

Your phone should ring within ~3s, and the agent should speak the greeting
within ~1.5s of pickup.

## Deploy

```bash
fly launch --no-deploy           # only on first run
fly secrets set \
  LIVEKIT_URL=... \
  LIVEKIT_API_KEY=... \
  LIVEKIT_API_SECRET=... \
  XAI_API_KEY=...
fly deploy
fly logs --app voxaris-vba-agent
```

## Phase status

- [x] Phase 1A — Hello-World greeting (this file)
- [ ] Phase 1B — `voxaris_agent/state.py` qualification state machine
- [ ] Phase 1C — `voxaris_agent/tools.py` 8 function tools (stubbed)
- [ ] Phase 3A — Real Supabase persistence
- [ ] Phase 3B — Real calendar + booking
- [ ] Phase 3C — Twilio `<Pay>` hand-off + SIP rejoin
- [ ] Phase 3D — Voicemail detection + SIP REFER transfer

## Latency targets

- Time-to-first-audio after caller stops talking: **<1s** (xAI publishes sub-1s
  TTFA; LiveKit + SIP adds ~150–250ms).
- If consistently >1.5s, check (in order):
  1. SIP trunk region matches Fly region (`iad`).
  2. xAI `input_audio_format=audio/pcmu` is set (no resampling).
  3. Silero is not double-VAD'ing on top of xAI server VAD.

## Tests

```bash
pytest
```
