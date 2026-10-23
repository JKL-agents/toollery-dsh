// Out-of-tree Cordis plugin. Only transport/lifecycle code lives in JavaScript;
// normalization, generation, versioned caches and retrieval stay in Python.
import { createSourceBarrier } from './sources.mjs'

export const name = 'toollery'
export const inject = ['tools', 'agents', 'systemPrompt']

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
  const endpoint = process.env.TOOLLERY_BRIDGE_URL
  const token = process.env.TOOLLERY_BRIDGE_TOKEN
  if (!managed && (!endpoint || !token || !/^http:\/\/127\.0\.0\.1:\d+$/.test(endpoint))) {
    throw new Error('Toollery requires its local Python bridge')
  }
  const states = new Map()
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
      selection: null, selectionTask: null, disposers: [] }
    states.set(agent, state)

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
                budget: status.budget, k: status.k }
              state.turnOptions = options
              // OFF is a real bypass, including when the worker is unavailable.
              if (!options.enabled) {
                state.selection = managed.bypass(agent.id, assembled.tools, selectionTurn, options)
                return state.selection
              }
              // The raw agent scope is the refresh target; assembled.tools is
              // the effective eligible subset after other plugins' policies.
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
      const tools = assembled.tools.filter(tool => keep.has(tool.name))
      state.selection.model_tool_names = tools.map(tool => tool.name)
      if (state.selection.enabled === false) return { ...assembled, tools }
      await post('/observed', {
        session_id: agent.id, selection_id: state.selection.selection_id,
        turn: state.turn, model_tool_names: tools.map(tool => tool.name),
      }, context.signal)
      if (assemblyEpoch !== state.turnEpoch) throw new Error('Toollery: assembly was superseded by a new user turn')
      // DSH's normal loop persists this exact schema set in request/header.
      return { ...assembled, tools }
    }, { prepend: true }))
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
