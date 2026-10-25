import assert from 'node:assert/strict'
import test from 'node:test'
import { ToolleryService } from '../plugin/service.mjs'

test('skill searches use the independent skill k and keep the legacy per-call alias', async () => {
  const service = new ToolleryService('unused')
  service.sessions.set('session', { skill_representation: 'spec', budget: 'top_k', k: 2, tool_k: 2, skill_k: 7 })
  const requests = []
  service.post = async (path, payload) => {
    requests.push({ path, payload })
    return { status: 'ranked', selected_names: [] }
  }

  await service.rankSkills('session', 'find a skill', [{ name: 'skill-a', description: '' }])
  await service.rankSkills('session', 'find a skill', [{ name: 'skill-a', description: '' }], { k: 4 })

  assert.deepEqual(requests.map(({ path, payload }) => [path, payload.skill_k]), [
    ['/skill-search', 7], ['/skill-search', 4],
  ])
  await service.dispose()
})

test('status performs one local-only skill manual cache preflight before generation approval', async () => {
  const service = new ToolleryService('unused')
  service.phase = 'ready'
  service.transport = { url: 'http://127.0.0.1:1', token: 'test' }
  service.sessions.set('session', { schemas: [], snapshot_hash: 'snapshot', enabled: true,
    retrieval_target: 'skills', skill_representation: 'manual', phase: 'ready' })
  service.registerSkillCatalogLoader('session', async () => [
    { name: 'one', description: 'One workflow', content: 'Private skill body one' },
    { name: 'two', description: 'Two workflow', content: 'Private skill body two' },
  ])
  const calls = []
  let preflighted = false
  service.post = async path => {
    calls.push(path)
    if (path === '/generator') return { generator: { configured: true } }
    if (path === '/skill-preflight') { preflighted = true; return { status: 'approval_required', total: 2, missing: 2, valid: 0 } }
    if (path === '/status') return {
      enabled: true, retrieval_target: 'skills', skill_representation: 'manual',
      skill_phase: preflighted ? 'approval_required' : 'preflight_required',
      skill_manual_count: 2, skill_missing: 2, skill_manuals_valid: 0,
      skill_manuals_prepared: false, phase: 'ready', tool_count: 0, turns: [],
    }
    throw new Error(`Unexpected route ${path}`)
  }

  const status = await service.status('session')
  assert.equal(status.skill_phase, 'approval_required')
  assert.equal(status.skill_missing, 2)
  assert.equal(calls.filter(path => path === '/skill-preflight').length, 1)
  assert.equal(calls.some(path => path === '/skill-prepare'), false)
  await service.status('session')
  assert.equal(calls.filter(path => path === '/skill-preflight').length, 1)
  await service.dispose()
})
