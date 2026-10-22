// Discovery is a readiness barrier, not a best-effort list of optional plugins.
import { createRequire } from 'node:module'
import { realpathSync } from 'node:fs'
import { pathToFileURL } from 'node:url'

function credentials(references = {}) {
  return Object.fromEntries(Object.entries(references).map(([key, variable]) => {
    const value = process.env[variable]
    if (!value) throw new Error(`Toollery: missing environment variable ${variable}`)
    return [key, value]
  }))
}

export function createSourceBarrier(ctx, sources, loadMcp = async () => {
  const anchor = process.env.TOOLLERY_DSH_RUNTIME_PACKAGE
  if (!anchor) throw new Error('Toollery: configured sources require the pinned DSH runtime')
  const require = createRequire(realpathSync(anchor))
  return import(pathToFileURL(require.resolve('@deepseek-ai/dsh-mcp-client')).href)
}) {
  let readiness
  return () => readiness ??= (async () => {
    const inventory = []
    for (const source of sources) {
      try {
        if (source.type === 'mcp') {
          const plugin = await loadMcp()
          const config = { ...source.config, failOnStartupError: true, reconnect: { enabled: false } }
          if (config.transport === 'stdio') config.env = credentials(source.env_from)
          else config.headers = credentials(source.headers_from)
          await ctx.plugin(plugin, plugin.Config(config))
        } else if (source.type === 'plugin') {
          const plugin = await import(source.module)
          await ctx.plugin(plugin, plugin.Config ? plugin.Config(source.config) : source.config)
        } else throw new Error('Unsupported tool-source type')
        inventory.push({ id: source.id, type: source.type, status: 'ready' })
      } catch (cause) {
        // Sticky rejection: later sessions cannot mistake the sources that did
        // succeed for the complete pool. Restart after correcting configuration.
        throw new Error(`Toollery: source ${source.id} failed; full pool is unavailable; fix configuration and restart`, { cause })
      }
    }
    return { complete: true, sources: inventory }
  })()
}
