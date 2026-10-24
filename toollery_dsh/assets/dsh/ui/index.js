// Same-origin, DSH-authenticated UI transport. Bridge credentials stay here.
export const inject = ['webServer', 'connection']
export function apply(ctx, config = {}) {
  const managed = config.managed ? (config.service || ctx.get('toollery')) : null
  ctx.effect(() => ctx.webServer.register({
    kind: 'exact', path: '/toollery/ui',
    async handler(req, res) {
      const send = (code, value) => { res.writeHead(code, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }); res.end(JSON.stringify(value)) }
      const admission = ctx.connection.admit(req)
      if ('rejection' in admission) return send(admission.rejection, { error: 'Access denied' })
      if (req.method !== 'POST') return send(405, { error: 'POST required' })
      try {
        let body = ''
        for await (const chunk of req) {
          body += chunk
          if (body.length > 8192) return send(413, { error: 'Request too large' })
        }
        const { session_id, options, action, setup_options, generator_settings } = JSON.parse(body)
        if ((session_id !== undefined && (typeof session_id !== 'string' || !session_id))
            || (!managed && !session_id) || ((options !== undefined || action === 'prepare') && !session_id)) {
          return send(400, { error: 'Session required' })
        }
        if (options !== undefined && (!options || Array.isArray(options)
            || Object.keys(options).some(key => !['enabled', 'representation', 'budget', 'k', 'retrieval_target'].includes(key)))) {
          return send(400, { error: 'Invalid settings' })
        }
        if (action !== undefined && (!managed || !['setup', 'prepare', 'save_generator'].includes(action) || options !== undefined)) {
          return send(400, { error: 'Invalid action' })
        }
        if (setup_options !== undefined && (action !== 'setup' || !setup_options || Array.isArray(setup_options)
            || Object.keys(setup_options).some(key => key !== 'allow_download')
            || typeof setup_options.allow_download !== 'boolean')) return send(400, { error: 'Invalid setup options' })
        if (generator_settings !== undefined && action !== 'save_generator') return send(400, { error: 'Invalid generator action' })
        if (managed) {
          if (action === 'setup') void managed.setup(setup_options)
          if (action === 'save_generator') await managed.saveGenerator(generator_settings)
          if (action === 'prepare') await managed.prepare(session_id)
          if (options !== undefined) await managed.configure(session_id, options)
          return send(200, await managed.status(session_id))
        }
        async function post(path, payload) {
          const response = await fetch(process.env.TOOLLERY_BRIDGE_URL + path, {
            method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + process.env.TOOLLERY_BRIDGE_TOKEN },
            body: JSON.stringify(payload), signal: AbortSignal.timeout(path === '/configure' ? 1800000 : 15000),
          })
          if (!response.ok) throw new Error('Session unavailable or settings could not be applied; current settings are preserved.')
          return response.json()
        }
        if (options !== undefined) await post('/configure', { session_id, options })
        send(200, await post('/status', { session_id }))
      } catch (error) { send(409, { error: error instanceof SyntaxError || error instanceof TypeError ? 'Invalid request.' : error.message }) }
    },
  }))
}
