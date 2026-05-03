# LiveKit › Accepting calls › Dispatch rule

> Source: https://docs.livekit.io/telephony/accepting-calls/dispatch-rule.md
> Snapshot: 2026-05-03 — POST-MVP REFERENCE.

Dispatch rules are for **inbound** routing. Our outbound flow doesn't use
them — outbound dispatches the agent explicitly via
`AgentDispatchClient.create_dispatch`.

## When we'll need this

Once we accept inbound calls (someone dials our 407 number), the
recommended setup is an **individual** dispatch rule that auto-creates a
room per caller and dispatches the `vba-qualifier` agent into it:

```json
{
  "dispatch_rule": {
    "rule": {
      "dispatchRuleIndividual": {
        "roomPrefix": "call-"
      }
    },
    "name": "VBA inbound",
    "roomConfig": {
      "agents": [
        { "agentName": "vba-qualifier",
          "metadata": "{\"direction\": \"inbound\"}" }
      ]
    }
  }
}
```

## Rule types

- **`dispatchRuleIndividual`** — one room per caller, named
  `<roomPrefix><phone-number>-<random>`.
- **`dispatchRuleDirect`** — all callers into one shared room with
  optional `pin`.
- **`dispatchRuleCallee`** — room name based on the *called* number, with
  `randomize` to make per-call rooms.

## Agent dispatch via roomConfig

The `roomConfig.agents` array specifies which named agents auto-dispatch
when the room is created. `metadata` is delivered as `ctx.job.metadata`.

## Trunks scope

Without `trunkIds`, a dispatch rule matches every inbound trunk. Pin to a
specific trunk:

```shell
lk sip dispatch create dispatch-rule.json --trunks "<trunk-id>"
```

## Python create

```python
from livekit import api

rule = api.SIPDispatchRule(
    dispatch_rule_individual=api.SIPDispatchRuleIndividual(
        room_prefix="call-",
    )
)
request = api.CreateSIPDispatchRuleRequest(
    dispatch_rule=api.SIPDispatchRuleInfo(
        rule=rule,
        name="VBA inbound",
        room_config=api.RoomConfiguration(
            agents=[api.RoomAgentDispatch(
                agent_name="vba-qualifier",
                metadata='{"direction": "inbound"}',
            )]
        ),
    )
)
dispatch = await lkapi.sip.create_sip_dispatch_rule(request)
```
