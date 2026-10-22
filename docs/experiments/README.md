# Experiment summary

Local development results on **ToolRet** (retrieval) and **ToolTalk** (executable tool use), using `deepseek-flash` where a model was required. These are subset/adapted evaluations, not the original paper's results or official leaderboard scores. See [protocol and detailed measurements](protocol.md) for dataset revisions, timing definitions and reproduction steps; [release verification](../verification.md) covers packaging separately.

## ToolRet: manuals improve candidate retrieval

The frozen development pool contains **10,656 tools and 5,679 queries**. UltraTool is held out; web tools are capped at 3,500 for cost control. Five failed manual generations and two affected queries are excluded. Each tool has three proxy queries; query text and relevance labels are never inputs to manual generation. The [recorded protocol](toolret-protocol.json) fixes the effective population.

| Search representation | Recall@3 | Recall@10 |
| --- | ---: | ---: |
| spec | 32.52% | 42.00% |
| manual | 32.91% | 44.95% |
| spec_manual | **37.23%** | **48.48%** |

Recall is the mean fraction of labelled positive tools retrieved per query. **Combined retrieval gains 4.71 / 6.48 percentage points over spec at k=3 / 10.** This supports manuals as a useful retrieval representation on this subset; it does not measure task completion.

Other checks establish the following:

- **Migration preserves behavior.** DSH retrieval matches the core implementation, and native projection preserves selected IDs and original schemas. Manuals remain local search text.
- **Top-k reduces schema context.** Token estimates compare selected original schemas against the full pool. Differences among representations reflect selected tools' schema lengths, not manual compression.
- **Candidate coverage limits the selector.** A downstream model cannot choose a required tool omitted by retrieval. Coverage therefore isolates retrieval limitations from model selection quality.
- **Provider time dominates local preparation.** Across 200 queries per configuration, three representations and k=3/10 (1,200 requests, workers=8), mean request latency was 3.10-3.59 s; local request preparation was about 121-156 ms. Latencies describe individual requests, not batches of eight.

The full-pool OFF request exceeded the provider's usable schema/context budget. **ToolRet provides no measured full-pool OFF latency baseline.** All timings come from one Windows machine and are sensitive to local load, network and provider scheduling.

## ToolTalk: narrow candidates can harm execution

The supplement uses **28 tools**, 38 conversations and **79 evaluated turns per arm**. OFF exposes all original schemas; ON uses combined top-3 retrieval from the latest user text, fixed throughout each turn. Turns receive gold prior history and replayed database state; scoring uses adapted strict API/exact-text checks. This is not an autonomous rollout or an official ToolTalk score.

| Metric | OFF / all 28 | ON / combined top-3 |
| --- | ---: | ---: |
| Successful turns | 67/79 (84.81%) | 51/79 (64.56%) |
| Successful tool-required turns | 37/49 (75.51%) | 21/49 (42.86%) |
| Mean whole-turn latency | 2.903 s | 2.743 s |
| Mean first output latency | 1.415 s | 1.676 s |
| Total provider tokens | 614,947 | 154,441 |

All 158 turn runs completed without runtime errors. ON introduced **16 losses and no recoveries**; 14 losses involved omitted candidates. Latest-user-text retrieval misses follow-up context and dependencies, and a fixed subset cannot recover later in the turn.

The **74.89% token reduction includes incomplete tasks**, so it is not an equal-quality efficiency gain. Whole-turn latency fell slightly while first output became slower; this does not establish a general speed benefit.

The datasets answer different questions: ToolRet supports improved retrieval with manuals; ToolTalk exposes the execution risk of a small, context-insensitive subset. These findings are compatible. History-aware retrieval, dependency expansion and recovery search remain outside v0.1.0.

## Manual preparation cost

| Dataset | Manuals | Recorded duration | Reported tokens |
| --- | ---: | ---: | ---: |
| ToolRet | 10,656 | 123.58 min | 11,222,001 |
| ToolTalk | 28 | 18.637 s | 24,125 |

Both used workers=8. ToolRet sums three generation batches, including recorded retry/cache behavior; it is **not one clean cold start**. ToolTalk is a clean small cold-generation measurement; warm manual validation and three indexes took 16-20 ms, excluding DSH startup. Dollar costs depend on provider pricing and cache billing.
