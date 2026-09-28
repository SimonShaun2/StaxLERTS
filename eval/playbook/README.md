# Canonical playbook

Current canonical source: [Stax GrokBot Playbook v2.0](stax-grokbot-playbook-v2.0.md), owner-supplied and approved on 2026-09-27. Preserve it unchanged. v1.9 remains in this directory as historical reference, not the current authority.

This adoption commit changes the Pine equal-level tolerance default to **0.25 × confirmed ATR(14)** only. It does not claim full v2.0 implementation. The existing A1+A2 build still needs reconciliation for traded-through sweep semantics, opening-range length, new-session relabeling, and pane-relative PARTIAL coverage; contract/schema migration remains pending, including PARTIAL status and the locked B minimum 1.5R. No A3/A4 work or server implementation is included.

Saved TradingView input overrides are independent of Pine defaults. An existing chart instance set to 0.1 must be explicitly changed to 0.25 or have its inputs reset to use the new default.
