# LiveKit › Making calls › Outbound calls

> Source: https://docs.livekit.io/telephony/making-calls/outbound-calls.md
> Snapshot: 2026-05-03

## Recommended pattern: agent-initiated outbound

Orchestrator only does AgentDispatch. The agent's entrypoint reads
`phone_number` from `ctx.job.metadata`, places the call itself, waits
for pickup, then starts the session.

```python
from livekit import agents, api
import json

@server.rtc_session(agent_name="my-telephony-agent")
async def my_agent(ctx: agents.JobContext):
    dial_info = json.loads(ctx.job.metadata)
    phone_number = dial_info.get("phone_number")
    sip_participant_identity = phone_number

    if phone_number is not None:
        try:
            await ctx.api.sip.create_sip_participant(api.CreateSIPParticipantRequest(
                room_name=ctx.room.name,
                sip_trunk_id='ST_xxxx',
                sip_call_to=phone_number,
                participant_identity=sip_participant_identity,
                wait_until_answered=True,
            ))
        except api.TwirpError as e:
            print(f"error creating SIP participant: {e.message}, "
                  f"SIP status: {e.metadata.get('sip_status_code')} "
                  f"{e.metadata.get('sip_status')}")
            ctx.shutdown()
            return

    # Wait for the SIP participant to fully join the room before starting the session
    participant = await ctx.wait_for_participant(identity=sip_participant_identity)

    # Create and start your AgentSession ... session.start(participant=participant ...)

    # On outbound, let the callee speak first (or, in our TCPA case,
    # speak the AI disclosure first since the user expects the call).
    if phone_number is None:
        await session.generate_reply(
            instructions="Greet the user and offer your assistance."
        )
```

> **Wait for the callee to answer**: call `session.start()` *after* the
> callee picks up. If the session starts while ringing, the greeting plays
> before the callee joins; they hear the tail end or silence.

## Dispatching the agent (the orchestrator's only job)

```shell
lk dispatch create \
    --new-room \
    --agent-name my-telephony-agent \
    --metadata '{"phone_number": "+15105550123"}'
```

```python
await lkapi.agent_dispatch.create_dispatch(
    api.CreateAgentDispatchRequest(
        agent_name="my-telephony-agent",
        room="new-room",
        metadata='{"phone_number": "+15105550123"}'
    )
)
```

## Call outcomes

| Outcome | SIP codes | Behavior | Indicators |
|---|---|---|---|
| Call answered | 200 OK | `wait_until_answered` returns | `sip.callStatus = active` |
| Call rejected | 486, 603 | `wait_until_answered` raises TwirpError | `USER_REJECTED` |
| No answer / timeout | 408, 480 | TwirpError | `USER_UNAVAILABLE` |
| SIP protocol failure | 5xx | TwirpError | `SIP_TRUNK_FAILURE` |
| Voicemail | 200 OK | call answered | `sip.callStatus = active` |

> **Voicemail is not a failure.** It answers at the SIP layer with 200 OK,
> so `wait_until_answered` returns successfully. Detect via the LLM with
> a `detected_answering_machine` `@function_tool` instead of error
> handling.

## Mid-call disconnect

```python
@ctx.room.on("participant_disconnected")
def on_participant_disconnected(participant: rtc.RemoteParticipant):
    if participant.identity != sip_participant_identity:
        return
    reason = participant.disconnect_reason
    # rtc.DisconnectReason.USER_REJECTED / USER_UNAVAILABLE / SIP_TRUNK_FAILURE / ...
```

## Voicemail detection (Python)

```python
@function_tool
async def detected_answering_machine(self):
    """Call this tool if you have detected a voicemail system, AFTER hearing the voicemail greeting"""
    await self.session.generate_reply(
        instructions="Leave a voicemail message letting the user know you'll call back later."
    )
    await asyncio.sleep(0.5)
    await hangup_call()
```

## Hangup (Python)

Use the prebuilt `EndCallTool` from `livekit.agents.prebuilt.tools`, or:

```python
async def hangup_call():
    ctx = get_job_context()
    if ctx is None:
        return
    await ctx.api.room.delete_room(
        api.DeleteRoomRequest(room=ctx.room.name)
    )
```

## Other CreateSIPParticipantRequest fields

- `display_name` — sets caller-ID Name (CNAM); provider must support it.
  Empty string triggers a CNAM lookup on most providers.
- `dtmf` — fixed extension codes to send on answer. `w` = 0.5s pause.
- `play_dialtone` — ringback to the agent side while dialing.
- `hide_phone_number` — masks the phone number from other participants.
- `krisp_enabled` — Krisp noise cancellation on the SIP audio.
- `wait_until_answered` — block the API call until SIP 200 OK.
