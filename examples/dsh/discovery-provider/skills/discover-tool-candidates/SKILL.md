---
name: discover-tool-candidates
description: Find candidate tools for a requested capability using the optional local Toollery discovery-provider example.
---

Use `discover_tools` when the user asks to find a tool or capability in the example catalog. Pass a concise description in `query`.

- Call only if `discover_tools` is in the current callable declarations. If absent, explain that the optional provider must be installed or made visible; do not invent a call.
- The provider supplies the complete local candidate batch. Toollery, rather than the language model, ranks it.
- Summarize the returned `selected` records and their `tool_id`, `candidate_ref` and `spec_hash`. Report that these are recommendations, not executed operations.
- `approval_required` requires owner configuration; do not authorize generation yourself. `discovery_pending` means preparation may still be running; do not start an automatic retry loop.
- Candidate names in the result are not newly callable tools. Do not call them unless DSH independently declares them. If the user asks to execute one, explain that this ranking-only example does not provide registration or execution.
- Existing callable tools may still be used normally. This skill does not bypass host restrictions or Toollery's frozen per-turn selection.
