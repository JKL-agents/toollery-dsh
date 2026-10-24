import { join, resolve } from 'node:path'
import { ToolleryService } from './service.mjs'
import { apply as applyFilter } from './shared/toollery.mjs'
import { apply as applyUI } from './ui/index.js'
import { applyProjection } from './projection.mjs'
import { registerSampleSkills } from './sample-skill-provider.mjs'

export const name = 'toollery-plugin'
export const inject = ['profileContext', 'tools', 'agents', 'systemPrompt', 'llm']

export function apply(ctx, config = {}) {
  const directory = config.dataDirectory ? resolve(ctx.profileContext.dir, config.dataDirectory)
    : join(ctx.profileContext.dir, 'toollery')
  const service = new ToolleryService(directory, config)
  ctx.provide('toollery', service)
  ctx.effect(() => () => service.dispose())
  applyFilter(ctx, { managed: true, service, residentToolNames: config.residentToolNames })
  applyProjection(ctx, service)
  // A self-contained sample catalog makes the bundled skill retrieval demo
  // usable on a fresh DSH profile. Names are prefixed to avoid user collisions.
  ctx.inject(['skills'], inner => registerSampleSkills(inner))
  ctx.inject(['webServer', 'connection'], inner => applyUI(inner, { managed: true, service }))
  ctx.inject(['commands'], inner => {
    inner.commands.register({
      name: 'toollery', description: 'Toollery setup, preparation and next-turn retrieval settings',
      input: { hint: 'setup | prepare | status | on | off | mode spec|manual|spec_manual | target tools|skills|both | budget all|top_k | k N' },
      async handler({ agent, rawInput }) {
        const [command = 'status', value, ...extra] = rawInput.trim().split(/\s+/).filter(Boolean)
        try {
          if (extra.length) throw new Error('Provide one value.')
          if (command === 'setup' && !value) { void service.setup(); return { kind: 'success', text: 'Worker setup started. Use /toollery status for progress; no model calls are made.' } }
          else if (command === 'prepare' && !value) await service.prepare(agent.id)
          else if (['on', 'off'].includes(command) && !value) await service.configure(agent.id, { enabled: command === 'on' })
          else if (['mode', 'target', 'budget', 'k'].includes(command) && value) await service.configure(agent.id, { [{ mode: 'representation', target: 'retrieval_target', budget: 'budget', k: 'k' }[command]]: command === 'k' ? Number(value) : value })
          else if (command !== 'status' || value) throw new Error('Use /toollery setup | prepare | status | on | off | mode VALUE | target tools|skills|both | budget VALUE | k N.')
          const status = await service.status(agent.id)
          return { kind: 'success', text: `Toollery ${status.enabled ? 'ON' : 'OFF'}; ${status.phase}; ${status.manuals_valid}/${status.tool_count} manuals; ${status.representation}, ${status.budget}, k=${status.k}, target=${status.retrieval_target || 'tools'}.\nConfiguration: ${directory}${status.error ? '\n' + status.error : ''}` }
        } catch (error) { return { kind: 'error', text: error.message } }
      },
    })
  })
  // Start the private worker when it is already installed. If the profile has
  // no matching worker yet, perform the safe local setup automatically: this
  // installs only the embedded wheel using DSH's bundled/existing Python. It
  // never downloads a runtime or calls a model; those require explicit UI
  // consent. Set autoSetup: false for hosts that want fully manual setup.
  void service.start().then(() => {
    if (config.autoSetup !== false && service.phase === 'needs_setup') {
      return service.setup({ allow_download: false })
    }
  }).catch(error => {
    // A plugin can be unloaded while startup/automatic setup is in flight.
    // Keep that lifecycle race from becoming an unhandled rejection.
    if (!service.disposed) {
      service.phase = 'needs_setup'
      service.message = error?.message || 'Automatic worker setup failed. Run setup again.'
    }
  })
}
