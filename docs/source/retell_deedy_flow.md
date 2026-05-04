# Retell Deedy flow (snapshot 2026-05-02)

> Source: pasted from Retell agent `agent_0e698d33fb60b7da9eff5d5654`
> live phone: `+14078538108`
> conversation_flow_id: `conversation_flow_7b33ee185da7`
> live_version: 1
> node_count: 22
> start_node: `start_disclosures`

The full JSON config (global_prompt, 22 nodes, edges, transitions, the
`opc_book` tool definition, default_dynamic_variables) is what the
LiveKit worker's persona is ported from. Fields ported:

- `global_prompt` → "Hard rules" + "PCI absolute prohibition" + "Two-strike rule" sections of [worker.py PERSONA_INSTRUCTIONS_TEMPLATE](../../apps/agent/voxaris_agent/worker.py)
- 22 nodes → "Conversation flow" section enumerated 1–22
- `opc_book` tool → `VBAQualifierAgent.opc_book` function tool, calls
  the existing endpoint at `https://arrivia-gvr.vercel.app/api/tools/opc-book`
  with the same x-api-key header.
- `default_dynamic_variables` → `DEFAULT_GUEST_CONTEXT` in worker.py
  (premium_offer = Disney park hopper tickets, slot_1 = "tomorrow at
  10:30 AM", etc.).

The Retell agent uses Claude 4.5 Haiku at temperature 0; we use Grok 4.1
fast (non-reasoning) at temperature 0.4 via LiveKit Inference.

## Migration notes

- Retell tracks state explicitly via the node graph; we track it
  inside the LLM's context. Phase 1B may upgrade to a real Python
  state machine if the single-prompt version drifts.
- Retell's `transition_condition` semantics are "if guest says X, go
  to node Y". In our prompt, we encode the same as natural-language
  rules ("if guest agrees → step N"). Grok navigates.
- `lookup_objection` (our tool, not in Retell) supplements the
  rebuttal phrasing inside obj_time / obj_sales / obj_general nodes
  by pulling from the Top 100 Objections playbook.
