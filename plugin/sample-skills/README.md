# Sample skill pack

This test pack contains 95 standalone skills selected from GitHub's [awesome-copilot](https://github.com/github/awesome-copilot), pinned to commit [`d85f762d6e521abf5fe16703946e9b38adc42da7`](https://github.com/github/awesome-copilot/tree/d85f762d6e521abf5fe16703946e9b38adc42da7). The source repository and bundled skill files are MIT-licensed; see [LICENSE](LICENSE).

The skills are included as a metadata, manual-preparation and body-loading test catalog. The plugin registers them through DSH's native skill provider, with names prefixed by `sample-` to avoid collisions with a user's existing skills. Toollery indexes their metadata in `spec` mode and reads bounded body evidence through DSH's scoped loader when a manual mode needs generation; DSH's native `skill` tool still reads each full `SKILL.md` for selected execution. This pack adds no Python, Node, Office, or other runtime dependencies.

These are general-purpose prompt instructions, not executable tools. Only the `SKILL.md` bodies are bundled; referenced supporting files may be absent (for example, `sample-agent-architecture` refers to unbundled `references/*.md`). Some workflows also require external capabilities such as WorkIQ for personalized email drafting. Loading a body does not verify that its full workflow can run. Missing files or services are separate from DSH permission restrictions; Toollery does not bypass host permissions. For a simple text-only test, use `sample-meeting-minutes` with meeting notes supplied in the prompt.


