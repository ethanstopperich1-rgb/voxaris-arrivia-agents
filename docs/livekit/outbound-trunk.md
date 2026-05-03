# LiveKit › Making calls › Outbound trunk

> Source: https://docs.livekit.io/telephony/making-calls/outbound-trunk.md
> Snapshot: 2026-05-03

## Outbound trunk JSON (Twilio)

```json
{
  "trunk": {
    "name": "My outbound trunk",
    "address": "<my-trunk>.pstn.twilio.com",
    "numbers": ["+15105550100"],
    "authUsername": "<username>",
    "authPassword": "<password>"
  }
}
```

```shell
lk sip outbound create outbound-trunk.json
```

## Address format per provider

- **Twilio**: `<trunk-name>.pstn.twilio.com` (use the Trunk's Termination URI domain)
- **Telnyx**: `sip.telnyx.com` (regional signaling addresses also valid)

## Per-call number override

Setting `numbers: ["*"]` allows any caller-ID number; supply
`sip_number` on `CreateSIPParticipant` per call.

## Region pinning (outbound)

Set `destination_country` on the trunk to originate calls from the
LiveKit region nearest the destination. Reduces PSTN latency for US
calls when set to `"US"`.

## Auth

LiveKit Cloud nodes do **not** have a static IP range. Prefer
username/password auth on the trunk. If your provider also requires an
IP allowlist, set it to `0.0.0.0/0` or `0.0.0.0/1` + `128.0.0.0/1`.

## Python create

```python
from livekit import api
from livekit.protocol.sip import CreateSIPOutboundTrunkRequest, SIPOutboundTrunkInfo

trunk = SIPOutboundTrunkInfo(
    name = "My trunk",
    address = "voxaris-vba-livekit.pstn.twilio.com",
    numbers = ["+14072890294"],
    auth_username = "<username>",
    auth_password = "<password>",
)
await lkapi.sip.create_sip_outbound_trunk(
    CreateSIPOutboundTrunkRequest(trunk=trunk)
)
```

## Update fields

```python
from livekit.protocol.models import ListUpdate

await lkapi.sip.update_sip_outbound_trunk_fields(
    trunk_id="<sip-trunk-id>",
    name="My updated outbound trunk",
    address="voxaris-vba-livekit.pstn.twilio.com",
    numbers=ListUpdate(add=["+15105550100"], remove=["+15105550100"]),
)
```

## Replace entirely

```python
from livekit.protocol.sip import SIPOutboundTrunkInfo, SIPTransport

trunk = SIPOutboundTrunkInfo(
    address="voxaris-vba-livekit.pstn.twilio.com",
    numbers=["+14072890294"],
    name="My replaced outbound trunk",
    transport=SIPTransport.SIP_TRANSPORT_AUTO,
    auth_username="<username>",
    auth_password="<password>",
)
await lkapi.sip.update_sip_outbound_trunk(trunk_id, trunk)
```
