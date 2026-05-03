# LiveKit › Accepting calls › Inbound trunk

> Source: https://docs.livekit.io/telephony/accepting-calls/inbound-trunk.md
> Snapshot: 2026-05-03 — POST-MVP REFERENCE.

Inbound trunks are not used by the current outbound-only build. Saved here
for the future "someone calls our 407 number and reaches Deedy" flow.

## Trunk JSON

```json
{
  "trunk": {
    "name": "My trunk",
    "numbers": ["+15105550100"],
    "krispEnabled": true
  }
}
```

```shell
lk sip inbound create inbound-trunk.json
```

## Twilio caveat

> **Twilio Elastic SIP Trunking does NOT support username/password auth on
> inbound.** For inbound from Twilio, use TwiML (see
> [inbound-twilio.md](inbound-twilio.md)). Other providers (Telnyx, Plivo,
> Exotel, Wavix) do support username/password.

## Restrictions

- `numbers: []` (empty) accepts any number — requires either auth or
  `allowed_addresses`.
- `allowedNumbers: ["+13105550100", ...]` restricts which caller numbers
  the trunk accepts.

## Python create

```python
from livekit import api

trunk = api.SIPInboundTrunkInfo(
    name="My trunk",
    numbers=["+15105550100"],
    krisp_enabled=True,
)
await lkapi.sip.create_sip_inbound_trunk(
    api.CreateSIPInboundTrunkRequest(trunk=trunk)
)
```
