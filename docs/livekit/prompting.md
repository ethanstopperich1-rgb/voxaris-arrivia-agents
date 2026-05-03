# LiveKit › Get Started › Prompting guide

> Source: https://docs.livekit.io/agents/start/prompting.md
> Snapshot: 2026-05-03

## Recommended prompt structure (Markdown)

1. **Identity** — "You are..." opening, name, role, primary
   responsibilities.
2. **Output rules** — voice-specific formatting (spell out numbers,
   plain text, one question per turn). *May be unnecessary for realtime
   native speech models like Grok* — the plugin handles speech directly.
3. **Tools** — general guidance on tool use; specifics belong on each
   tool's `description`.
4. **Goals** — overarching objective; each handoff/task can override.
5. **Guardrails** — out-of-scope handling, sensitive topics, privacy.
6. **User information** — substituted from `ctx.job.metadata`.

## How our [worker.py](../../apps/agent/voxaris_agent/worker.py) maps to this

| Section in guide | Our prompt |
|---|---|
| Identity | "You are Deedy, the Voxaris Virtual Booking Agent..." |
| Output rules | Mostly omitted (Grok is realtime native speech). "Plain spoken English. No buzzwords." line covers it. |
| Tools | "Tools" section enumerates `record_answer`, `transfer_to_human`, `detect_voicemail`, `lookup_objection` |
| Goals | "You succeed when: ..." closing line |
| Guardrails | TCPA disclosure, never-claim-human, never-quote-prohibited-pricing |
| User information | `=== CURRENT GUEST CONTEXT ===` block with `{resort_name}`, `{incentive}`, `{guest_stay_type}`, `{placement_location}` substituted from `ctx.job.metadata` |

## Workflows for complex agents

> "While it is possible to build some voice agents with a single set of
> good instructions, most use-cases require breaking the agent down into
> smaller components using agent handoffs and tasks."

For the qualification flow, the build plan calls for an *explicit
state machine in Python* (Phase 1B) rather than trusting a single prompt
to track state. This matches LiveKit's workflow guidance — each
qualification gate is effectively its own task.

## Testing

LiveKit Agents has a built-in pytest-compatible testing harness.
Our smoke tests in [apps/agent/tests/test_smoke.py](../../apps/agent/tests/test_smoke.py)
cover persona invariants. Phase 1B will add conversational tests using
`livekit.agents.testing`.
