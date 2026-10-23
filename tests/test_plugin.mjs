// Run after scripts/build_plugin.py; exercises the shipped worker without a DSH SDK.
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync, writeFileSync, existsSync, readFileSync, mkdirSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { createServer } from 'node:http'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { ToolleryService } from '../build/plugin/service.mjs'

const tools = [
  { name: 'add', description: 'Add numbers and return their sum', parameters: { type: 'object' } },
  { name: 'temperature', description: 'Convert Celsius Fahrenheit temperature', parameters: { type: 'object' } },
]
async function until(fn, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const value = await fn()
    if (value) return value
    await new Promise(done => setTimeout(done, 50))
  }
  throw new Error('Timed out waiting for worker state')
}

test('shipped bundle setup, explicit preparation, current selections, failure/OFF, restart and disposal', { timeout: 120000 }, async () => {
  const directory = mkdtempSync(join(tmpdir(), 'toollery plugin '))
  const service = new ToolleryService(directory, { python: process.env.TOOLLERY_TEST_PYTHON || 'python' })
  const inherited = Object.fromEntries(['PYTHONPATH', 'PYTHONHOME', 'PIP_TARGET'].map(key => [key, process.env[key]]))
  const shadow = join(directory, 'toollery_dsh')
  mkdirSync(shadow)
  writeFileSync(join(shadow, '__init__.py'), 'raise RuntimeError("Profile source must not shadow the installed worker")')
  process.env.PYTHONPATH = directory
  process.env.PYTHONHOME = join(directory, 'nonexistent python home')
  process.env.PIP_TARGET = join(directory, 'wrong pip target')
  let provider
  let releaseGeneration
  try {
    await service.register('native', tools)
    assert.equal((await service.status('native')).phase, 'needs_setup')
    await service.setup()
    assert.equal(service.phase, 'ready', service.message)
    assert.ok(existsSync(join(directory, 'manual-generator.json')))
    const worker = JSON.parse(readFileSync(join(directory, 'worker.json'), 'utf8'))
    const probe = spawnSync(worker.python, ['-I', '-c',
      'import json, sys; from pathlib import Path; import importlib.metadata as m; import toollery_dsh.worker as w; print(json.dumps({"version": m.version("toollery-dsh"), "private": Path(w.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), "packages": sorted(d.metadata["Name"] for d in m.distributions())}))'],
      { cwd: directory, encoding: 'utf8', windowsHide: true })
    assert.equal(probe.status, 0, probe.stderr)
    const installed = JSON.parse(probe.stdout)
    assert.equal(installed.version, worker.version)
    assert.equal(installed.private, true)
    assert.deepEqual(installed.packages, ['pip', 'toollery-dsh'])
    assert.equal(existsSync(process.env.PIP_TARGET), false)
    await until(async () => (await service.status('native')).phase === 'needs_preparation')
    assert.equal((await service.status('native')).manuals_valid, 0)
    writeFileSync(join(directory, 'manual-generator.json'), JSON.stringify({ provider: 'mock' }))
    await service.configure('native', { enabled: false })
    await service.prepare('native')
    await until(async () => (await service.status('native')).phase === 'ready')
    assert.equal((await service.status('native')).enabled, false)
    await service.configure('native', { enabled: true, representation: 'spec', k: 1 })
    for (const [prompt, expected] of [['Add numbers', 'add'], ['Celsius Fahrenheit', 'temperature']]) {
      const result = await service.post('/select', { session_id: 'native', snapshot_hash: service.snapshotHash('native'), prompt, eligible_names: tools.map(t => t.name) })
      assert.deepEqual(result.selected_names, [expected])
    }
    const restricted = await service.post('/select', { session_id: 'native', snapshot_hash: service.snapshotHash('native'), prompt: 'Add numbers', eligible_names: ['temperature'] })
    assert.deepEqual(restricted.selected_names, [])
    const child = service.child
    child.kill()
    await until(() => !service.child)
    const failed = await service.status('native')
    assert.equal(failed.enabled, true)
    assert.match(failed.error, /Worker stopped/)
    await service.configure('native', { enabled: false })
    const turn = service.bypass('native', [tools[1]], 4)
    await service.recordCall({ session_id: 'native', selection_id: turn.selection_id, call_id: 'call1', name: 'temperature', status: 'success' })
    assert.equal((await service.status('native')).turns.at(-1).calls[0].status, 'success')
    await service.start()
    await until(async () => (await service.status('native')).phase === 'ready')
    assert.equal((await service.status('native')).enabled, false)
    assert.equal((await service.status('native')).manuals_valid, 2)
    // Exercise the actual installed provider subprocess from a hostile profile
    // directory, using a local API fixture rather than a paid model.
    let calls = 0
    let expectedKey = 'offline-fixture-key'
    provider = createServer((request, response) => {
      let body = ''
      request.on('data', chunk => { body += chunk })
      request.on('end', () => {
        calls++
        assert.equal(request.headers.authorization, `Bearer ${expectedKey}`)
        const capability = JSON.parse(JSON.parse(body).messages[1].content).capabilities[0]
        response.setHeader('Content-Type', 'application/json')
        const complete = () => response.end(JSON.stringify({ choices: [{ finish_reason: 'stop', message: { content: JSON.stringify({
          [capability.name]: [1, 2, 3].map(n => ({ query: `Fixture intent ${n} for ${capability.name}`, scenario_type: 'fixture', generation_notes: 'Offline API' })),
        }) } }] }))
        if (capability.name === 'multiply' && !releaseGeneration) releaseGeneration = () => { if (!response.writableEnded) complete() }
        else complete()
      })
    })
    await new Promise(done => provider.listen(0, '127.0.0.1', done))
    const manualSettings = { model: 'offline-fixture', api_url: `http://127.0.0.1:${provider.address().port}`, api_key: expectedKey }
    const saved = await service.saveGenerator(manualSettings)
    assert.equal(saved.configured, true)
    assert.equal(JSON.stringify(await service.status()).includes(expectedKey), false)
    assert.equal(calls, 0, 'Saving settings must not make provider requests')
    await service.register('api', tools)
    await until(async () => (await service.status('api')).phase === 'needs_preparation')
    await service.prepare('api')
    await until(async () => (await service.status('api')).phase === 'ready')
    assert.equal(calls, 2)
    assert.equal((await service.status('api')).manuals_valid, 2)
    const hostHashBeforeDiscovery = service.snapshotHash('api')
    const discoveryCandidates = [
      { schema: { name: 'calendar.create_event', description: 'Create calendar events', parameters: { type: 'object' } },
        tool_id: 'calendar.create_event', candidate_ref: 'provider-ref-17', provider_id: 'calendar-provider',
        executor: { opaque: 'provider-only' }, source: 'remote' },
      { schema: { name: 'weather', description: 'Get weather forecasts', parameters: { type: 'object' } },
        tool_id: 'weather', candidate_ref: 'provider-ref-18', provider_id: 'calendar-provider' },
    ]
    const discovery = await service.rankCandidates('api', 'Create a calendar event', discoveryCandidates,
      { representation: 'spec_manual', k: 1, requestId: 'discovery-request-1' })
    assert.equal(discovery.status, 'ranked')
    assert.equal(discovery.request_id, 'discovery-request-1')
    assert.match(discovery.candidate_snapshot_hash, /^sha256:/)
    assert.equal(discovery.selection.method, 'spec_manual')
    assert.equal(discovery.selected[0].tool_id, 'calendar.create_event')
    assert.equal(discovery.selected[0].candidate_ref, 'provider-ref-17')
    assert.match(discovery.selected[0].spec_hash, /^sha256:/)
    assert.deepEqual(discovery.selected_names, ['calendar.create_event'])
    assert.equal(discovery.candidate_count, 2)
    assert.equal(calls, 4, 'Discovery generates manuals only for the provider batch')
    assert.equal(service.snapshotHash('api'), hostHashBeforeDiscovery)
    assert.equal((await service.status('api')).tool_count, 2)
    // A retry may use a fresh request ID while reusing the completed snapshot job.
    const retriedDiscovery = await service.rankCandidates('api', 'Create a calendar event', discoveryCandidates,
      { representation: 'spec_manual', k: 1, requestId: 'discovery-request-2' })
    assert.equal(retriedDiscovery.request_id, 'discovery-request-1')
    assert.deepEqual(retriedDiscovery.selected_names, ['calendar.create_event'])
    assert.equal(calls, 4, 'A matching retry must not regenerate manuals')
    const expanded = [...tools, { name: 'multiply', description: 'Multiply two numbers', parameters: { type: 'object' } }]
    await assert.rejects(service.reconcile('api', expanded, undefined, { maxAutoTools: 0 }), { code: 'approval_required' })
    assert.equal(calls, 4, 'Approval-required must not generate manuals')
    assert.equal((await service.status('api')).pending_tool_count, 3)
    await service.prepare('api')
    await until(() => releaseGeneration)
    await assert.rejects(service.reconcile('api', expanded, undefined, { timeoutMs: 150 }), error => {
      assert.equal(error.code, 'refresh_pending')
      assert.equal(error.detail.generation_cancelled, false)
      return true
    })
    assert.equal(calls, 5, 'A wait timeout must not restart the provider call')
    const retryRefresh = service.reconcile('api', expanded)
    releaseGeneration()
    await until(async () => (await service.status('api')).phase === 'ready')
    assert.equal((await service.status('api')).tool_count, 3,
      'Approved refresh status must show the committed pool without waiting for a new selection')
    const refreshed = await retryRefresh
    assert.equal(refreshed.status, 'committed')
    assert.deepEqual(refreshed.diff.added, ['multiply'])
    assert.equal((await service.status('api')).tool_count, 3)
    assert.equal(calls, 5, 'Only the new host tool should require a generation request')
    await service.configure('api', { enabled: true, k: 1 })
    const newSelection = await service.post('/select', {
      session_id: 'api', snapshot_hash: service.snapshotHash('api'), pool_revision: service.poolRevision('api'),
      prompt: 'Multiply numbers', eligible_names: expanded.map(tool => tool.name),
    })
    assert.deepEqual(newSelection.selected_names, ['multiply'])
    expectedKey = 'rotated-fixture-key'
    await service.saveGenerator({ ...manualSettings, api_key: expectedKey })
    await until(async () => (await service.status('api')).phase === 'ready')
    assert.equal(calls, 5, 'Rotating credentials reuses existing manuals')
    // Changing a model rechecks the cache but waits for explicit generation.
    await service.saveGenerator({ ...manualSettings, model: 'offline-fixture-next', api_key: '' })
    await until(async () => (await service.status('api')).phase === 'needs_preparation')
    assert.equal(calls, 5)
    await service.prepare('api')
    await until(async () => (await service.status('api')).phase === 'ready')
    assert.equal(calls, 8)
    await service.stop()
    writeFileSync(join(directory, 'worker.json'), JSON.stringify({ ...worker, version: 'old-version' }))
    await service.start()
    assert.equal(service.child, null)
    assert.equal(service.phase, 'needs_setup')
    assert.match(service.message, /version.*setup/i)
    await service.setup()
    assert.equal(service.phase, 'ready', service.message)
    await until(async () => (await service.status('api')).phase === 'ready')
    assert.equal((await service.status('api')).manuals_valid, 3)
    assert.equal(calls, 8, 'Repeated setup must preserve the generator configuration and cached manuals')
    service.unregister('native')
    await service.stop()
    assert.equal(service.child, null)
    service.dispose()
    assert.equal(service.sessions.size, 0)
  } finally {
    releaseGeneration?.()
    await service.stop()
    if (provider) await new Promise(done => provider.close(done))
    for (const [key, value] of Object.entries(inherited)) {
      if (value === undefined) delete process.env[key]
      else process.env[key] = value
    }
    rmSync(directory, { recursive: true, force: true })
  }
})
