// Offline acceptance fixture: an unrelated host policy and stream observer.
import { createServer } from 'node:http'
import { writeFileSync } from 'node:fs'

export const name = 'toollery-host-fixture'
export const inject = ['tools', 'agents', 'systemPrompt', 'llm']
export function apply(ctx, config) {
  const streams = []
  let dynamicRegistered = false
  ctx.on('llm/stream', (request, next) => {
    streams.push({ sessionId: request.sessionId, tools: request.tools?.map(tool => tool.name) })
    return next()
  })
  ctx.on('agent/created', ({ agent }) => {
    agent.ctx.systemPrompt.section({ name: 'host-fixture', order: 100, text: 'Host policy fixture remains active.', interpolate: false })
    agent.ctx.on('system-prompt/assemble', async (_assembly, _context, next) => {
      const assembled = await next()
      return { ...assembled, tools: assembled.tools.filter(tool => tool.name !== 'multiply_numbers') }
    })
  })
  ctx.inject(['toollery'], inner => {
    const service = inner.get('toollery')
    const server = createServer(async (req, res) => {
      try {
        let input = ''
        for await (const part of req) input += part
        const { action, id, options } = JSON.parse(input)
        let result
        if (action === 'setup') { await service.setup(); result = { phase: service.phase, message: service.message } }
        else if (action === 'prepare') result = await service.prepare(id)
        else if (action === 'configure') { await service.configure(id, options); result = await service.status(id) }
        else if (action === 'status') result = await service.status(id)
        else if (action === 'streams') result = streams
        else if (action === 'add-dynamic-tool') {
          if (!dynamicRegistered) {
            ctx.tools.register({
              name: 'uppercase_text',
              description: 'Convert text to uppercase.',
              parameters: { type: 'object', properties: { text: { type: 'string' } }, required: ['text'], additionalProperties: false },
              output: { schema: { type: 'string' }, render: (_args, value) => [{ type: 'text', text: value }] },
              async execute(args, execution) {
                execution.signal.throwIfAborted()
                return String(args.text).toUpperCase()
              },
            })
            dynamicRegistered = true
          }
          result = { registered: dynamicRegistered }
        }
        else throw new Error('Unknown fixture action')
        res.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify(result))
      } catch (error) { res.writeHead(500).end(JSON.stringify({ error: error.message })) }
    })
    server.listen(0, '127.0.0.1', () => writeFileSync(config.addressFile, String(server.address().port)))
    inner.effect(() => () => new Promise(done => server.close(done)))
  })
}
