import assert from 'node:assert/strict'
import { Readable } from 'node:stream'
import test from 'node:test'
import { apply } from '../toollery_dsh/assets/dsh/ui/index.js'

function fixture(admission = { peer: {} }) {
  let handler
  const ctx = {
    effect: fn => fn(), connection: { admit: () => admission },
    webServer: { register: route => { handler = route.handler; return () => {} } },
  }
  apply(ctx)
  return async (body, method = 'POST') => {
    const req = Readable.from([JSON.stringify(body)])
    req.method = method
    const res = { writeHead(code) { this.code = code }, end(body) { this.body = JSON.parse(body) } }
    await handler(req, res)
    return res
  }
}
test('UI route rejects unauthenticated requests and unsupported settings', async () => {
  assert.equal((await fixture({ rejection: 401 })({ session_id: 's' })).code, 401)
  const send = fixture()
  assert.equal((await send({}, 'GET')).code, 405)
  assert.equal((await send({})).code, 400)
  assert.equal((await send({ session_id: 's', options: { endpoint: 'not-allowed' } })).code, 400)
})
test('UI route forwards only allowed operations and keeps bridge authorization host-side', async () => {
  const original = globalThis.fetch
  const previousUrl = process.env.TOOLLERY_BRIDGE_URL
  const previousToken = process.env.TOOLLERY_BRIDGE_TOKEN
  const requests = []
  process.env.TOOLLERY_BRIDGE_URL = 'http://127.0.0.1:1234'
  process.env.TOOLLERY_BRIDGE_TOKEN = 'test-only-token'
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options })
    return new Response(JSON.stringify({ enabled: false, tool_count: 12 }))
  }
  try {
    const res = await fixture()({ session_id: 's', options: { enabled: false } })
    assert.equal(res.code, 200)
    assert.deepEqual(requests.map(row => row.url), ['http://127.0.0.1:1234/configure', 'http://127.0.0.1:1234/status'])
    assert.equal(requests[0].options.headers.Authorization, 'Bearer test-only-token')
    assert.equal(JSON.stringify(res.body).includes('test-only-token'), false)
  } finally {
    globalThis.fetch = original
    if (previousUrl === undefined) delete process.env.TOOLLERY_BRIDGE_URL
    else process.env.TOOLLERY_BRIDGE_URL = previousUrl
    if (previousToken === undefined) delete process.env.TOOLLERY_BRIDGE_TOKEN
    else process.env.TOOLLERY_BRIDGE_TOKEN = previousToken
  }
})
