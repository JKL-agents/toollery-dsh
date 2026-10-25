# Toollery for DeepSeek Harness v0.3.1

v0.3.1 completes the first native skill-manual workflow and refines the target-aware controls without changing DSH's ownership of registration, permissions or execution.

## Native skill manuals

- Load eligible skill bodies through DSH's scoped native loader only for manual preparation.
- Generate bounded body-derived intent queries and store them in a profile-local `skill-cache` with provenance-linked identities.
- Support `spec`, `manual` and `spec_manual` skill representations. `spec` remains metadata-only; manual modes reuse valid entries and invalidate them when body, catalog metadata or generator identity changes.
- Keep full skill bodies and generated manuals out of model-visible catalog history. DSH's native `skill` provider still loads and executes selected bodies.

## Pre-submission authorization

- Check the current scoped skill catalog and local cache before a prompt is sent.
- Ask for explicit approval only when skill manuals are missing. The cache check itself makes no model call.
- Preserve the composer draft and block submission while generation is running; unlock it when preparation completes. `/toollery prepare-skills` remains available for deliberate pre-generation.
- Report preparation progress and approximate ETA based on completed uncached manual work.

## UI and boundaries

- Use `Tool rep` and `Skill rep` labels in the target-aware panel; `skills` shows only Skill rep and `both` shows both.
- Remove the redundant Indexed tool pool line.
- Keep tool k and skill k independent; in `both`, one **Update k** action applies both displayed values together. `/toollery k N` remains the command-line compatibility form.
- Update OFF messaging to explain that Toollery retrieval is bypassed while DSH's host-eligible tools and native skill catalog remain available under host restrictions.
- Toollery does not yet make every native DSH tool permanently resident while ON. The native `skill` declaration is retained, and complete host-eligible tool visibility is preserved by OFF and the `skills` target.

## Verification

Validation passed: 83 Python tests (80 passed, 3 optional ToolTalk skips), 29 Node CI/UI/service tests, 5 packaged-plugin tests, and 12 checks in the real native DSH fixture (`paid_api_calls: 0`). The totals include 22 focused Python skill-manual tests and 6 Node UI/route tests. The wheel and source archive passed `twine check`; the npm tarball embeds the matching worker wheel. See [the verification summary](verification.md) for the detailed scope and historical v0.3.0 checks.

Install the matching `@jkl-agents/dsh-toollery` npm version or the v0.3.1 `.tgz` asset from the [GitHub release](https://github.com/JKL-agents/toollery-dsh/releases/tag/v0.3.1).
