import assert from 'node:assert/strict'
import test from 'node:test'
import { apply, inject } from '../examples/dsh/discovery-provider/index.mjs'
import { fetchCandidateBatch, resolveCandidate } from '../examples/dsh/discovery-provider/catalog.mjs'

function fixture(rankCandidates, config = {}) {
  const registered = []
  const agent = { id: 'native-root' }
  const signal = new AbortController().signal
  apply({
    get(name) { assert.equal(name, 'toollery'); return { rankCandidates } },
    agents: { roots: () => [agent] },
    tools: { register: tool => registered.push(tool) },
  }, config)
  assert.deepEqual(inject, ['tools', 'agents', 'toollery'])
  assert.deepEqual(registered.map(tool => tool.name), ['discover_tools'])
  const tool = registered[0]
  return { tool, agent, signal, call: (args = { query: 'Celsius Fahrenheit' }, execution = { agent, signal }) => tool.execute(args, execution) }
}

function ranked(batch) {
  const candidate = batch.find(item => item.schema.name === 'celsius_to_fahrenheit')
  return { status: 'ranked', request_id: 'request-1', candidate_snapshot_hash: 'snapshot-1',
    candidate_count: batch.length, selection: { representation: 'spec_manual', k: 3 },
    selected: [{ tool_id: candidate.tool_id, candidate_ref: candidate.candidate_ref,
      name: candidate.schema.name, spec_hash: 'spec-1', score: 1 }],
    manual_report: { generated: 0 } }
}

test('discovery registers only its entry tool and hands the complete batch to Toollery', async () => {
  let observed
  const f = fixture(async (...args) => { observed = args; return ranked(args[2]) })
  const result = JSON.parse(await f.call())
  const [id, query, batch, options, signal] = observed
  assert.equal(id, f.agent.id)
  assert.equal(query, 'Celsius Fahrenheit')
  assert.equal(signal, f.signal)
  assert.equal(batch.length, 6)
  assert.equal(new Set(batch.map(c => c.tool_id)).size, 6)
  assert.ok(batch.some(c => c.schema.name === 'word_count'), 'unrelated candidates must not be prefiltered')
  assert.ok(batch.every(c => typeof c.candidate_ref === 'string' && !('execute' in c)))
  assert.equal(options.maxAutoTools, 0)
  assert.equal(options.allowGeneration, false)
  assert.equal(options.timeoutMs, 120000)
  assert.equal(options.budget, 'top_k')
  assert.equal(result.candidate_count, 6)
  assert.equal(result.selected[0].spec_hash, 'spec-1')
  assert.match(result.message, /No candidate was registered or executed/)
  assert.equal(resolveCandidate(result.selected[0].tool_id, result.selected[0].candidate_ref).schema.name, 'celsius_to_fahrenheit')
})

test('owner configuration permits bounded generation; model arguments cannot enable it', async () => {
  let options
  const f = fixture(async (_id, _q, batch, opts) => { options = opts; return ranked(batch) },
    { allowManualGeneration: true, k: 2, representation: 'manual' })
  await assert.rejects(f.call({ query: 'temperature', allowManualGeneration: true }), /Provide only/)
  assert.equal(options, undefined)
  await f.call()
  assert.equal(options.maxAutoTools, 6)
  assert.equal(options.allowGeneration, false)
  assert.equal(options.k, 2)
  assert.equal(options.representation, 'manual')
})

test('approval and pending jobs remain explicit, with no automatic retry', async () => {
  let calls = 0
  const approval = fixture(async () => { calls++; return { status: 'approval_required', missing: 6, max_auto_tools: 0 } })
  assert.equal(JSON.parse(await approval.call()).status, 'approval_required')
  const pending = fixture(async () => { calls++; throw Object.assign(new Error('private diagnostic'), {
    code: 'discovery_pending', detail: { job_id: 'job-1', candidate_snapshot_hash: 'snapshot-1' },
  }) })
  const result = JSON.parse(await pending.call())
  assert.equal(result.status, 'discovery_pending')
  assert.equal(result.job_id, 'job-1')
  assert.equal(result.generation_cancelled, false)
  assert.equal(calls, 2)
})

test('invalid inputs, unmanaged agents and cancellation never reach ranking', async () => {
  let calls = 0
  const f = fixture(async () => { calls++; throw new Error('unexpected') })
  for (const args of [null, [], {}, { query: '' }, { query: 'x'.repeat(4001) }, { query: 'ok', k: 6 }]) {
    await assert.rejects(f.call(args), /Provide only/)
  }
  await assert.rejects(f.call({ query: 'ok' }, { agent: { id: 'other' }, signal: f.signal }), /native root/)
  const controller = new AbortController()
  controller.abort()
  await assert.rejects(f.call({ query: 'ok' }, { agent: f.agent, signal: controller.signal }), { name: 'AbortError' })
  assert.equal(calls, 0)
})

test('in-flight cancellation is preserved and provider diagnostics are not exposed', async () => {
  const controller = new AbortController()
  const f = fixture(async () => { controller.abort(); throw new Error('private diagnostic') })
  await assert.rejects(f.call({ query: 'ok' }, { agent: f.agent, signal: controller.signal }), { name: 'AbortError' })
  const failed = fixture(async () => { throw new Error('private credential diagnostic') })
  await assert.rejects(failed.call(), error => !error.message.includes('credential') && /ranking failed/.test(error.message))
})

test('provider rejects unknown identities and keeps catalog metadata detached', async () => {
  const f = fixture(async (_id, _q, batch) => {
    const result = ranked(batch)
    result.selected[0].candidate_ref = 'unknown-ref'
    return result
  })
  await assert.rejects(f.call(), /unknown candidate identity/)
  const batch = await fetchCandidateBatch()
  batch[0].schema.name = 'modified'
  assert.equal((await fetchCandidateBatch())[0].schema.name, 'add_numbers')
  assert.equal(resolveCandidate('local-demo:add_numbers', 'wrong-ref'), undefined)
})
