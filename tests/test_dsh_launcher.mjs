import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import vm from 'node:vm'
import test from 'node:test'
import { apply } from '../toollery_dsh/assets/dsh/launcher-ui/index.js'

test('waiting page shows install progress, failure, retry and authenticated handoff', async () => {
  const code = await readFile(new URL('../toollery_dsh/assets/waiting.js', import.meta.url), 'utf8')
  const elements = new Map(), requests = [], timers = [], redirects = []
  const element = selector => {
    if (!elements.has(selector)) elements.set(selector, {
      textContent: '', hidden: false, attributes: {},
      classList: { toggle() {} },
      setAttribute(name, value) { this.attributes[name] = value },
      removeAttribute(name) { delete this.attributes[name] },
    })
    return elements.get(selector)
  }
  let status = { phase: 'starting', stage: 'installing', message: 'Installing dependencies', elapsed_seconds: 132,
    packages: { resolved: 120, downloaded: 30, reused: 40, added: 70 } }
  const sandbox = {
    URL, URLSearchParams,
    location: { hash: '#token=setup-token', replace: url => redirects.push(url) },
    document: { querySelector: element },
    fetch: async (url, options) => { requests.push([url, options]); return { ok: true, json: async () => status } },
    setTimeout: callback => timers.push(callback),
  }
  vm.runInNewContext(code, sandbox)
  const settle = () => new Promise(resolve => setImmediate(resolve))
  await settle()
  assert.equal(element('#installing').attributes['aria-current'], 'step')
  assert.match(element('#detail').textContent, /30 downloaded/)
  assert.equal(element('#elapsed').textContent, 'Elapsed: 2m 12s')
  assert.equal(requests[0][1].headers['X-Toollery-Token'], 'setup-token')
  status = { phase: 'error', message: 'Web dependency installation failed.' }
  await timers.shift()()
  assert.equal(element('#progress').hidden, true)
  assert.match(element('#recovery').textContent, /Startup failed/)
  status = { phase: 'starting', stage: 'starting', message: 'Starting DSH' }
  await timers.shift()()
  assert.equal(element('#progress').hidden, false)
  assert.equal(element('#recovery').hidden, true)
  status = { phase: 'ready', port: 3080, url: 'https://example.org/?token=untrusted' }
  await timers.shift()()
  assert.equal(redirects.length, 0)
  status = { phase: 'ready', port: 3080, url: 'http://127.0.0.1:3080/?token=dsh-token' }
  await timers.shift()()
  assert.deepEqual(redirects, [status.url])
  assert.equal(timers.length, 0)
})

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
