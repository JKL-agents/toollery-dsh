import assert from 'node:assert/strict'
import test from 'node:test'
import { apply } from '../toollery_dsh/assets/dsh/toollery.mjs'
import { ToolleryService } from '../plugin/service.mjs'

const schema = name => ({ name, description: name, parameters: { type: 'object' } })
const deferred = () => {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}
const tick = () => new Promise(done => setImmediate(done))

async function fixture() {
  const handlers = {}, agentHandlers = {}, calls = { settings: 0, refresh: 0, select: [] }
  const registry = [schema('alpha'), schema('beta'), schema('denied')]
  const options = { enabled: true, representation: 'spec_manual', budget: 'top_k', k: 1 }
  const service = {
    async register() { return {} }, enabled: () => options.enabled,
    async settings() { calls.settings++; return { ...options } },
    async reconcile(_id, source) { calls.refresh++; return { schemas: await source() } },
    snapshotHash: () => 'hash', poolRevision: () => 1,
    bypass(_id, tools, turn, settings) {
      return { ...settings, turn, selection_id: `off-${turn}`, selected_names: tools.map(tool => tool.name) }
    },
    async post(path, payload) {
      if (path !== '/select') return {}
      calls.select.push(payload)
      return { enabled: true, turn: payload.turn, selection_id: `selection-${payload.turn}`,
        selected_names: [payload.prompt], ...payload.options }
    }, unregister() {},
  }
  const agent = { id: 'native', ctx: {
    on(name, fn) { agentHandlers[name] = fn; return () => {} },
    tools: { guard: () => () => {} },
    systemPrompt: { section: () => () => {}, context: () => () => {} },
  } }
  const ctx = { on(name, fn) { handlers[name] = fn }, effect() {},
    agents: { roots: () => [agent] }, tools: { schemas: () => registry } }
  apply(ctx, { managed: true, service })
  await handlers['agent/created']({ agent })
  const claim = (turn, text) => agentHandlers['agent/inbox/claimed']({ turn,
    message: { id: `message-${turn}`, source: { kind: 'user' }, content: [{ type: 'text', text }] } })
  const assemble = (tools = registry.filter(tool => tool.name !== 'denied')) =>
    agentHandlers['system-prompt/assemble']({}, {}, async () => ({ tools, prompt: 'host' }))
  const preStep = (messages, gate) => agentHandlers['agent/pre-step']({ signal: new AbortController().signal },
    async () => { if (gate) await gate; return { kind: 'enter', messages } })
  return { calls, registry, options, service, claim, assemble, preStep }
}

test('Skills and Both rank only the native eligible catalog while preserving tools', async () => {
  const f = await fixture()
  f.registry.push(schema('skill'))
  const calls = []
  f.service.rankSkills = async (_id, prompt, candidates, options) => {
    calls.push({ prompt, candidates, options })
    return { selected_names: [prompt.includes('first') ? 'alpha-skill' : 'beta-skill'],
      candidate_count: candidates.length, retrieval_ms: 1 }
  }
  const catalog = () => ({ content: [{ type: 'text', text: 'native catalog' }],
    source: { kind: 'skill-catalog', entries: [
      { name: 'alpha-skill', description: 'Alpha instructions' },
      { name: 'beta-skill', description: 'Beta instructions' },
    ] } })
  const eligible = f.registry.filter(tool => tool.name !== 'denied')
  f.options.retrieval_target = 'skills'
  f.claim(1, 'first alpha')
  assert.deepEqual((await f.assemble(eligible)).tools.map(tool => tool.name), ['alpha', 'beta', 'skill'])
  const first = await f.preStep([catalog()])
  assert.deepEqual(first.messages[0].source.entries.map(entry => entry.name), ['alpha-skill'])
  assert.deepEqual(calls[0].candidates.map(candidate => candidate.name), ['alpha-skill', 'beta-skill'])
  assert.equal(f.calls.refresh, 0, 'Skills mode must not refresh the tool index')
  f.options.k = 2
  const sameTurn = await f.preStep([catalog()])
  assert.deepEqual(sameTurn.messages[0].source.entries.map(entry => entry.name), ['alpha-skill'])
  assert.equal(calls.length, 1, 'Later model steps reuse the frozen skill selection')

  f.claim(2, 'second beta')
  f.options.retrieval_target = 'both'
  await f.assemble(eligible)
  const second = await f.preStep([catalog()])
  assert.deepEqual(second.messages[0].source.entries.map(entry => entry.name), ['beta-skill'])
  assert.equal(calls.length, 2)
  assert.equal(f.calls.refresh, 1, 'Both mode still reconciles the tool pool')

  f.claim(3, 'first alpha')
  f.options.enabled = false
  assert.deepEqual((await f.assemble(eligible)).tools.map(tool => tool.name), ['alpha', 'beta', 'skill'])
  const off = await f.preStep([catalog()])
  assert.deepEqual(off.messages[0].source.entries.map(entry => entry.name), ['alpha-skill', 'beta-skill'])
  assert.equal(calls.length, 2, 'OFF leaves the native skill catalog untouched')
})

test('a delayed old skill pre-step cannot publish into the newer turn', async () => {
  const f = await fixture(), gate = deferred()
  f.registry.push(schema('skill'))
  f.options.retrieval_target = 'skills'
  let ranks = 0
  f.service.rankSkills = async () => { ranks++; return { selected_names: ['alpha-skill'] } }
  const catalog = { content: [{ type: 'text', text: 'native catalog' }],
    source: { kind: 'skill-catalog', entries: [{ name: 'alpha-skill', description: 'Alpha instructions' }] } }
  f.claim(1, 'first')
  await f.assemble(f.registry)
  const old = assert.rejects(f.preStep([catalog], gate.promise), /superseded/)
  f.claim(2, 'second')
  await f.assemble(f.registry)
  const current = await f.preStep([catalog])
  gate.resolve()
  await old
  assert.equal(ranks, 1)
  assert.equal(current.messages[0].source.entries[0].name, 'alpha-skill')
})

test('bypass counts the current host pool without refreshing the tool index', () => {
  const service = new ToolleryService('.')
  service.sessions.set('s', { schemas: [schema('old')], localTurns: [] })
  const record = service.bypass('s', [schema('old'), schema('new')], 1,
    { enabled: true, retrieval_target: 'skills' }, [schema('old'), schema('new'), schema('restricted')])
  assert.equal(record.full_pool_count, 3)
  assert.equal(record.eligible_pool_count, 2)
  assert.deepEqual(record.selected_names, ['old', 'new'])
  assert.equal(service.sessions.get('s').schemas.length, 1, 'Bypass must not replace the worker snapshot')
})

test('same-turn assemblies share settings, refresh and retrieval and retain frozen options', async () => {
  const f = await fixture(), gate = deferred()
  const post = f.service.post
  f.service.post = async (...args) => {
    if (args[0] === '/select') await gate.promise
    return post(...args)
  }
  f.claim(1, 'alpha')
  const a = f.assemble(), b = f.assemble()
  await tick()
  f.options.k = 2
  f.options.enabled = false
  gate.resolve()
  assert.deepEqual((await Promise.all([a, b])).map(result => result.tools.map(tool => tool.name)), [['alpha'], ['alpha']])
  assert.equal(f.calls.settings, 1)
  assert.equal(f.calls.refresh, 1)
  assert.equal(f.calls.select.length, 1)
  assert.equal(f.calls.select[0].options.k, 1)
  assert.equal(f.calls.select[0].options.enabled, true)
  assert.deepEqual(f.calls.select[0].eligible_names, ['alpha', 'beta'])
  f.registry.push(schema('new-tool'))
  assert.deepEqual((await f.assemble()).tools.map(tool => tool.name), ['alpha'])
  assert.equal(f.calls.refresh, 1, 'A later model step must not refresh the same turn')
  assert.deepEqual((await f.assemble([schema('beta')])).tools, [], 'An unloaded selected tool must not be resurrected')
})

test('a delayed old turn cannot replace or clear the newer turn selection', async () => {
  const f = await fixture(), gate = deferred(), entered = deferred()
  const post = f.service.post
  f.service.post = async (...args) => {
    if (args[0] === '/select' && args[1].turn === 1) { entered.resolve(); await gate.promise }
    return post(...args)
  }
  f.claim(1, 'alpha')
  const first = assert.rejects(f.assemble(), /superseded/)
  await entered.promise
  f.claim(2, 'beta')
  assert.deepEqual((await f.assemble()).tools.map(tool => tool.name), ['beta'])
  gate.resolve()
  await first
  assert.deepEqual((await f.assemble()).tools.map(tool => tool.name), ['beta'])
  assert.equal(f.calls.select.length, 2)
})

test('OFF freezes the host-eligible set and skips refresh even with no worker', async () => {
  const f = await fixture()
  f.options.enabled = false
  f.service.post = async () => { throw new Error('Worker is unavailable') }
  f.claim(1, 'alpha')
  assert.deepEqual((await f.assemble()).tools.map(tool => tool.name), ['alpha', 'beta'])
  f.registry.push(schema('new-tool'))
  f.options.enabled = true
  assert.deepEqual((await f.assemble()).tools.map(tool => tool.name), ['alpha', 'beta'])
  assert.equal(f.calls.settings, 1)
  assert.equal(f.calls.refresh, 0)
})

test('refresh wait timeout retains job progress and does not claim cancellation', async () => {
  const service = new ToolleryService('.')
  assert.equal(service.refreshTimeoutMs, 120000)
  service.sessions.set('s', { enabled: true, retrieval_ready: true, snapshot_hash: 'old', pool_revision: 0 })
  const now = Date.now
  let clock = 0
  Date.now = () => clock
  service.post = async path => {
    if (path === '/refresh') return { status: 'accepted', job_id: 'job', snapshot_hash: 'new' }
    clock = 2000
    return { phase: 'refreshing', completed: 1, total: 3, elapsed_seconds: 5, eta_seconds: 10 }
  }
  try {
    await assert.rejects(service.reconcile('s', [schema('alpha')], undefined, { timeoutMs: 1000 }), error => {
      assert.equal(error.code, 'refresh_pending')
      assert.equal(error.detail.job_id, 'job')
      assert.equal(error.detail.generation_cancelled, false)
      assert.equal(error.detail.eta_seconds, 10)
      assert.equal(error.detail.elapsed_seconds, 5)
      return true
    })
  } finally { Date.now = now }
})

test('Node rechecks the host pool after commit with at most one retry', async () => {
  const service = new ToolleryService('.')
  service.sessions.set('s', { enabled: true, retrieval_ready: true, snapshot_hash: 'old', pool_revision: 0 })
  let reads = 0, refreshes = 0
  service.post = async () => ({ status: 'committed', snapshot_hash: `hash-${++refreshes}`, pool_revision: refreshes })
  const source = () => [schema(`tool-${++reads}`)]
  await assert.rejects(service.reconcile('s', source), { code: 'unstable_tool_pool' })
  assert.equal(refreshes, 2)
  assert.equal(reads, 4)
})
