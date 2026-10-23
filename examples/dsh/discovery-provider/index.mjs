// The only registered tool is discover_tools. Candidate executors remain
// provider-owned and are deliberately NOT registered or invoked by this demo.
import { randomUUID } from 'node:crypto'
import { fetchCandidateBatch, resolveCandidate } from './catalog.mjs'

export const name = 'toollery-discovery-example'
export const inject = ['tools', 'agents', 'toollery']

export function apply(ctx, config = {}) {
  const service = ctx.get('toollery')
  const representation = config.representation ?? 'spec_manual'
  const k = config.k ?? 3
  if (!['spec', 'manual', 'spec_manual'].includes(representation)) throw new Error('Invalid discovery representation.')
  if (!Number.isInteger(k) || k < 1 || k > 6) throw new Error('Discovery k must be an integer from 1 to 6.')
  if (config.allowManualGeneration !== undefined && typeof config.allowManualGeneration !== 'boolean') {
    throw new Error('allowManualGeneration must be a boolean.')
  }
  ctx.tools.register({
    name: 'discover_tools',
    description: 'Discover tools for a requested capability: retrieve and rank a full batch from a local demo catalog using Toollery. Returns candidate recommendations only; it does not install, expose, authorize or execute the candidates. Use this when asked to find tools, not to perform an operation.',
    parameters: { type: 'object', properties: {
      query: { type: 'string', description: 'Describe the capability or task for which tools should be found.' },
    }, required: ['query'], additionalProperties: false },
    output: { schema: { type: 'string' }, render: (_args, value) => [{ type: 'text', text: value }] },
    async execute(args, execution) {
      execution.signal.throwIfAborted()
      if (!args || typeof args !== 'object' || Array.isArray(args) || Object.keys(args).length !== 1
          || typeof args.query !== 'string' || !args.query.trim() || args.query.length > 4000) {
        throw new Error('Provide only a nonempty query string, at most 4000 characters.')
      }
      if (!execution.agent || !ctx.agents.roots().includes(execution.agent)) {
        throw new Error('Discovery example requires a native root conversation managed by Toollery.')
      }
      const batch = await fetchCandidateBatch(execution.signal)
      let result
      try {
        result = await service.rankCandidates(execution.agent.id, args.query, batch, {
          representation, budget: 'top_k', k, requestId: `local-demo-${randomUUID()}`,
          // The owner config, never a model argument, controls paid generation.
          // Never override the limit with allowGeneration: true.
          maxAutoTools: config.allowManualGeneration === true ? batch.length : 0,
          allowGeneration: false, timeoutMs: 120000,
        }, execution.signal)
      } catch (error) {
        execution.signal.throwIfAborted()
        if (error.code !== 'discovery_pending') {
          throw new Error('Discovery ranking failed. Check Toollery worker setup, manual-provider settings and session readiness; retry the same query to reuse pending work.')
        }
        return JSON.stringify({ status: 'discovery_pending', generation_cancelled: false,
          job_id: error.detail?.job_id,
          candidate_snapshot_hash: error.detail?.candidate_snapshot_hash,
          message: 'Preparation is still running. Retry the same query to reuse it; timeout did not cancel generation.' })
      }
      execution.signal.throwIfAborted()
      if (result.status === 'approval_required') {
        return JSON.stringify({ status: result.status, request_id: result.request_id,
          candidate_snapshot_hash: result.candidate_snapshot_hash, candidate_count: result.candidate_count,
          missing: result.missing, max_auto_tools: result.max_auto_tools,
          message: 'Candidate manuals are missing. The owner can enable allowManualGeneration in this example bundle and reinstall it, or use representation: spec for ranking without manual-generation charges. Then retry in a new conversation.' })
      }
      if (result.status !== 'ranked') throw new Error('Candidate manual preparation failed. Check Toollery provider settings and retry.')
      // This immutable local catalog cannot change while ranking. For a live
      // catalog, also revalidate its snapshot/version, schema and permissions.
      for (const hit of result.selected) {
        const record = resolveCandidate(hit.tool_id, hit.candidate_ref)
        if (!record || record.schema.name !== hit.name) throw new Error('Ranking returned an unknown candidate identity.')
      }
      return JSON.stringify({ status: result.status, request_id: result.request_id,
        candidate_snapshot_hash: result.candidate_snapshot_hash, candidate_count: result.candidate_count,
        selection: result.selection, selected: result.selected, manual_report: result.manual_report,
        message: 'Recommendations only. No candidate was registered or executed. Do not call candidate names unless DSH separately declares them as available tools.' })
    },
  })
}
