import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import vm from 'node:vm'
import test from 'node:test'
import { apply } from '../toollery_dsh/assets/dsh/launcher-ui/index.js'

test('launcher handoff requires DSH admission and POST', async () => {
  for (const [admission, method, expected] of [[{ rejection: 401 }, 'POST', 403], [{}, 'GET', 403], [{}, 'POST', 200]]) {
    let handler
    apply({ effect: fn => fn(), connection: { admit: () => admission },
      webServer: { register: route => { handler = route.handler } } })
    const res = { writeHead(code) { this.code = code }, end(body) { this.body = JSON.parse(body) } }
    await handler({ method }, res)
    assert.equal(res.code, expected)
    assert.equal(JSON.stringify(res.body).includes('API_KEY'), false)
  }
})

test('workspace auto-entry runs once per launch and preserves refresh state', async () => {
  const code = await readFile(new URL('../toollery_dsh/assets/dsh/launcher-ui/client.js', import.meta.url), 'utf8')
  const calls = [], storage = new Map()
  let plugin, launch = 'launch-one'
  const sandbox = {
    window: { __ModuleLoader__: { load: value => { plugin = value.factory() } } },
    fetch: async () => ({ ok: true, json: async () => ({ path: '/demo playground', preset: 'test-preset', launch }) }),
    sessionStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value) },
    console,
  }
  vm.runInNewContext(code, sandbox)
  const ctx = {
    workspaces: { create: async ({ path }) => { calls.push(path); return { workspaceId: 'workspace' } } },
    uiWorkspace: { openWorkspace: async (id, unused, options) => { calls.push([id, options.agentPreset]) } },
  }
  const settle = () => new Promise(resolve => setImmediate(resolve))
  plugin.apply(ctx); await settle()
  assert.deepEqual(calls, ['/demo playground', ['workspace', 'test-preset']])
  plugin.apply(ctx); await settle()
  assert.equal(calls.length, 2)
  launch = 'launch-two'
  plugin.apply(ctx); await settle()
  assert.equal(calls.length, 4)
})
