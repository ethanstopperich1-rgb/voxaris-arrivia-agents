# LiveKit › Accepting calls › Twilio Voice integration

> Source: https://docs.livekit.io/telephony/accepting-calls/inbound-twilio.md
> Snapshot: 2026-05-03 — POST-MVP REFERENCE.

For the future inbound flow only. Outbound (LiveKit → Twilio Elastic SIP
Trunking → PSTN) does not need this.

## Why TwiML and not direct SIP?

Twilio Elastic SIP Trunking doesn't support username/password auth for
*inbound*. The workaround is Twilio Programmable Voice + TwiML returning a
`<Dial><Sip>` to LiveKit's SIP host.

> **Limitation**: TwiML approach does **not support SIP REFER** (cold
> transfers) or outbound calls. To use REFER, switch to Elastic SIP
> Trunking — which our outbound trunk already is.

## Setup

1. Buy a Twilio number (we have +14072890294).
2. Create a TwiML Bin pointing at LiveKit's SIP host:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Dial>
    <Sip username="<sip_trunk_username>" password="<sip_trunk_password>">
      sip:<your_phone_number>@%{sipHost}%;transport=tcp
    </Sip>
  </Dial>
</Response>
```

3. Configure the Twilio number's "A call comes in" → TwiML Bin.
4. Create a LiveKit inbound trunk with matching username/password:

```json
{
  "trunk": {
    "name": "My inbound trunk",
    "numbers": ["+14072890294"],
    "authUsername": "<sip_trunk_username>",
    "authPassword": "<sip_trunk_password>"
  }
}
```

```shell
lk sip inbound create inbound-trunk.json
```

5. Create a dispatch rule (see [dispatch-rule.md](dispatch-rule.md)).

## Multi-number routing

Use a separate inbound trunk + dispatch rule per phone number, with
different `roomConfig.agents.agentName` to route different numbers to
different agents.
