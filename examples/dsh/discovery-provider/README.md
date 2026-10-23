# Optional candidate-batch discovery provider

This is a separate, local DSH example bundle. It adds **one** model-callable tool, `discover_tools`, to an existing profile with Toollery installed. It is not installed by the main Toollery package and is not published to npm.

```text
User requests a capability
  -> DSH calls discover_tools(query)
  -> provider returns all six catalog candidates (no prefilter)
  -> Toollery checks cache / optionally generates missing manuals
  -> Toollery ranks this batch in an ephemeral BM25 index
  -> provider returns snapshot-bound recommendations to DSH
```

The six candidates cover addition, multiplication, temperature, distance, hashing and word counts. They remain outside the host registry. Their opaque references resolve to provider-owned local records and callbacks; **this example never invokes those callbacks or registers the candidates**. It demonstrates discovery and ranking, not same-turn tool execution or internet search.

## Before you install

- Use the tested DSH runtime **0.2.1-rc.2** (Desktop target **0.12.1**) and a native root conversation / Standard preset.
- Install and enable `@jkl-agents/dsh-toollery`; set up its local worker. Keep your existing chat model. For manual modes, configure Toollery's manual-generation provider and finish initial tool-pool preparation.
- Download or clone the Toollery repository. This example is at `examples/dsh/discovery-provider/`.
- The provider needs the running `toollery` service in the same Cordis service realm. It does not import or install another Toollery instance. If Toollery is absent/disabled, this dependent plugin remains pending or is unloaded.

## Choose the generation policy

Edit this example's `cordis.patch.yml` **before installing/packing**:

```yaml
      config:
        representation: spec_manual
        k: 3
        allowManualGeneration: false
```

| Configuration | Behavior |
| --- | --- |
| `spec_manual`, generation `false` (default) | Rank using valid cached manuals. If any are missing, return `approval_required` without generating them. |
| `spec_manual`, generation `true` | Reuse cache and generate missing manuals for up to the six trusted local candidates. This sends their schemas to Toollery's configured manual provider and may incur API charges. |
| `spec`, generation `false` | Rank the full batch by specifications, with no candidate-manual generation. Useful for checking the connection first. |
| `manual` | Same manual/cache policy as `spec_manual`, but ranking uses manuals only. |

`k` is from 1 to 6. These are **provider-batch settings**; they are independent of the Toollery panel's host-pool k/mode/ON-OFF settings. The model receives only a `query` argument and cannot approve generation or change these options. Turning normal Toollery filtering OFF does not disable an explicit `discover_tools` call. Disable this example or keep its generation permission false to prevent its candidate generation. Chat calls retain your normal chat-provider charges.

Reinstall/reload the example and start a new conversation after changing its packaged config. Do not assume edits to the original downloaded folder change an already installed copy.

## Install in DSH Desktop

### Local bundle through Creation mode

In a Creation-mode conversation that exposes the `plugin_manager` management tool, ask:

> Install the optional local bundle at `<absolute path to examples/dsh/discovery-provider>` using `plugin_manager` with action `install_bundle`. Inspect its files and configuration first. Do not install another DSH or change the chat provider or other plugin settings. Report whether application was applied or restart-required, and whether the `toollery-discovery-example` entry is active. This example registers only `discover_tools`; do not claim it installs the candidate tools.

Replace the path, for example `D:\work\toollery-dsh\examples\dsh\discovery-provider`. Installing a bundle executes its plugin code and remains subject to DSH's normal approval/access policy. If the manager is unavailable, use the archive route below. If restart is requested, restart before testing. Test in a **new Standard/native conversation**, not the management/PTC conversation.

### Local archive through Plugins

If you have Node/npm, run these PowerShell commands from the downloaded **repository root**:

```powershell
New-Item -ItemType Directory -Force dist | Out-Null
npm pack ./examples/dsh/discovery-provider --ignore-scripts --pack-destination ./dist
```

This packs local source only; it does not publish the package. In **Plugins → Add plugin → Install a third-party plugin**, enter the absolute path to `dist/local-toollery-discovery-example-0.0.0.tgz`. Install, enable and restart if requested. No build, Python installation or new API key is needed for this example itself; it uses the existing Toollery worker and manual settings.

The example archive is not a separately published npm package or a promised release asset. Do not enter its `@local/...` name as a public-registry package.

### Existing DSH CLI profile (optional)

After packing the archive above, use your existing DSH CLI installation:

```powershell
dsh plugin --profile web add "D:\path\to\toollery-dsh\dist\local-toollery-discovery-example-0.0.0.tgz"
dsh --profile web
```

Replace the path and `web` with your existing CLI profile. Toollery must already be installed and configured in that same profile. The CLI requires its supported Node and pnpm environment; this example supplies neither. Desktop uses an application-owned profile: these commands do not configure Desktop, and `--profile desktop` does not select it. Use the Desktop installation route for Desktop.

## Try it

1. Confirm that the example plugin and Toollery are active, with the worker ready. If adding `discover_tools` leaves initial manuals missing, prepare the host pool in Toollery's panel first.
2. For the first check, turn normal Toollery filtering **OFF**, then send a new message. This makes the host-allowed discovery tool available without a top-k visibility dependency; OFF does not bypass DSH restrictions. Once confirmed, test ON with `budget=all` or a suitable k. This example does not reserve an always-visible discovery slot.
3. Send one of these prompts:

   ```text
   Call discover_tools to find tools for converting Celsius temperatures to Fahrenheit. Only report candidate recommendations; do not perform the conversion.
   ```

   ```text
   Call discover_tools to find a tool for computing a SHA-256 hash of text. Report the candidate IDs; do not compute the hash.
   ```

   ```text
   Call discover_tools to find tools for multiplying two numbers and counting words in text. Report the ranked candidates only.
   ```

4. Inspect the **actual `discover_tools` call and result**, rather than the assistant's prose alone. A successful result contains `status: ranked`, `candidate_count: 6`, `candidate_snapshot_hash`, `selection`, and `selected[]` entries with `tool_id`, `candidate_ref`, `spec_hash` and score. Top-k can return fewer than k positive-scoring hits. Exact ranking depends on the representation and generated manuals.
5. In manual mode with generation enabled, the first cold call can generate up to six manuals, each containing three proxy queries. Repeat the **same query**: valid manuals / completed jobs should be reused. Changing the query reranks the same full batch without discarding its manuals. Repeating an identical completed job can return its original `request_id` and preparation report; that report is not evidence of new paid calls.

If Toollery already has manuals for identical schemas and generation settings, the first example call may also reuse them. This is expected.

## Optional skill

The included `skills/discover-tool-candidates/SKILL.md` is model guidance, not registration code. To use it, copy its containing `discover-tool-candidates` directory into the workspace's `.dsh/skills/` directory (or a skill directory configured in your DSH profile), then refresh skills / start a new conversation as your host requires. Confirm DSH lists it before relying on it. Packaging the file does **not** automatically install the skill.

The skill asks the model to use `discover_tools` for discovery requests, respect approval/pending responses, and avoid treating candidate names as callable tools. Skill loading and discovery are themselves subject to current tool visibility; neither guidance nor a skill name makes a filtered or unregistered tool callable.

## Results and troubleshooting

| Observation | Action / interpretation |
| --- | --- |
| `discover_tools` absent | Check the example's activation state and Toollery service dependency; restart if required, then use a new native conversation. Check host restrictions and temporarily use OFF or `all`. |
| `approval_required` | Missing candidate manuals are not authorized. Choose `spec`, or enable the owner's generation flag, reinstall and retry. The example does not silently switch representation or enable paid generation. |
| `discovery_pending` | The foreground wait reached 120 seconds. Generation may continue; retry the same query to reuse the job. Cancellation/timeout is not a promise that charges stopped. Avoid automatic retry loops. |
| Ranking/preparation failure | Check worker/session readiness and the manual provider's model, endpoint and credentials in Toollery. Correct the problem and retry; completed cache entries remain reusable. |
| No matching candidates | Local BM25 can return an empty result. The catalog is small and does not contain live weather, currency rates, calendar APIs or internet search. |
| Assistant tries to call a recommended candidate | Candidate results are not declarations. Use the explicit ranking-only prompts above; this example does not implement registration or execution. |
| Toollery panel shows only one discovery call | Expected. The candidate batch lives in a separate ephemeral search; it is not added to the host-pool count or the current turn's selection. Inspect the discovery result for its ranking. |

Disable/remove `@local/toollery-discovery-example` in Plugins to remove the discovery tool. Remove the copied skill separately if used. Toollery's shared cache is retained. The bundle does not alter your model, persona, host restrictions or other tool registrations.

## Adapt this to your own provider

Keep these steps separate:

1. **Acquire a batch.** Replace `fetchCandidateBatch(signal)` with your authenticated catalog fetch. Return multiple candidates, not a cloud-selected winner. Keep remote credentials and executor implementations provider-side. This example passes all six candidates; there is no spec prefilter. Batches larger than Toollery's 256-candidate limit need an explicitly designed batching/prefilter policy, not silent truncation. Prefiltering changes recall and should be evaluated separately.
2. **Describe identities.** Each candidate supplies `{ tool_id, provider_id, candidate_ref, schema: { name, description, parameters } }`. IDs must be unique in the submitted batch and should be provider-namespaced. The reference is opaque, stable for that provider/version and free of credentials. Schema names are not necessarily global IDs. Keep mappings to executor records outside Toollery.
3. **Rank.** Inject `['tools', 'agents', 'toollery']`, obtain the service with `ctx.get('toollery')`, and call `rankCandidates(execution.agent.id, query, batch, options, execution.signal)` from a native managed root tool handler. The source shows this complete call. Define owner-controlled miss/cost policy; never give the model a generation-approval boolean. The example caps generation at six misses. A real catalog should have explicit batch and cumulative budgets suitable for its scale.
4. **Interpret the result.** Preserve `request_id`, `candidate_snapshot_hash` and each selected `tool_id` / `candidate_ref` / `spec_hash`. Handle approval, pending and failed states. A matching pending/completed request is reusable; do not invent new candidate versions to force a retry.
5. **Resolve and revalidate.** Before any later exposure/execution, check that the recommendation still belongs to the fetched batch and schema version, resolve the opaque reference, and recheck permissions, eligibility and executor availability. This static example validates local identity mapping and never executes. With a mutable catalog, preserve/recheck its version across the await and reject/retry boundedly if it changed. Do not assume a worker snapshot hash alone proves that the remote catalog is still current.
6. **Keep execution host-owned.** If your provider registers selected tools through DSH, normal Toollery reconciliation can incorporate them on a later user turn. Same-turn model-facing additions require a separately implemented explicit selection-epoch transition; returning candidates from `discover_tools` alone does not provide one. Alternatively design a provider-controlled executor with its own DSH registration, policy and validation. That executor is outside this ranking-only example.

Toollery caches candidate schemas/manuals and builds an ephemeral index for the batch. It does not fetch a remote catalog, authorize execution, or modify the current frozen host selection. The provider receives a retrieval recommendation, not an execution grant.

## Implementation references

This small example follows DSH's documented mechanisms rather than importing another discovery framework:

- [DSH tool authoring](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/cookbook/adding-a-tool.md): raw JSON-Schema registration, execution identity, cancellation and output rendering. Raw tools own their input validation.
- [Cordis services](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/cordis-tutorial/03-services.md): dependency injection and dependent-plugin lifecycle.
- [DSH dynamic composition](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/develop/practice/dynamic-cordis.md): provider/agent installs a bundle through the management interface.
- [Toollery plugin API](../../../docs/plugin.md#provider-owned-discovery-batches): local batch ranking and lifecycle boundaries.

Upstream links describe a moving branch; this example targets the pinned runtime above. No third-party discovery source code is vendored.
