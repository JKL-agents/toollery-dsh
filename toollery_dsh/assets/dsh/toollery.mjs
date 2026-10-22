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
  const endpoint = process.env.TOOLLERY_BRIDGE_URL
  const token = process.env.TOOLLERY_BRIDGE_TOKEN
  if (!endpoint || !token || !/^http:\/\/127\.0\.0\.1:\d+$/.test(endpoint)) {
    throw new Error('Toollery requires its local Python bridge')
  }
  const states = new Map()
  const discover = createSourceBarrier(ctx, config.toolSources ?? [])
  async function post(path, payload, signal) {
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
    // Register the listener synchronously. Source errors abort agent creation;
    // they must never unload this filter and leave an unfiltered agent behind.
    const discovery = await discover()
    signal?.throwIfAborted()
    const schemas = ordered(ctx.tools.schemas(agent))
    if (schemas.some(tool => tool.name === 'run_code')) throw new Error('Toollery v1 requires native tool presentation')
    const snapshot = await post('/snapshot', { session_id: agent.id, schemas, discovery }, signal)
    const state = { frozen: new Map(structuredClone(schemas).map(tool => [tool.name, tool])),
      snapshot, messages: [], turn: null, selection: null, disposers: [] }
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
      if (turn !== state.turn) state.messages = []
      state.turn = turn
      state.messages.push(message)
      state.selection = null
    }))
    // A guard also covers scoped tools, which tools.restrict() deliberately
    // leaves visible. The full registry stays intact for the next prompt.
    state.disposers.push(agent.ctx.tools.guard(execution =>
      state.selection?.selected_names.includes(execution.name)
        ? undefined : 'Toollery: tool is outside the current prompt selection'))

    state.disposers.push(agent.ctx.on('tools/execute', async (execution, next) => {
      const selection = state.selection
      if (!selection) return next()
      const call = { session_id: agent.id, selection_id: selection.selection_id,
        call_id: execution.callId, name: execution.name }
      // Observe real dispatched calls, not the retrieval candidates. Reporting
      // failures must never cause a successfully executed tool to be retried.
      const report = async status => {
        try { await post('/call', { ...call, status }) }
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
      const assembled = await next()
      if (!sameSnapshot(ctx.tools.schemas(agent), state.frozen)
          || !sameSnapshot(assembled.tools, state.frozen)) {
        throw new Error('Toollery: tool pool changed; restart and prepare its new snapshot')
      }
      if (state.selection === null) {
        if (state.messages.length === 0) throw new Error('Toollery: no current user text to retrieve with')
        const prompt = state.messages.flatMap(message => message.content)
          .filter(block => block.type === 'text').map(block => block.text).join('\n')
        state.selection = await post('/select', {
          session_id: agent.id, snapshot_hash: snapshot.snapshot_hash, prompt,
          message_ids: state.messages.map(message => message.id), turn: state.turn,
        }, context.signal)
      }
      const keep = new Set(state.selection.selected_names)
      const tools = assembled.tools.filter(tool => keep.has(tool.name))
      if (tools.length !== keep.size) throw new Error('Toollery: selected tool is absent from the frozen pool')
      await post('/observed', {
        session_id: agent.id, selection_id: state.selection.selection_id,
        turn: state.turn, model_tool_names: tools.map(tool => tool.name),
      }, context.signal)
      // DSH's normal loop persists this exact schema set in request/header.
      return { ...assembled, tools }
    }))
  })
  function dispose(agent) {
    const state = states.get(agent)
    if (!state) return
    states.delete(agent)
    for (const cleanup of state.disposers.reverse()) cleanup()
  }
  ctx.on('agent/disposed', ({ agent }) => dispose(agent))
  ctx.effect(() => () => { for (const agent of [...states.keys()]) dispose(agent) })
}
