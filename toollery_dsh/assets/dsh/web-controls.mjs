// Official slash-command surface: status is UI output, never model input.
export const name = 'toollery-web-controls'
export const inject = ['commands']

export function apply(ctx) {
  async function post(path, payload) {
    const response = await fetch(process.env.TOOLLERY_BRIDGE_URL + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + process.env.TOOLLERY_BRIDGE_TOKEN },
      body: JSON.stringify(payload), signal: AbortSignal.timeout(path === '/configure' ? 1800000 : 15000),
    })
    if (!response.ok) throw new Error(`Toollery request failed (${response.status})`)
    return response.json()
  }
  ctx.commands.register({
    name: 'toollery', description: 'Toollery on/off, status, tools, and next-turn retrieval settings',
    input: { hint: 'on | off | status | tools | mode spec|manual|spec_manual | budget all|top_k | k N' },
    async handler({ agent, rawInput }) {
      const [command = 'status', value, ...extra] = rawInput.trim().split(/\s+/).filter(Boolean)
      const session_id = agent.id
      let status
      if (['on', 'off'].includes(command) && !value) {
        try {
          status = await post('/configure', { session_id, options: { enabled: command === 'on' } })
        } catch {
          return { kind: 'error', text: 'Could not change Toollery state. ON requires ready manuals and indexes; check the launcher output. Existing setting was preserved.' }
        }
      } else if (['mode', 'budget', 'k'].includes(command)) {
        if (!value || extra.length) return { kind: 'error', text: 'Provide one value. Example: /toollery k 3' }
        const field = { mode: 'representation', budget: 'budget', k: 'k' }[command]
        try {
          status = await post('/configure', { session_id, options: { [field]: command === 'k' ? Number(value) : value } })
        } catch {
          return { kind: 'error', text: 'Invalid setting or session not ready. Mode: spec|manual|spec_manual; budget: all|top_k; k: positive integer.' }
        }
      } else if (['status', 'tools'].includes(command) && !value) {
        status = await post('/status', { session_id })
      } else {
        return { kind: 'error', text: 'Usage: /toollery on | off | status | tools | mode spec|manual|spec_manual | budget all|top_k | k N' }
      }
      const last = status.last_selection
      return { kind: 'success', text: [
        `Toollery ${status.enabled ? 'ON' : 'OFF'} (${status.ready ? 'ready' : 'not ready'}) — ${status.tool_count} tools; ${status.manuals_valid} valid manuals.`,
        status.enabled ? `Next turn: ${status.representation}, ${status.budget}, k=${status.k}.`
          : `Next turn: full original pool (${status.tool_count} tools), retrieval bypassed. Saved settings: ${status.representation}, ${status.budget}, k=${status.k}.`,
        last ? `Last turn ${last.turn} (${last.enabled ? 'ON' : 'OFF'}): ${last.selected_names.join(', ') || '(none)'} (${last.retrieval_ms.toFixed(3)} ms).` : 'No turn yet.',
        ...(command === 'tools' ? ['Full pool: ' + status.tool_names.join(', ')] : []),
      ].join('\n') }
    },
  })
}
