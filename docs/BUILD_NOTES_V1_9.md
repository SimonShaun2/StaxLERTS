# v1.9 build notes

Historical notes. Current canonical source is [playbook v2.1](../eval/playbook/stax-grokbot-playbook-v2.1.md); see [adoption scope](../eval/playbook/README.md) for implementation gaps.

Canonical source: `eval/playbook/stax-grokbot-playbook-v1.9.md`. Read only; do not edit or silently reconcile owner rules. VERSION line: `VERSION: v1.9 · 2026-09-27`.

1. The server assigns grades. Implement the intraday/non-major sweep cap at B and the requirement for a major sweep or failed major reclaim for A and above in server grading policy. Visual adjustment cannot override the cap. This document specifies that requirement; it is not a claim that a server has been built.
2. `contracts/grok_decision_v2.schema.json` includes `checklist.0_cut`, `checklist.6_invalidation.invalidation_reference`, `invalidation_price`, and `checklist.7_room.session_checklist`. Checklist prices are verified evidence echoes, never invented trade selections. Semantic validation must cross-check the render cut, candidate IDs, references, full level list and ordered first obstacle.

Pine checkpoint now includes A1+A2 only. No A3 state machines, A4 candidate level options, A5 webhooks, A6 strategy twin, or server implementation is authorized in this checkpoint. All deterministic calculations commit on confirmed bars; pivot availability is the confirmation bar, never backdated to the pivot candle.

Known source decisions to clarify before later checkpoints: §1.6 setup-specific invalidation and §1.9 displacement-origin invalidation must be reconciled by the owner if they select different levels. Do not tighten a stop to force R:R. Exact overnight/NY catalog boundaries are configurable because the playbook gives approximate session windows rather than a complete range-calendar specification. B minimum R:R remains owner-pending; drills' 1.5R is not silently promoted to policy.
