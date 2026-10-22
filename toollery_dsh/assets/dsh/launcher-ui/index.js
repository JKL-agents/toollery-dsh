// Optional launcher-only workspace handoff, behind DSH's normal admission gate.
export const inject = ['webServer', 'connection']
export function apply(ctx) {
  ctx.effect(() => ctx.webServer.register({
    kind: 'exact', path: '/toollery/launcher',
    async handler(req, res) {
      const admission = ctx.connection.admit(req)
      const allowed = !('rejection' in admission) && req.method === 'POST'
      res.writeHead(allowed ? 200 : 403, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
      res.end(JSON.stringify(allowed ? {
        path: process.env.TOOLLERY_LAUNCHER_WORKSPACE,
        preset: process.env.TOOLLERY_LAUNCHER_PRESET,
        launch: process.env.TOOLLERY_LAUNCHER_ID,
      } : { error: 'Access denied' }))
    },
  }))
}
