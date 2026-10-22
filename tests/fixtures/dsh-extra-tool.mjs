export const name = 'toollery-discovery-fixture'
export const inject = ['tools']
export function apply(ctx) {
  ctx.tools.register({
    name: 'fixture_echo', description: 'Echo a local test message.',
    parameters: { type: 'object', properties: { text: { type: 'string' } }, required: ['text'] },
    output: { schema: { type: 'string' }, render: (_args, text) => [{ type: 'text', text }] },
    async execute({ text }) { return text },
  })
}
