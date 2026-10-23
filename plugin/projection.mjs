import { isAgentLoopRequest, markAgentLoopRequest } from '@deepseek-ai/dsh-llm'

// DSH requests are immutable and next() cannot replace its argument. Re-enter
// the public stream API with a detached request, before other middleware runs.
// The second entry has no toolHistory and passes through this hook immediately.
// Other stream middleware and the provider execute once, with the same route,
// controls, messages, replay state, session identity and cancellation signal.
export function applyProjection(ctx, service) {
  ctx.on('llm/stream', function (request, next) {
    const state = service.sessions.get(request.sessionId)
    if (!state?.schemas || request.purpose || request.toolHistory === undefined) return next()
    const { toolHistory: _history, ...current } = request
    if (isAgentLoopRequest(request)) markAgentLoopRequest(current)
    return this.stream(Object.freeze(current))
  }, { prepend: true })
}
