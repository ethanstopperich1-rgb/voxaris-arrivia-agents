# LiveKit reference docs (frozen snapshots)

Pinned copies of LiveKit telephony docs as of 2026-05-03. Saved here so the
agent code's behavior can be cross-referenced against a known doc revision —
LiveKit's docs evolve and these are what the current implementation is
written against.

| File | Source |
|---|---|
| [outbound-calls.md](outbound-calls.md) | https://docs.livekit.io/telephony/making-calls/outbound-calls.md |
| [outbound-trunk.md](outbound-trunk.md) | https://docs.livekit.io/telephony/making-calls/outbound-trunk.md |
| [inbound-trunk.md](inbound-trunk.md) | https://docs.livekit.io/telephony/accepting-calls/inbound-trunk.md |
| [inbound-twilio.md](inbound-twilio.md) | https://docs.livekit.io/telephony/accepting-calls/inbound-twilio.md |
| [dispatch-rule.md](dispatch-rule.md) | https://docs.livekit.io/telephony/accepting-calls/dispatch-rule.md |
| [xai-plugin.md](xai-plugin.md) | https://docs.livekit.io/agents/models/realtime/plugins/xai.md |
| [prompting.md](prompting.md) | https://docs.livekit.io/agents/start/prompting.md |

## Key facts the agent code relies on

- **Outbound trunk address** for Twilio: `<trunk-name>.pstn.twilio.com`. Ours:
  `voxaris-vba-livekit.pstn.twilio.com`.
- **Twilio Elastic SIP Trunking does not support** username/password auth on
  *inbound*. For inbound (post-MVP), TwiML Bin → `<Sip>` is required.
- **Outbound** authenticates via username/password set on the Twilio
  Credential List + the `auth_username`/`auth_password` fields on the LiveKit
  outbound trunk.
- **Recommended outbound flow**: AgentDispatch only (from orchestrator) →
  agent's entrypoint creates the SIP participant via
  `ctx.api.sip.create_sip_participant` → waits for participant join →
  starts session → speaks greeting. Implemented in
  [apps/agent/voxaris_agent/worker.py](../../apps/agent/voxaris_agent/worker.py).
- **SIP error metadata**: `TwirpError.metadata['sip_status_code']` carries
  the upstream SIP status (486 busy, 408 timeout, 5xx trunk failure).
  Worker logs these on the `TwirpError` branch.
- **Disconnect reasons**: `participant.disconnect_reason` carries one of
  `USER_REJECTED`, `USER_UNAVAILABLE`, `SIP_TRUNK_FAILURE`, etc.
- **xAI plugin VAD defaults** (threshold=0.5, silence=200ms) are
  intentionally overridden in [worker.py](../../apps/agent/voxaris_agent/worker.py)
  to `silence_duration_ms=600` — gives slower / older callers room to
  pause mid-answer without being cut off.
