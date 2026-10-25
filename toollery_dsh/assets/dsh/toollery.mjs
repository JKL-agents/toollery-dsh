// Out-of-tree Cordis plugin. Only transport/lifecycle code lives in JavaScript;
// normalization, generation, versioned caches and retrieval stay in Python.
import { createSourceBarrier } from './sources.mjs'
import { randomUUID } from 'node:crypto'
import { catalogDescription, catalogMessage, loadSkillCandidates, projectSkillCatalog, visibleSkillCatalog } from './skills.mjs'

export const name = 'toollery'
export const inject = ['tools', 'agents', 'systemPrompt', 'skills']

const ordered = schemas => [...schemas].sort((a, b) => a.name < b.name ? -1 : a.name > b.name ? 1 : 0)

// Match the frozen snapshot without serializing large descriptions every turn.
// Descriptions compare as strings; parameter objects still receive a deep,
// order-independent comparison, including additions/removals and array order.
export function sameValue(left, right) {
  if (left === right) return true
  if (!left || !right || typeof left !== 'object' || typeof right !== 'object') return false
  if (Array.isArray(left) !== Array.isArray(right)) return false
  const keys = Object.keys(left)
  return keys.length === Object.keys(right).length
    && keys.every(key => Object.hasOwn(right, key) && sameValue(left[key], right[key]))
}
export function sameSnapshot(schemas, frozen) {
  if (schemas.length !== frozen.size) return false
  const seen = new Set()
  return schemas.every(schema => {
    if (seen.has(schema.name)) return false
    seen.add(schema.name)
    return sameValue(schema, frozen.get(schema.name))
  })
}

export function apply(ctx, config = {}) {
  const managed = config.managed ? (config.service || ctx.get('toollery')) : null
  const residentToolNames = new Set(['skill', ...(Array.isArray(config.residentToolNames) ? config.residentToolNames : [])])
  // Toollery ranks the native skill catalog published for this agent and
  // workspace. Manual mode loads bodies only through DSH's scoped native
  // loader; it never reads an unscoped registry or publishes bodies to history.
  const endpoint = process.env.TOOLLERY_BRIDGE_URL
  const token = process.env.TOOLLERY_BRIDGE_TOKEN
  if (!managed && (!endpoint || !token || !/^http:\/\/127\.0\.0\.1:\d+$/.test(endpoint))) {
    throw new Error('Toollery requires its local Python bridge')
  }
  const states = new Map()
  if (managed && typeof managed.invalidateSkillPreflight === 'function') {
    ctx.on('skills/change', () => managed.invalidateSkillPreflight())
  }
  const discover = managed ? async () => ({ complete: true, source: 'host-agent-scope' })
    : createSourceBarrier(ctx, config.toolSources ?? [])
  async function post(path, payload, signal) {
    if (managed) return managed.post(path, payload, signal)
    // Web sessions prepare their frozen snapshot before publication. Most hit
    // cache; an explicit generation-enabled launch may need longer.
    const timeout = AbortSignal.timeout(path === '/snapshot' ? 1800000 : 15000)
    const response = await fetch(endpoint + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token },
      body: JSON.stringify(payload),
      signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
    })
    if (!response.ok) throw new Error(`Toollery bridge ${path} failed (${response.status})`)
    return response.json()
  }
  ctx.on('agent/created', async ({ agent, signal }) => {
    if (managed && !ctx.agents.roots().includes(agent)) return
    // Register the listener synchronously. Source errors abort agent creation;
    // they must never unload this filter and leave an unfiltered agent behind.
    const discovery = await discover()
    signal?.throwIfAborted()
    const schemas = ordered(ctx.tools.schemas(agent))
    if (managed && (!schemas.length || schemas.some(tool => tool.name === 'run_code'))) {
      managed.unsupported(agent.id, schemas.length ? 'PTC presentation is not supported. Use a native agent to enable Toollery.' : 'This agent has no tools.')
      return
    }
    if (schemas.some(tool => tool.name === 'run_code')) throw new Error('Toollery requires native tool presentation')
    const snapshot = managed ? await managed.register(agent.id, schemas)
      : await post('/snapshot', { session_id: agent.id, schemas, discovery }, signal)
    const state = { frozen: new Map(structuredClone(schemas).map(tool => [tool.name, tool])),
      snapshot, messages: [], turn: null, turnEpoch: 0, turnOptions: null,
      selection: null, selectionTask: null, skillSelection: null, skillSelectionEpoch: null, skillTask: null,
      skillCatalog: null, skillBodies: new Map(), disposers: [] }
    states.set(agent, state)
    if (managed && typeof managed.registerSkillCatalogLoader === 'function') {
      managed.registerSkillCatalogLoader(agent.id, async ({ signal } = {}) => {
        const workingDirectory = ctx.get('workingDirectory')
        const cwd = workingDirectory?.ensure ? await workingDirectory.ensure(agent, signal) : undefined
        const snapshot = await ctx.skills.snapshot({ cwd, signal, scope: agent })
        if (!snapshot.complete) throw new Error('The native skill catalog is still updating. Retry the cache check shortly.')
        const entries = snapshot.skills.map(skill => ({
          name: skill.name, description: catalogDescription(skill.description), invocation: skill.invocation,
        }))
        return loadSkillCandidates(entries, {
          skills: ctx.skills, workingDirectory, agent, signal, cache: state.skillBodies,
          loadBodies: true,
        })
      })
      const initial = await managed.settings(agent.id)
      if (initial.enabled && ['skills', 'both'].includes(initial.retrieval_target)
          && initial.skill_representation !== 'spec') {
        try { await managed.preflightSkills(agent.id) } catch { /* The composer presents a retryable cache-check state. */ }
      }
    }

    // Callable schemas describe NOW. Without an explicit temporal contract a
    // model can mistake a past, successful call for an invented tool once that
    // schema leaves the subset. Keep this policy in the integration, not only
    // in the demo persona, and let DSH retain the original execution history.
    state.disposers.push(agent.ctx.systemPrompt.section({
      name: 'toollery:tool-scope-policy',
      order: 4900,
      interpolate: false,
      text: 'Tools are selected separately for each user turn. Current tool declarations govern which tools you may call now, not which tools existed in earlier turns. Historical tool calls and results retain their original meaning and success or error status when the current tool set changes. Assess earlier executions from their recorded calls and results; absence from the current tool list is not evidence that a past call was invented or failed.',
    }))
    state.disposers.push(agent.ctx.systemPrompt.context({
      name: 'toollery:current-tool-scope',
      order: 130,
      required: true,
      interpolate: false,
      // Selection is resolved by the assembly waterfall below. DSH refreshes
      // context providers afterwards, then durably admits this at the current
      // user turn. No full-pool names or schemas are added to model history.
      text: () => state.selection === null ? '' : (
        `Tool scope: user turn ${state.selection.turn}; ${state.selection.selected_names.length} tools currently callable. `
        + 'Current declarations apply to this turn only. Earlier tool calls and results retain their recorded success or error status.'
      ),
    }))

    state.disposers.push(agent.ctx.on('agent/inbox/claimed', ({ message, turn }) => {
      if (message.source?.kind !== 'user') return
      if (turn !== state.turn) {
        state.messages = []
        state.turnEpoch += 1
        state.selection = null
        state.selectionTask = null
        state.skillSelection = null
        state.skillSelectionEpoch = null
        state.skillTask = null
        state.turnOptions = null
      }
      state.turn = turn
      state.messages.push(message)
    }))
    // A guard also covers scoped tools, which tools.restrict() deliberately
    // leaves visible. The full registry stays intact for the next prompt.
    state.disposers.push(agent.ctx.tools.guard(execution => {
      if (state.selection?.enabled === false || (!state.selection && managed && !managed.enabled(agent.id))) return
      return state.selection?.model_tool_names?.includes(execution.name)
        ? undefined : 'Toollery: tool is outside the current prompt selection'
    }))

    state.disposers.push(agent.ctx.on('tools/execute', async (execution, next) => {
      const selection = state.selection
      if (!selection) return next()
      const call = { session_id: agent.id, selection_id: selection.selection_id,
        call_id: execution.callId, name: execution.name }
      // Observe real dispatched calls, not the retrieval candidates. Reporting
      // failures must never cause a successfully executed tool to be retried.
      const report = async status => {
        try {
          if (managed) await managed.recordCall({ ...call, status })
          else await post('/call', { ...call, status })
        }
        catch (error) { console.error('Toollery call observation failed:', error.message) }
      }
      await report('running')
      try {
        const result = await next()
        await report(result.isError ? 'error' : 'success')
        return result
      } catch (error) {
        await report('error')
        throw error
      }
    }))

    state.disposers.push(agent.ctx.on('system-prompt/assemble', async (_assembly, context, next) => {
      const assemblyEpoch = state.turnEpoch
      const assembled = await next()
      if (assemblyEpoch !== state.turnEpoch) throw new Error('Toollery: assembly was superseded by a new user turn')
      // The first assembly of a turn owns one single-flight operation covering
      // settings, pool reconciliation, retrieval and selection freeze. Later
      // model steps await the same result; UI changes therefore apply next turn.
      if (state.selection === null) {
        if (state.messages.length === 0) throw new Error('Toollery: no current user text to retrieve with')
        const selectionTurn = state.turn
        const selectionEpoch = state.turnEpoch
        const eligibleNames = assembled.tools.map(tool => tool.name)
        const currentTask = state.selectionTask
        if (!currentTask || currentTask.epoch !== selectionEpoch) {
          const messages = structuredClone(state.messages)
          const prompt = messages.flatMap(message => message.content)
            .filter(block => block.type === 'text').map(block => block.text).join('\n')
          const promise = (async () => {
            let options
            if (managed) {
              const status = await managed.settings(agent.id)
              if (selectionEpoch !== state.turnEpoch) throw new Error('Toollery: selection was superseded by a new user turn')
              options = { enabled: status.enabled, representation: status.representation,
                skill_representation: status.skill_representation || status.representation,
                budget: status.budget, k: status.k,
                tool_k: status.tool_k ?? status.k, skill_k: status.skill_k ?? status.k,
                retrieval_target: status.retrieval_target || 'tools' }
              state.turnOptions = options
              // OFF is a real bypass, including when the worker is unavailable.
              if (!options.enabled) {
                state.selection = managed.bypass(agent.id, assembled.tools, selectionTurn, options, ctx.tools.schemas(agent))
                return state.selection
              }
              // The raw agent scope is the refresh target; assembled.tools is
              // the effective eligible subset after other plugins' policies.
              if (options.retrieval_target === 'skills') {
                state.selection = managed.bypass(agent.id, assembled.tools, selectionTurn,
                  { ...options, enabled: true, retrieval_target: 'skills' }, ctx.tools.schemas(agent))
                return state.selection
              }
              await managed.reconcile(agent.id, () => ordered(ctx.tools.schemas(agent)), context.signal,
                { representation: options.representation })
            } else if (!sameSnapshot(ctx.tools.schemas(agent), state.frozen)
              || assembled.tools.some(tool => !sameValue(tool, state.frozen.get(tool.name)))) {
              throw new Error('Toollery: tool pool changed; restart and prepare its new snapshot')
            }
            if (selectionEpoch !== state.turnEpoch || selectionTurn !== state.turn) {
              throw new Error('Toollery: selection was superseded by a new user turn')
            }
            const selection = await post('/select', {
              session_id: agent.id,
              snapshot_hash: managed ? managed.snapshotHash(agent.id) : snapshot.snapshot_hash,
              pool_revision: managed ? managed.poolRevision(agent.id) : undefined,
              prompt, eligible_names: eligibleNames,
              options, message_ids: messages.map(message => message.id), turn: selectionTurn,
            }, context.signal)
            if (selectionEpoch !== state.turnEpoch || selectionTurn !== state.turn) {
              throw new Error('Toollery: selection was superseded by a new user turn')
            }
            state.selection = selection
            return selection
          })()
          state.selectionTask = { epoch: selectionEpoch, promise }
          promise.finally(() => {
            if (state.selectionTask?.promise === promise) state.selectionTask = null
          }).catch(() => {})
        }
        const task = state.selectionTask
        if (!task || task.epoch !== state.turnEpoch) {
          throw new Error('Toollery: selection was superseded by a new user turn')
        }
        await task.promise
        if (selectionEpoch !== state.turnEpoch || !state.selection) {
          throw new Error('Toollery: selection was superseded by a new user turn')
        }
      }
      const keep = new Set(state.selection.selected_names)
      // Keep resident tools only while the host assembly makes them eligible.
      // Never revive a restricted/unloaded loader from an older snapshot.
      const tools = state.selection.retrieval_target === 'skills'
        ? assembled.tools
        : assembled.tools.filter(tool => keep.has(tool.name) || residentToolNames.has(tool.name))
      state.selection.model_tool_names = tools.map(tool => tool.name)
      if (state.selection.enabled === false || state.selection.retrieval_target === 'skills') return { ...assembled, tools }
      await post('/observed', {
        session_id: agent.id, selection_id: state.selection.selection_id,
        turn: state.turn, model_tool_names: tools.map(tool => tool.name),
      }, context.signal)
      if (assemblyEpoch !== state.turnEpoch) throw new Error('Toollery: assembly was superseded by a new user turn')
      // DSH's normal loop persists this exact schema set in request/header.
      return { ...assembled, tools }
    }, { prepend: true }))

    if (managed) {
      state.disposers.push(agent.ctx.on('agent/pre-step', async (event, next) => {
        const epoch = state.turnEpoch
        const decision = await next()
        if (epoch !== state.turnEpoch) throw new Error('Toollery: skill selection was superseded by a new user turn')
        if (decision?.kind === 'reject') return decision
        const nativeCatalog = catalogMessage(decision?.messages || [])
        if (nativeCatalog) state.skillCatalog = structuredClone(nativeCatalog)
        if (state.selection?.enabled === false
          || !['skills', 'both'].includes(state.turnOptions?.retrieval_target || 'tools')) return decision
        // Native DSH omits the catalog when its full pool matches history.
        // Retain that full catalog so all -> top-k and OFF -> ON still rerank.
        // On a resumed session the first native step can already suppress its
        // catalog because the visible history matches the full current pool.
        const catalog = nativeCatalog || state.skillCatalog || visibleSkillCatalog(agent)
        if (catalog && !state.skillCatalog) state.skillCatalog = structuredClone(catalog)
        if (!catalog || !state.messages.length || !state.selection?.model_tool_names?.includes('skill')) return decision
        const current = state.skillTask
        if (state.skillSelectionEpoch !== epoch && (!current || current.epoch !== epoch)) {
          const prompt = state.messages.flatMap(message => message.content || [])
            .filter(block => block?.type === 'text').map(block => block.text).join('\n')
          const promise = (async () => {
            const options = state.turnOptions || await managed.settings(agent.id)
            const candidates = await loadSkillCandidates(catalog.source.entries, {
              skills: ctx.skills, workingDirectory: typeof ctx.get === 'function' ? ctx.get('workingDirectory') : undefined,
              agent, signal: event.signal, cache: state.skillBodies,
              loadBodies: (options.skill_representation || options.representation) !== 'spec',
            })
            const result = await managed.rankSkills(agent.id, prompt, candidates, {
              representation: options.skill_representation || options.representation,
              budget: options.budget, skill_k: options.skill_k ?? options.k,
              requestId: `skills-${state.turn}-${epoch}`,
            }, event.signal)
            if (epoch !== state.turnEpoch) throw new Error('Toollery: skill selection was superseded by a new user turn')
            state.skillSelection = result
            state.skillSelectionEpoch = epoch
            return result
          })()
          state.skillTask = { epoch, promise }
          promise.finally(() => {
            if (state.skillTask?.promise === promise) state.skillTask = null
          }).catch(() => {})
        }
        const result = state.skillSelectionEpoch === epoch
          ? state.skillSelection : await state.skillTask.promise
        const selected = result?.selected_names || []
        if (state.selection?.selection_id && typeof managed.recordSkillSelection === 'function') {
          await managed.recordSkillSelection({ session_id: agent.id, selection_id: state.selection.selection_id,
            selected_names: selected, candidate_count: result?.candidate_count,
            retrieval_ms: result?.retrieval_ms })
        }
        if (epoch !== state.turnEpoch) throw new Error('Toollery: skill selection was superseded by a new user turn')
        const previous = visibleSkillCatalog(agent)
        const projected = projectSkillCatalog({ ...catalog,
          ...(nativeCatalog ? {} : { id: randomUUID() }),
          source: { ...catalog.source, ...(previous ? { update: true } : {}) },
        }, selected)
        // The native consumer may offer its full catalog every model step
        // because history contains our subset. Do not repeatedly admit the
        // same projected subset or lose the native update on the next turn.
        const unchanged = previous && sameValue(previous.source.entries, projected.source.entries)
        const messages = decision.messages.flatMap(message => message === nativeCatalog
          ? (unchanged ? [] : [projected]) : [message])
        if (!nativeCatalog && !unchanged) messages.push(projected)
        return { ...decision, messages }
      }, { prepend: true }))
    }
  })
  function dispose(agent) {
    const state = states.get(agent)
    managed?.unregister(agent.id)
    if (!state) return
    states.delete(agent)
    state.turnEpoch += 1
    state.selectionTask = null
    for (const cleanup of state.disposers.reverse()) cleanup()
  }
  ctx.on('agent/disposed', ({ agent }) => dispose(agent))
  ctx.effect(() => () => { for (const agent of [...states.keys()]) dispose(agent) })
}
