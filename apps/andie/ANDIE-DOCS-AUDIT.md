# Andie Source-Doc Audit (2026-05-04)

Cross-checking every Andie source document against the deployed
`apps/andie/voxaris_andie/worker.py`. Each doc is read end-to-end,
mapped to the persona/template/tool that implements it, and any
gaps are flagged.

Source files audited (8 in `~/Downloads/`):

1. `Scope_Confirmation (1).docx` — MVP boundaries
2. `EndtoEnd_Workflow_AI_Agent (1).docx` — high-level journey
3. `Vacation_Rewards_Flow.md` (in repo, no docx)
4. `GVR_Inbound_Call_Script_1.docx` — canonical 7-stage flow
5. `GVR_Condensed_Sales_Script (1).docx` — pitch-deck condensed
6. `GVR_Call_Tranfer_Script.docx` (+ v2)  — outbound transfer flow
7. `GVR_(FAQ_Doc) (1).docx` — **EMPTY** (header only)
8. `Rick_Web_Presentation_Script_v2.docx` — **NEW**, full web walkthrough

---

## 🟢 Coverage matrix — what's in code

| Source-doc requirement | Andie persona / code | Status |
|---|---|---|
| Agent name "Andie" + AI disclosure | `PERSONA_INSTRUCTIONS_TEMPLATE` line 1 | ✅ |
| Pronounce "Andee" / "uh-RIH-vee-uh" | greeting templates | ✅ |
| Recording-disclosure first line | both greetings | ✅ |
| 4 benefit pillars (Savings Credits, Great Getaways, Quarterly Specials, Reward Points) | persona Stage 3 + objection lookup | ✅ |
| 7-stage flow (Greeting → Discovery → Education → CTA → Booking Link → Live Transfer → Close) | persona stages | ✅ |
| Discovery questions (8 listed) | persona Stage 2 prompts | ✅ |
| $250 cash-credit enrollment incentive | `incentive_amount` template var (default `$250`) | ✅ |
| $250 transfer bonus on top → "$500 total" | `transfer_bonus_amount` + `total_after_bonus` vars | ✅ |
| Live transfer (Option A) preferred path | `transfer_to_specialist` tool + Stage 6 prompt | ✅ |
| Microsoft Bookings link (Option B) fallback | `send_scheduler_link` tool + Stage 8 prompt | ✅ |
| Object-loop: temperature check → discovery tieback → re-offer | persona objection-loop section + 84-entry rebuttal lib | ✅ |
| 5 close styles (warm/energetic/confident/soft/brief) | persona Stage 7 | ✅ |
| Routing rules (call wants info / link / live agent) | persona ROUTING block | ✅ |
| FTC red-flag list ("government-approved", "act now") | persona compliance section | ✅ |
| "NOT a government agency / NOT endorsed" disclaimer | persona Identity section | ✅ |
| PCI prohibition (no SSN, no credit card) | persona compliance | ✅ |
| Soft identity verification (email domain / masked phone) | `verify_me_to_caller` tool | ✅ |

---

## 🟡 Findings

### F1 — Terminology: "cash credits" vs "Travel Savings Credits"
**Source docs (5 of 5):** Rick's Web Presentation, Inbound Script, Condensed
Sales Script, and both Transfer Script versions all use **"$250 in cash
credits"** to describe the enrollment incentive. The phrase appears
~14 times across the canonical scripts.

**Persona compliance rule (worker line 502):** *"Travel Savings Credits
are NOT cash, NOT a gift card."*

**Resolution:** These are TWO DIFFERENT THINGS in the source taxonomy:

- **Travel Savings Credits** — the recurring member-benefit currency
  applied at checkout for discounts. Compliance rule: never call this
  "cash" on its own (it's not redeemable, only applies as discount).
- **$250 cash credits** — the one-time enrollment incentive loaded on
  signup or transfer. Sales scripts deliberately call it "cash credits"
  for impact.

The Andie persona now reflects both: the enrollment-incentive prompts
use "cash credits" (matches all 5 source scripts), while the underlying
benefit-currency compliance rule on line 502 keeps "Travel Savings
Credits are NOT cash." Earlier today I had over-corrected (replaced 4
instances of "cash credits" → "Travel Savings Credits"); reverted after
reading the source docs.

### F2 — Rick's web-walk specifics not yet captured in the persona
Rick's Web Presentation Script (NEW, 13.5KB) has detail the persona
doesn't carry today:

- "Pull up your account at [WEBSITE]" — direct member to log in and
  click "My Benefits" so Andie can do a *web walk-through*. Andie
  currently educates verbally only.
- Specific UI breadcrumbs: *"scroll down to Resort Stays Under $499
  and click Preview"*, *"scroll to Save Big Four Times a Year"*,
  *"click Shop Now or Book with Points under Featured Redemptions"*.
- Real example numbers: hotel $480 → $145 with credits; cruise $1,500
  → $1,250 with $250 credits; Cancun all-inclusive 35,000 points
  ($2,898 retail); Carnival cruise 50,000 points ($1,788 retail);
  Waikiki $2,500 → $1,250 quarterly special; Venetian Vegas $2,137
  → $299 Great Getaway week.

**Recommendation:** Add a `web_walk_mode` boolean to member context.
When true, Andie reads the breadcrumbs and pauses for "Got it?" /
"See how that works?" feedback. When false (current behavior), pure
verbal education. Either way, surface 2-3 of the concrete examples
above into the persona's benefit-education stage so members hear real
numbers, not abstractions.

### F3 — "Looping process" objection structure (Step 1 → Step 5)
Both `GVR_Condensed_Sales_Script` and `Rick_Web_Presentation_Script_v2`
specify a 5-step looping process when objections come up:

1. Temperature check — *"Does the offer sound good? Scale 1–10?"*
2. Make the offer better — *"add more points / 7-day cert / Vacation Cash"*
3. Sell yourself (Forrest Gump) — *"3 reasons about yourself"*
4. Ask for the money again — *"validations / sign out / load account"*
5. Step-down (only if necessary) — *"emotional tieback + lower price"*

Andie's current objection loop (worker line ~660) covers Steps 1, 3-ish,
and 4 implicitly. Steps 2 and 5 are **not** in the persona. **MVP
scope (per `Scope_Confirmation`) explicitly excludes "AI-led selling
beyond education"** — so Step 2 (sweetener) and Step 5 (price step-down)
are intentionally out-of-scope. Andie's job is to **educate + transfer**;
Rick's loop is for the human specialist after transfer.

**Status:** ✅ Correct as-is. The closing/loop process is for the
post-transfer human, not Andie.

### F4 — "First-ring answer" requirement
**Inbound Script Stage 1 AGENT NOTE:** *"Andie answers on the first
ring. Keep the tone warm, confident, and human."*

This is a **deployment** constraint, not a persona one. Verified
yesterday — both numbers ring through and the worker speaks within
~1 second of pickup (after the Egress non-blocking fix).

**Status:** ✅

### F5 — Optional "Speak to an agent right now" vs default Bookings link
**EndtoEnd Workflow Section C:** "Handoff option 1 — schedule a virtual
appointment (DEFAULT MVP)" / "Handoff option 2 — speak to an agent
right now (OPTIONAL)".

**Andie persona today:** Lead with **live transfer first** (per user's
explicit direction earlier), Bookings link is the fallback.

**Status:** Persona deviates from MVP spec **on purpose** — user
explicitly requested live-transfer-first ordering during build. Documented
here for future-Phase reconciliation if the human-coverage operations
demand a switch back to Bookings-first.

### F6 — "Friends and Family / Authorized User" benefit
Mentioned in 3 of 5 source docs (Inbound Script Benefit 4 NOTE, Rick
Web Presentation Reward Points section, Condensed Sales Script).

**Andie persona today:** Persona briefly references Family but doesn't
have the structured "Authorized User vs Friends and Family" distinction
Rick's script makes.

**Recommendation:** Phase-2 nice-to-have. Add to objection library if
a member asks "can my family use this?" — currently the FAQ library
has this covered if asked directly.

### F7 — Discovery question set
Source docs are consistent: 8 discovery questions, ask 2-3.

| Question | In Andie persona? |
|---|---|
| "When you signed up, where were you looking to travel?" | ✅ |
| "Top 3 destinations domestic/international" | ✅ |
| "When was your last cruise? Which line, cabin size?" | ✅ |
| "A cruise you want to do but haven't yet?" | ✅ |
| "Last time you stayed at an all-inclusive? Mexico/DR?" | ✅ |
| "Most members spend $2K-$5K — more or less?" | ✅ |
| "3-4 family/friends who travel similarly?" | ✅ |
| "Sounds like you book most of the travel, correct?" | ✅ |

**Status:** ✅ All 8 carried.

### F8 — Closing styles
Inbound Script Stage 7 lists 5 close styles. Andie persona carries
all 5 in the Stage 7 instructions, with the same trigger conditions.
**Status:** ✅

### F9 — FAQ doc is empty (47 chars)
`GVR_(FAQ_Doc) (1).docx` is just a title page with no Q&A entries.
Our `apps/andie/voxaris_andie/data/qa.json` has its own 51-entry GVR
FAQ library populated from the build-plan briefing PDFs.

**Status:** ✅ — repo's qa.json supersedes; the empty docx is just a
header.

---

## What I'm fixing right now

1. **Reverted the "cash credits → Travel Savings Credits" overcorrection**
   in 4 customer-facing prompts. The compliance rule on line 502 stays.
2. **Added the structured 5-step looping-process note** in the persona
   (documenting what's in scope for Andie vs. post-transfer human).

## What needs Phase-2 work

- **F2** — Rick's web-walk mode + concrete dollar examples
  (`hotel $480→$145`, `cruise $1500→$1250`, etc.). 2-hour task, real
  demo-impact lift. Need one shared template the persona quotes
  verbatim so Andie sounds specific instead of abstract.

## What's intentional, not a gap

- **F3** — Selling Steps 2 + 5 omitted on purpose (out of MVP scope).
- **F5** — Live-transfer-first ordering is per explicit user direction.
- **F9** — Empty FAQ docx is superseded by qa.json.

---

*All 8 source docs read in full on 2026-05-04. Worker version
`FKajv68mBWRk` reflects the corrections in this audit (deployed).*
