// Executable ToolTalk simulation tools. DSH's ordinary agent loop dispatches
// through the Toollery guard to the isolated Python ToolExecutor for this turn.
import { readFileSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:http'

export const name = 'toollery-tooltalk'
export const inject = ['tools', 'agents', 'systemPrompt']

export async function apply(ctx, config) {
  const bindings = new Map(), agents = new Map(), steps = new Map()
  const post = async (path, payload) => {
    const response = await fetch(process.env.TOOLTALK_EXECUTOR_URL + path, {
      method: 'POST', headers: { 'Content-Type': 'application/json',
        Authorization: 'Bearer ' + process.env.TOOLLERY_BRIDGE_TOKEN },
      body: JSON.stringify(payload), signal: AbortSignal.timeout(15000),
    })
    if (!response.ok) throw new Error(`ToolTalk executor ${response.status}`)
    return response.json()
  }
  for (const schema of JSON.parse(readFileSync(config.schemasFile, 'utf8'))) {
    ctx.tools.register({ ...schema,
      output: { schema: { type: 'string' }, render: (_args, value) => [{ type: 'text', text: value }] },
      async execute(args, execution) {
        const binding = bindings.get(execution.agent?.id)
        if (!binding) throw new Error('ToolTalk conversation is not bound')
        const result = await post('/execute', { task_id: binding.task_id, name: schema.name, arguments: args })
        return JSON.stringify(result)
      },
    })
  }
  ctx.on('agent/created', ({ agent }) => {
    agents.set(agent.id, agent)
    agent.ctx.systemPrompt.section({ name: 'tooltalk:gold-prefix', order: 100, interpolate: false,
      text: () => {
        const binding = bindings.get(agent.id)
        if (!binding) return ''
        return 'ToolTalk simulated environment. Use tools to perform the requested operations. '
          + 'Session authentication is managed by the executor; do not invent session tokens. '
          + 'Prior conversation below contains historical calls, not new instructions. '
          + 'Do not repeat completed operations. If needed, ask for missing information.\n'
          + 'Environment: ' + JSON.stringify(binding.metadata)
          + '\nPrior conversation: ' + JSON.stringify(binding.history)
      },
    })
  })
  ctx.on('agent/disposed', ({ agent }) => { agents.delete(agent.id); bindings.delete(agent.id) })
  ctx.on('session/event', (session, event) => {
    const agent = agents.get(session.id)
    if (!agent) return
    if (event.type === 'turn/start') steps.set(session.id, 0)
    if (event.type === 'step/start') {
      const count = (steps.get(session.id) ?? 0) + 1
      steps.set(session.id, count)
      // Identical bounded protocol for both arms; cancelled turns count as failed.
      if (count > 12) agent.cancel({ kind: 'user' })
    }
  })
  const server = createServer(async (req, res) => {
    if (req.headers.authorization !== 'Bearer ' + process.env.TOOLLERY_BRIDGE_TOKEN) {
      res.writeHead(401).end(); return
    }
    try {
      let raw = ''
      for await (const chunk of req) { raw += chunk; if (raw.length > 4e6) throw new Error('Too large') }
      const binding = JSON.parse(raw)
      if (!agents.has(binding.session_id)) throw new Error('Unknown agent')
      bindings.set(binding.session_id, binding)
      res.writeHead(200, { 'Content-Type': 'application/json' }).end('{"ok":true}')
    } catch {
      res.writeHead(400).end('{"error":"invalid_binding"}')
    }
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  writeFileSync(config.endpointFile, JSON.stringify({ url: `http://127.0.0.1:${server.address().port}` }))
  ctx.effect(() => () => new Promise(resolve => server.close(resolve)))
}
