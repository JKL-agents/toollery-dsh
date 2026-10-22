import assert from 'node:assert/strict'
import test from 'node:test'
import { createSourceBarrier } from '../toollery_dsh/assets/dsh/sources.mjs'
import { apply } from '../toollery_dsh/assets/dsh/toollery.mjs'

const mcp = { Config: config => config }
const source = id => ({ id, type: 'mcp', config: { transport: 'stdio', serverName: id } })

test('discovery awaits every configured source, shared by concurrent sessions', async () => {
  const started = []
  let release
  const delayed = new Promise(resolve => { release = resolve })
  const barrier = createSourceBarrier({ async plugin(_plugin, config) {
    started.push(config.serverName)
    assert.equal(config.failOnStartupError, true)
    assert.equal(config.reconnect.enabled, false)
    if (config.serverName === 'first') await delayed
  } }, [source('first'), source('second')], async () => mcp)
  const first = barrier()
  assert.equal(first, barrier())
  await new Promise(resolve => setImmediate(resolve))
  assert.deepEqual(started, ['first'])
  release()
  const result = await first
  assert.equal(result.complete, true)
  assert.deepEqual(started, ['first', 'second'])
  assert.equal(result.sources.length, 2)
})

test('failed later source stays failed and cannot publish a partial pool', async () => {
  let attempts = 0
  const barrier = createSourceBarrier({ async plugin(_plugin, config) {
    attempts++
    if (config.serverName === 'bad') throw new Error('connection failed')
  } }, [source('good'), source('bad')], async () => mcp)
  await assert.rejects(barrier(), /source bad failed; full pool is unavailable/)
  await assert.rejects(barrier(), /source bad failed/)
  assert.equal(attempts, 2)
})

test('source failure aborts agent creation before registry snapshot or bridge call', async () => {
  const previous = { url: process.env.TOOLLERY_BRIDGE_URL, token: process.env.TOOLLERY_BRIDGE_TOKEN }
  process.env.TOOLLERY_BRIDGE_URL = 'http://127.0.0.1:1234'
  process.env.TOOLLERY_BRIDGE_TOKEN = 'test'
  const handlers = {}
  let snapshots = 0
  try {
    apply({
      on(name, fn) { handlers[name] = fn }, effect() {},
      tools: { schemas() { snapshots++; return [] } },
    }, { toolSources: [{ id: 'bad', type: 'unsupported' }] })
    await assert.rejects(handlers['agent/created']({ agent: { id: 'test' } }), /source bad failed/)
    assert.equal(snapshots, 0)
  } finally {
    for (const [name, value] of [['TOOLLERY_BRIDGE_URL', previous.url], ['TOOLLERY_BRIDGE_TOKEN', previous.token]]) {
      if (value === undefined) delete process.env[name]
      else process.env[name] = value
    }
  }
})
