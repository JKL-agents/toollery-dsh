# Experiment protocol and detailed measurements

Supporting methods, timing definitions and reproduction steps for the [experiment summary](README.md). All results are local development measurements.

## ToolRet population and method

The official [tools](https://huggingface.co/datasets/mangopy/ToolRet-Tools) snapshot is pinned to `e06c38c75612b6536bd959e08cdd345894aba6a7`; [queries](https://huggingface.co/datasets/mangopy/ToolRet-Queries) to `b8c76ad3349ff17497b6bdb28bb5b8f61a0f6445`.

The raw data has 44,453 tools and 7,961 queries. We held out UltraTool, retained the code/customized categories and capped web tools at 3,500 for generation cost control. The deterministic development view has 10,661 tools. Five tools with unsuccessful manual generation and two queries whose full positive sets were no longer present were then excluded, leaving **10,656 tools and 5,679 queries**. The exact exclusions and effective snapshot hash are in [toolret-protocol.json](toolret-protocol.json).

The web subset retains complete positive sets for selected queries, adds deterministically ordered distractors, then retains every query whose entire original positive set is in the frozen pool. Labels remain unchanged. Source categories/labels are used to construct and evaluate this development view. Evaluation queries and relevance labels are not used to generate manuals or build indexes; query text is the runtime search input, and relevance labels do not enter retrieval scoring. This subset changes the retrieval population and difficulty, so its scores are not comparable to the official full ToolRet benchmark.

Three proxy queries are generated per tool using the Appendix A.1 prompt. The tool's description is normalized when usable; otherwise structured tool documentation supplies the evidence directly. No separate LLM-written description is required. BM25 indexes spec, manual query text, or their combination. The original tool schema remains the only tool documentation sent downstream. ToolRet tools are documentation records; no real ToolRet API execution is claimed.

## Seven ToolRet checks

1. **Warm startup and full-pool preparation.** Validate every cached manual, build three indexes, load the DSH bridge, and confirm that the complete 10,656-tool snapshot is available. This does not generate manuals. The historical `dsh_ready_ms` field includes DSH startup plus snapshot/preparation; it is not a timer for the DSH binary alone.
2. **Retrieval quality and migration consistency.** Run the same frozen queries/pool through the DSH integration. Rankings and scores match the core implementation, providing evidence that the migration preserves retrieval behavior.
3. **Per-query recovery and regression.** Compare spec and combined retrieval at multiple k values, counting recovered and newly missed positives and breaking results down by category/source. This helps explain the aggregate improvement and exposes tradeoffs hidden by one mean. It does not establish why every query improves causally.
4. **Native schema projection.** Compare the selected IDs and original schema content with DSH's actual native provider tool payload. This checks the Toollery-to-DSH bridge for dropped, changed or extra tools; it is not a tool-execution accuracy test.
5. **Schema/token budget estimate.** Compare top-k original schemas with the full original-spec pool. Serialized bytes are an auxiliary payload-size measure; estimated tokenizer counts indicate context reduction. Differences among representations reflect which tools were selected and their schema lengths. Manuals are not appended to the provider tool payload. Byte count is not a provider token receipt.
6. **Candidate coverage upper bound.** Use the labelled positives to measure how much of the answer is available in each candidate set, before involving a selector model. Any-hit coverage and all-positive coverage have different denominators. A model restricted to these candidates cannot recover omitted positive tools; this separates retrieval limitations from selector behavior.
7. **Real provider selection, latency and receipts.** Use a fixed stratified sample of 200 queries, three representations and k=3/10, with workers=8. Each request asks a real model to choose among exposed candidates. Record first output, provider duration, whole request duration and actual input/cache/output token receipts. Requests are independent selection tasks, not end-to-end tool execution.

## Detailed ToolRet measurements

### Latency and token receipts

| Representation / k | First output mean (ms) | Provider mean (ms) | Whole request mean (ms) | Whole request p95 (ms) | Prompt tokens | Output tokens | Total tokens |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| spec / 3 | 2,178 | 2,258 | 3,103 | 4,682 | 408,802 | 37,511 | 446,313 |
| manual / 3 | 2,305 | 2,428 | 3,227 | 5,006 | 282,659 | 45,566 | 328,225 |
| spec_manual / 3 | 2,318 | 2,400 | 3,279 | 5,095 | 276,726 | 44,342 | 321,068 |
| spec / 10 | 2,475 | 2,651 | 3,586 | 5,213 | 1,365,855 | 50,685 | 1,416,540 |
| manual / 10 | 2,415 | 2,604 | 3,548 | 5,156 | 688,210 | 53,288 | 741,498 |
| spec_manual / 10 | 2,401 | 2,582 | 3,522 | 5,422 | 746,246 | 49,425 | 795,671 |

Latency statistics describe **one request**, aggregated across 200 queries per row, not the total time for a group of eight workers. Token counts are totals across that row's requests and include conversation/instructions, schemas and provider tokenization; input includes cache-read tokens. Whole request duration also includes local work and scheduling overhead. It is not the sum of the reported mean components, because scopes differ.

Local BM25 means in the recorded run were 11.74/20.32/13.05 ms for spec/manual/combined; request-ready means were approximately 121–156 ms. These noisy sequential local measurements do not establish a speed ordering among representations. BM25 query cost depends on postings for matched terms and scoring work, not only document length. Cache validation, pool consistency and tool assembly have a separate timing scope so local overhead can be inspected independently of remote provider work.

The full-pool OFF request exceeded the tested provider's usable schema/context budget. **There is no successful 10k-tool OFF latency baseline**, and no measured full-pool latency saving is claimed. Payload/token budget estimates remain useful, but do not substitute for such a baseline.

### Manual generation cost

Three generation batches at workers=8 accepted 10,656 manuals. Their summed recorded generation duration was **7,414.50 seconds (123.58 minutes)** and reported token usage was **11,222,001 tokens**. This sums generation batches, including their recorded retries/cache behavior; it is not one clean cold-start experiment and excludes gaps between runs. Dollar costs are not quoted because provider pricing and cache billing vary.

## ToolTalk evaluation protocol

Source: [microsoft/ToolTalk](https://github.com/microsoft/ToolTalk), commit `e05f4ce6132c80ed33392b81535b077d56ab28fd`. It provides 28 tools across seven suites, which fit an OFF/all-tools comparison. We used 28 easy conversations and 10 fixed hard conversations, yielding **79 turns per arm / 158 real DSH turn runs**, with zero runtime errors.

OFF exposes 28 original specs; ON uses latest-user-text BM25 over spec_manual, with a fixed top-3 subset for the entire turn. Both use the same chat model, with workers=8. Each turn receives gold prior conversation context and replayed database state. This is **turn-level evaluation with gold history**, not an autonomous multi-turn rollout. Prior context is serialized as system JSON; the current gold action never enters the model request.

Scoring uses `upstream_api_checks_with_exact_free_text_v1`: upstream API checks plus exact free-text comparison instead of sent2vec similarity. **These are adapted strict scores, not official ToolTalk leaderboard scores.** Final natural-language response quality is not graded.

### Detailed ToolTalk results

| Metric | OFF / all 28 | ON / combined top-3 |
| --- | ---: | ---: |
| Strict successful turns | 67/79 (84.81%) | 51/79 (64.56%) |
| Successful tool-required turns | 37/49 (75.51%) | 21/49 (42.86%) |
| Correct no-call turns | 30/30 | 30/30 |
| Mean whole-turn latency | 2.903 s | 2.743 s |
| Mean first output latency | 1.415 s | 1.676 s |
| Prompt tokens | 591,796 | 129,209 |
| Output tokens | 23,151 | 25,232 |
| Total tokens | 614,947 | 154,441 |

ON candidates contain all required tools for 28/49 tool-required turns; gold call-name coverage is 49/77 (63.64%). Of 16 new losses, 14 involve candidate omissions. The 74.89% token reduction includes incomplete tasks and is not an equal-quality comparison.

Cold generation of 28 manuals took 18.637 seconds / 24,125 reported tokens at workers=8. Warm validation and three indexes took 16-20 ms, excluding full DSH process startup.

## Reproduction entry points

The release contains the experiment drivers and this compact protocol, but not datasets, paid manual caches, provider response logs or private local runs. Exact paid outputs can change with provider/model updates. Run optional scripts from the repository root in a source checkout:

```powershell
& .venv/Scripts/python.exe -m pip install '.[benchmark]'
& .venv/Scripts/python.exe scripts/download_toolret.py
& .venv/Scripts/python.exe scripts/analyze_toolret.py
& .venv/Scripts/python.exe scripts/build_toolret_prototype.py
& .venv/Scripts/python.exe scripts/build_toolret_subset.py
# Configure examples/generator.command.json and a local .env first.
# This next step generates paid manuals for the development view.
& .venv/Scripts/python.exe scripts/pilot_manuals.py --workers 8 --cache-dir work/toollery-cache-development-full --run-prefix toolret-development
& .venv/Scripts/python.exe scripts/benchmark_dsh_toolret.py --phase sanity
& .venv/Scripts/python.exe scripts/benchmark_dsh_toolret.py --phase local
# --phase paid runs real provider selection; --phase all also includes it.
```

The benchmark driver applies the recorded five-tool/two-query exclusions and verifies the exact effective snapshot. Fresh generation may succeed for those tools or fail for others. Additional failures must be resolved or reported as a new population; do not silently label new subsets as this historical run. Generator configuration/interpreter changes conservatively change cache identity, so regenerate caches rather than copying historical cache records across environments.

For ToolTalk, clone the optional MIT-licensed source and use the fixed commit:

```powershell
git clone https://github.com/microsoft/ToolTalk.git upstream/ToolTalk
git -C upstream/ToolTalk checkout e05f4ce6132c80ed33392b81535b077d56ab28fd
& .venv/Scripts/python.exe scripts/benchmark_dsh_tooltalk.py --offline-smoke --output runs/tooltalk-smoke
# Paid full turn-level supplement, with checkpoints:
& .venv/Scripts/python.exe scripts/benchmark_dsh_tooltalk.py --workers 8 --output runs/tooltalk
```

The adapter supplies its exact-text scorer without requiring sent2vec. Optional benchmark scripts use their checked-in examples and repository-root `.env`; the interactive application's user-workspace configuration is separate. See each script's `--help` before paid runs. Public data remains subject to its upstream terms.
