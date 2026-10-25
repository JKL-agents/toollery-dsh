// Focused offline acceptance against installed DSH services, not mocked contexts.
// node tests/fixtures/dsh-skill-host.mjs <pnpm-store> <report.json>
import assert from 'node:assert/strict'
import { mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { existsSync } from 'node:fs'
import { pathToFileURL } from 'node:url'
import { register } from 'node:module'

const [store, report] = process.argv.slice(2)
const sampleCount = JSON.parse(await readFile(new URL('../../build/plugin/sample-skills/manifest.json', import.meta.url), 'utf8')).length
const entries = await readdir(store)
function entry(name) {
  const direct = join(store, '@deepseek-ai', name, 'lib/index.js')
  if (existsSync(direct)) return direct
  // pnpm truncates long package directory names on Windows.
  const entry = entries.find(item => item.startsWith(`@deepseek-ai+${name}`.slice(0, 24))
    && existsSync(join(store, item, 'node_modules/@deepseek-ai', name, 'lib/index.js')))
  assert.ok(entry, `Missing installed DSH package: ${name}`)
  return join(store, entry, 'node_modules/@deepseek-ai', name, 'lib/index.js')
}
async function load(name) { return import(pathToFileURL(entry(name))) }
const { Context } = await load('cordis')
const { agentEvents } = await load('dsh-agent')
const { createUserMessage } = await load('dsh-llm')
const { createScope } = await load('dsh-scope')
const { Session, SessionId } = await load('dsh-session')
const { SkillRegistry } = await load('dsh-skill')
const nativeSkill = await load('dsh-tool-skill')
const { ToolRuntime } = await load('dsh-tools')
const { SystemPrompt } = await load('dsh-system-prompt')
const root = await mkdtemp(join(tmpdir(), 'toollery real skill host '))
const ctx = new Context()
const disposers = []
const checks = []
const check = (name, fn) => { fn(); checks.push(name) }
async function mount(plugin, config) { const fiber = ctx.plugin(plugin, config); await fiber; disposers.push(() => fiber.dispose()); return fiber }
try {
  await mount(SystemPrompt, { includeHarnessIdentity: false })
  await mount(ToolRuntime, { mode: 'native' })
  await mount(SkillRegistry)
  const agent = { id: 'skill-host-acceptance', session: Session.create(SessionId('skill-host-acceptance')) }
  await mount({ inject: ['tools', 'systemPrompt'], apply(inner) {
    const scope = createScope(inner, agent)
    agent.ctx = scope.ctx
    inner.effect(() => () => scope.dispose())
  } })
  ctx.provide('agents', { roots: () => [agent] })
  ctx.provide('workingDirectory', { ensure: async () => root })
  ctx.provide('profileContext', { dir: root })
  ctx.provide('llm', {})
  const llmUrl = pathToFileURL(entry('dsh-llm')).href
  const resolver = `export async function resolve(specifier, context, nextResolve) { if (specifier === '@deepseek-ai/dsh-llm') return { url: ${JSON.stringify(llmUrl)}, shortCircuit: true }; return nextResolve(specifier, context) }`
  register(`data:text/javascript,${encodeURIComponent(resolver)}`, import.meta.url)
  const toolleryPlugin = await import('../../build/plugin/index.mjs')
  await mount(toolleryPlugin, { autoSetup: false, dataDirectory: '.', python: process.env.TOOLLERY_TEST_PYTHON || 'python' })
  const service = ctx.get('toollery')
  assert.ok(toolleryPlugin.inject.includes('skills'))
  assert.ok(!toolleryPlugin.inject.includes('workingDirectory'))
  await mount({ inject: ['skills', 'tools'], apply(inner) {
    inner.skills.register({ name: 'user-only', description: 'Never in model catalog', content: 'USER BODY',
      source: 'runtime', invocation: { modelInvocable: false, userInvocable: true } })
    for (const name of ['alpha-tool', 'beta-tool']) inner.tools.register({ name, description: name,
      parameters: { type: 'object', properties: {}, additionalProperties: false },
      output: { schema: { type: 'string' }, render: (_args, value) => [{ type: 'text', text: value }] },
      execute: async () => name })
  } })
  await mount(nativeSkill)
  const providerDeadline = Date.now() + 10000
  while (!(await ctx.skills.list({})).some(skill => skill.name === 'sample-meeting-minutes')) {
    assert.ok(Date.now() < providerDeadline, 'Bundled sample provider did not register through the plugin entry')
    await new Promise(done => setTimeout(done, 25))
  }
  const bundledSkills = await ctx.skills.list({})
  check('Plugin entry registers bundled skills through the injected native registry', () => {
    assert.ok(bundledSkills.length >= 90)
    assert.ok(bundledSkills.some(skill => skill.name === 'sample-meeting-minutes'))
  })
  await service.setup()
  assert.equal(service.phase, 'ready', service.message)
  await writeFile(join(root, 'manual-generator.json'), JSON.stringify({ provider: 'mock' }))
  // A resumed session may already contain the full native catalog. The
  // native consumer then suppresses it on the first Toollery-enabled step.
  const seeded = await agentEvents(ctx, agent).waterfall('agent/pre-step',
    { turn: 0, step: 1, messages: [], signal: new AbortController().signal },
    async () => ({ kind: 'enter', messages: [] }))
  for (const message of seeded.messages) agent.session.append('user/message', message, { surfaceOp: 'append' })
  await agentEvents(ctx, agent).serial('agent/created', { signal: new AbortController().signal })
  let turn = 0
  const signal = new AbortController().signal
  const catalogs = decision => decision.messages.filter(message => message.source?.kind === 'skill-catalog')
  async function step(prompt, { nextTurn = true, commit = true } = {}) {
    let messages = []
    if (nextTurn) {
      turn++
      const message = createUserMessage({ content: [{ type: 'text', text: prompt }], source: { kind: 'user' } })
      await agentEvents(ctx, agent).serial('agent/inbox/claimed', { message, turn })
      messages = [message]
    }
    // Same ordering as the real AgentLoop: claim -> assemble -> pre-step -> admit.
    const assembly = await ctx.systemPrompt.assemble({ agent, scope: agent, signal })
    const decision = await agentEvents(ctx, agent).waterfall('agent/pre-step', { turn, step: nextTurn ? 1 : 2, messages, signal },
      async () => ({ kind: 'enter', messages }))
    if (commit) for (const message of decision.messages) agent.session.append('user/message', message, { surfaceOp: 'append' })
    return { assembly, decision, catalog: catalogs(decision).at(-1) }
  }
  await service.configure(agent.id, { enabled: true, retrieval_target: 'skills', skill_representation: 'manual', k: 1 })
  const preflight = await service.preflightSkills(agent.id)
  assert.equal(preflight.status, 'approval_required')
  assert.ok(preflight.missing > 0)
  checks.push('Native catalog cache is checked before chat submission without generation calls')
  await service.prepareSkills(agent.id)
  const prepareDeadline = Date.now() + 30000
  while ((await service.status(agent.id)).skill_phase !== 'ready') {
    const status = await service.status(agent.id)
    assert.ok(Date.now() < prepareDeadline, `Skill manual preparation timed out: ${JSON.stringify({ skill_phase: status.skill_phase, skill_error: status.skill_error, skill_manual_count: status.skill_manual_count, skill_missing: status.skill_missing, phase: status.phase, error: status.error })}`)
    await new Promise(done => setTimeout(done, 50))
  }
  const first = await step('meeting minutes decisions action items')
  check('Manual retrieval ranks the real native catalog after body-derived manuals are prepared', () => {
    assert.equal(first.catalog.source.entries.length, 1)
    assert.equal(first.catalog.source.entries[0].name, 'sample-meeting-minutes')
    assert.deepEqual(first.assembly.tools.map(tool => tool.name).sort(), ['alpha-tool', 'beta-tool', 'skill'])
    assert.equal(service.sessions.get(agent.id).skill_representation, 'manual')
    assert.equal(service.sessions.get(agent.id).skill_manuals_prepared, true)
  })
  const loaded = await ctx.tools.execute({ callId: 'load-selected-skill', name: 'skill', arguments: { name: 'sample-meeting-minutes' }, agent, signal })
  check('Selected skill loads full body through real native tool pipeline', () => {
    assert.ok(!loaded.isError, JSON.stringify(loaded)); assert.match(JSON.stringify(loaded), /action items/i)
  })
  const again = await step('', { nextTurn: false })
  check('Later model step does not append a duplicate skill catalog', () => assert.equal(catalogs(again.decision).length, 0))
  const second = await step('write a clear git commit message')
  check('Next turn replaces previous selected skill', () => {
    assert.equal(second.catalog.source.entries.length, 1)
    assert.notEqual(second.catalog.source.entries[0].name, first.catalog.source.entries[0].name)
  })
  const unadmitted = await step('draft a professional email', { commit: false })
  const retried = await step('draft a professional email')
  check('Unadmitted selection does not suppress the next turn catalog', () => {
    assert.ok(unadmitted.catalog)
    assert.ok(retried.catalog)
    assert.deepEqual(retried.catalog.source.entries, unadmitted.catalog.source.entries)
  })
  await service.configure(agent.id, { budget: 'all' })
  const all = await step('all skills')
  check('All budget exposes complete eligible catalog, excluding user-only skill', () => {
    assert.equal(all.catalog.source.entries.length, sampleCount); assert.ok(!all.catalog.source.entries.some(x => x.name === 'user-only'))
  })
  await service.configure(agent.id, { budget: 'top_k', k: 1 })
  const afterAll = await step('meeting minutes decisions action items')
  check('All-to-top-k reranks even when native catalog is unchanged', () => assert.equal(afterAll.catalog?.source.entries.length, 1))
  await service.configure(agent.id, { enabled: false })
  const off = await step('plain chat')
  check('OFF restores full native catalog and tools', () => {
    assert.equal(off.catalog.source.entries.length, sampleCount); assert.equal(off.assembly.tools.length, 3)
  })
  // Both requires a prepared tool index; prepare with mock manuals only.
  await writeFile(join(root, 'manual-generator.json'), JSON.stringify({ provider: 'mock' }))
  await service.prepare(agent.id)
  const deadline = Date.now() + 15000
  while ((await service.status(agent.id)).phase !== 'ready') {
    assert.ok(Date.now() < deadline, 'Mock preparation timed out')
    await new Promise(done => setTimeout(done, 50))
  }
  await service.configure(agent.id, { enabled: true, retrieval_target: 'both', representation: 'spec', k: 1, skill_k: 2 })
  const both = await step('Use sample-meeting-minutes and sample-agent-architecture; add alpha-tool.')
  const bothStatus = await service.status(agent.id)
  check('Both ranks skills and tools while keeping native skill loader', () => {
    assert.equal(both.catalog.source.entries.length, 2)
    assert.ok(both.assembly.tools.some(x => x.name === 'skill'))
    assert.ok(both.assembly.tools.length <= 2)
    const selection = bothStatus.last_selection
    assert.equal(selection.tool_k, 1)
    assert.equal(selection.skill_k, 2)
  })
  const restriction = agent.ctx.tools.restrict({ allow: ['alpha-tool'] })
  try {
    await service.configure(agent.id, { retrieval_target: 'skills' })
    const restricted = await step('meeting minutes')
    check('Host-restricted loader is not resurrected and no catalog is exposed', () => {
      assert.deepEqual(restricted.assembly.tools.map(x => x.name), ['alpha-tool'])
      assert.ok(!restricted.catalog || restricted.catalog.source.entries.length === 0)
    })
  } finally { restriction() }
  const result = { passed: true, paid_api_calls: 0, real_dsh_services: true, checks }
  if (report) await writeFile(report, JSON.stringify(result, null, 2))
  console.log(JSON.stringify(result, null, 2))
} finally {
  for (const dispose of disposers.reverse()) await dispose()
  await rm(root, { recursive: true, force: true })
}
