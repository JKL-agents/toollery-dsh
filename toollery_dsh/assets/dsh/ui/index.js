// Same-origin, DSH-authenticated UI transport. Bridge credentials stay here.
export const inject = ['webServer', 'connection']
export function apply(ctx) {
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
        const { session_id, options } = JSON.parse(body)
        if (typeof session_id !== 'string' || !session_id) return send(400, { error: 'Session required' })
        if (options !== undefined && (!options || Array.isArray(options)
            || Object.keys(options).some(key => !['enabled', 'representation', 'budget', 'k'].includes(key)))) {
          return send(400, { error: 'Invalid settings' })
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
      } catch (error) { send(409, { error: error.message }) }
    },
  }))
}
