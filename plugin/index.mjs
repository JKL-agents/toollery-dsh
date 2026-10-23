import { join, resolve } from 'node:path'
import { ToolleryService } from './service.mjs'
import { apply as applyFilter } from './shared/toollery.mjs'
import { apply as applyUI } from './ui/index.js'
import { applyProjection } from './projection.mjs'

export const name = 'toollery-plugin'
export const inject = ['profileContext', 'tools', 'agents', 'systemPrompt', 'llm']

export function apply(ctx, config = {}) {
  const directory = config.dataDirectory ? resolve(ctx.profileContext.dir, config.dataDirectory)
    : join(ctx.profileContext.dir, 'toollery')
  const service = new ToolleryService(directory, config)
  ctx.provide('toollery', service)
  ctx.effect(() => () => service.dispose())
  applyFilter(ctx, { managed: true, service })
  applyProjection(ctx, service)
  ctx.inject(['webServer', 'connection'], inner => applyUI(inner, { managed: true, service }))
  ctx.inject(['commands'], inner => {
    inner.commands.register({
      name: 'toollery', description: 'Toollery setup, preparation and next-turn retrieval settings',
      input: { hint: 'setup | prepare | status | on | off | mode spec|manual|spec_manual | budget all|top_k | k N' },
      async handler({ agent, rawInput }) {
        const [command = 'status', value, ...extra] = rawInput.trim().split(/\s+/).filter(Boolean)
        try {
          if (extra.length) throw new Error('Provide one value.')
          if (command === 'setup' && !value) { void service.setup(); return { kind: 'success', text: 'Worker setup started. Use /toollery status for progress; no model calls are made.' } }
          else if (command === 'prepare' && !value) await service.prepare(agent.id)
          else if (['on', 'off'].includes(command) && !value) await service.configure(agent.id, { enabled: command === 'on' })
          else if (['mode', 'budget', 'k'].includes(command) && value) await service.configure(agent.id, { [{ mode: 'representation', budget: 'budget', k: 'k' }[command]]: command === 'k' ? Number(value) : value })
          else if (command !== 'status' || value) throw new Error('Use /toollery setup | prepare | status | on | off | mode VALUE | budget VALUE | k N.')
          const status = await service.status(agent.id)
          return { kind: 'success', text: `Toollery ${status.enabled ? 'ON' : 'OFF'}; ${status.phase}; ${status.manuals_valid}/${status.tool_count} manuals; ${status.representation}, ${status.budget}, k=${status.k}.\nConfiguration: ${directory}${status.error ? '\n' + status.error : ''}` }
        } catch (error) { return { kind: 'error', text: error.message } }
      },
    })
  })
  // A failed/missing worker leaves the plugin installed and visibly OFF.
  // Existing DSH chat, providers and other plugins remain available.
  void service.start()
}
