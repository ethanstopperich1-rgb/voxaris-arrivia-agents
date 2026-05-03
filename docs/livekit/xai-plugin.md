# LiveKit › Partner spotlight › xAI › Grok Voice Agent API plugin

> Source: https://docs.livekit.io/agents/models/realtime/plugins/xai.md
> Snapshot: 2026-05-03

## Install

```shell
uv add "livekit-agents[xai]~=1.5"     # Python
pnpm add "@livekit/agents-plugin-xai@1.x"   # Node
```

## Auth

`XAI_API_KEY` env var. We have ours in `apps/agent/.env`.

## Usage

```python
from livekit.agents import AgentSession
from livekit.plugins import xai

session = AgentSession(
    llm=xai.realtime.RealtimeModel(voice="Ara"),
)
```

## Plugin defaults vs xAI API defaults

The plugin overrides xAI's server defaults to be more aggressive:

| Param | xAI API default | Plugin default |
|---|---|---|
| `threshold` | 0.85 | 0.5 |
| `prefix_padding_ms` | 333 | 300 |
| `silence_duration_ms` | (unset) | 200 |

Our `worker.py` overrides `silence_duration_ms=600` so older / slower
callers can pause mid-answer without being cut off.

## Provider tools (Python only)

```python
from livekit.plugins import xai

tools=[
    xai.realtime.XSearch(),
    xai.realtime.WebSearch(),
    xai.realtime.FileSearch(vector_store_ids=["..."]),
]
```

We do **not** use these — our `lookup_objection` is a local Python
function tool that's faster and offline.

## Voice literals

Plugin accepts `'Ara' | 'Eve' | 'Leo' | 'Rex' | 'Sal'` (PascalCase) or a
custom `voice_id`. The xAI public docs show lowercase but the plugin
enforces Pascal. We use `Eve`.
