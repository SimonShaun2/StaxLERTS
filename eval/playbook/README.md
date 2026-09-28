# Canonical playbook

Current canonical source: [Stax GrokBot Playbook v2.1](stax-grokbot-playbook-v2.1.md), owner-supplied and approved on 2026-09-27. Preserve it unchanged. v1.9 and v2.0 remain in this directory as historical references, not the current authority.

The existing A1+A2 build has adopted the equal-level tolerance default **0.25 × confirmed ATR(14)**. It does not claim full v2.1 implementation. It still needs reconciliation for traded-through sweep semantics, opening-range length, new-session relabeling, and pane-relative PARTIAL coverage; contract/schema migration remains pending, including PARTIAL status and the locked B minimum 1.5R. The new deterministic trend-day and Rule A flipped-major retest requirements belong to [A3+A4](../../docs/A3_A4_V2_1_REQUIREMENTS.md). This adoption records those requirements; detector implementation awaits the missing MA/session definitions. No server implementation is included.

Saved TradingView input overrides are independent of Pine defaults. An existing chart instance set to 0.1 must be explicitly changed to 0.25 or have its inputs reset to use the new default.
