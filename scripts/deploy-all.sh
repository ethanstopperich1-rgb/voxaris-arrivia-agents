#!/usr/bin/env bash
# Coordinated deploy for both Deedy + Andie on LiveKit Cloud (Ship plan).
#
# Run from the voxaris-vba root. Requires:
#   - lk CLI authed (`lk cloud auth`)
#   - apps/agent/.env populated and exported
#   - LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET in shell env
#
# Order matters. Read each section before running. Bail on any error.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> Sourcing env"
set -a; source apps/agent/.env; set +a
export LIVEKIT_URL LIVEKIT_API_KEY LIVEKIT_API_SECRET

# ----------------------------------------------------------------------
# Phase 1 — Deedy: redeploy with explicit dispatch + update her rule
# ----------------------------------------------------------------------

echo "==> [Deedy] Redeploying worker with agent_name=deedy-vba"
( cd apps/agent && lk agent deploy )

echo "==> [Deedy] Updating dispatch rule SDR_ito8WVmoAGkV with roomConfig.agents"
# LK doesn't have an `update --replace`, so we delete + recreate at the
# same name. Old SipDispatchRuleId becomes stale; the rule is matched
# by trunk binding (any inbound trunk), so this is safe for a single
# inbound trunk setup. Verify the new SDR is bound after re-create.
lk sip dispatch delete --id SDR_ito8WVmoAGkV || true
lk sip dispatch create infra/livekit/dispatch/deedy-dispatch.json

echo "==> [Deedy] Verifying agent registered with name"
lk agent list

# ----------------------------------------------------------------------
# Phase 2 — Andie: first-time deploy + dispatch rule + bind LK number
# ----------------------------------------------------------------------

echo "==> [Andie] Pushing secrets to LiveKit Cloud"
( cd apps/andie && set -a && source .env && set +a && \
  lk agent secrets set XAI_API_KEY="$XAI_API_KEY" \
                       DEEPGRAM_API_KEY="$DEEPGRAM_API_KEY" \
                       RIME_API_KEY="$RIME_API_KEY" \
                       SENDBLUE_API_KEY_ID="$SENDBLUE_API_KEY_ID" \
                       SENDBLUE_API_SECRET_KEY="$SENDBLUE_API_SECRET_KEY" \
                       SENDBLUE_FROM_NUMBER="$SENDBLUE_FROM_NUMBER" \
                       ARRIVIA_GVR_API_KEY="$ARRIVIA_GVR_API_KEY" \
                       LIVEKIT_SIP_OUTBOUND_TRUNK_ID="$LIVEKIT_SIP_OUTBOUND_TRUNK_ID" \
                       SPECIALIST_PHONE="${SPECIALIST_PHONE:-+10000000000}" \
                       LOG_LEVEL=INFO )

echo "==> [Andie] First-time create"
( cd apps/andie && lk agent create )

echo "==> [Andie] Creating dispatch rule"
ANDIE_SDR=$(lk sip dispatch create infra/livekit/dispatch/andie-dispatch.json --json | jq -r '.sipDispatchRuleId')
echo "    Andie dispatch rule: $ANDIE_SDR"

echo "==> [Andie] Binding +16892608790 to Andie's dispatch rule"
ANDIE_PN_ID=$(lk number list --json | jq -r '.items[] | select(.e164=="+16892608790") | .id')
echo "    Andie phone-number id: $ANDIE_PN_ID"
lk number update --id "$ANDIE_PN_ID" --sip-dispatch-rule-id "$ANDIE_SDR"

# ----------------------------------------------------------------------
# Verification
# ----------------------------------------------------------------------

echo "==> Final state"
echo "Agents:"; lk agent list
echo ""; echo "Dispatch rules:"; lk sip dispatch list
echo ""; echo "Numbers:"; lk number list

echo ""
echo "✅ Deploy complete. Test calls:"
echo "   - Deedy:  +14072890294 (Twilio inbound)"
echo "   - Andie:  +16892608790 (LK Phone Number)"
echo "   - Andie outbound: dispatch via 'lk dispatch create --agent-name andie-gvr ...'"
