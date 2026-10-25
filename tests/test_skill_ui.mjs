// Exercise the browser module's rendered controls without a Desktop profile.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'

const source = readFileSync(new URL('../toollery_dsh/assets/dsh/ui/client.js', import.meta.url), 'utf8')

function render(target, overrides = {}) {
  const record = { turn: 1, enabled: true, retrieval_target: target, representation: 'spec_manual', skill_representation: 'spec_manual',
    budget: 'top_k', k: 3, tool_k: 3, skill_k: 5, retrieval_ms: 0, skill_retrieval_ms: .26, full_pool_count: 45,
    eligible_pool_count: 45, selected_names: [], skill_selected_names: ['meeting-minutes'], skill_candidate_count: 110 }
  const data = { enabled: true, retrieval_target: target, representation: 'spec_manual', skill_representation: 'spec_manual', budget: 'top_k',
    k: 3, tool_k: 3, skill_k: 5, tool_count: 44, last_selection: record, turns: [record], ...overrides }
  const slots = new Map()
  const requests = []
  const blocks = []
  const effects = []
  let stateIndex = 0
  const React = {
    createElement: (type, props, ...children) => ({ type, props: props || {}, children }),
    useState: () => [['10', '80'][stateIndex++], () => {}], // Tool and skill k have independent drafts.
    useEffect: effect => effects.push(effect),
    useSyncExternalStore: (_subscribe, snapshot) => typeof snapshot() === 'boolean'
      ? snapshot() : { data, busy: false, error: '' },
  }
  let plugin
  vm.runInNewContext(source, {
    window: { __ModuleLoader__: { load: definition => { plugin = definition.factory(() => React) } } },
    fetch: async (_url, options) => {
      requests.push(JSON.parse(options.body))
      return { ok: true, json: async () => data }
    },
  })
  const ctx = { conversation: { blocks: { set: (...args) => blocks.push(args) } },
    slots: { inject: (_name, apply) => apply(), register: (slot, component) => slots.set(slot.name, component) } }
  plugin.apply(ctx)
  const controls = slots.get('conversation.composer.dock')({ sessionId: 'test' })
  for (const effect of effects) effect()
  return { controls,
    card: slots.get('conversation.chat.turnTail')({ sessionId: 'test', turn: { turn: 1 } }), requests, blocks }
}

function* descendants(node) {
  if (Array.isArray(node)) { for (const child of node) yield* descendants(child) }
  else if (node && typeof node === 'object') {
    yield node
    yield* descendants(node.children)
  }
}
function text(node) {
  if (Array.isArray(node)) return node.map(text).join('')
  return node && typeof node === 'object' ? text(node.children) : typeof node === 'string' ? node : ''
}

test('representation controls are concise and both k values update together', async () => {
  for (const target of ['skills', 'both', 'tools']) {
    const { controls, card, requests } = render(target)
    const nodes = [...descendants(controls)]
    const representation = nodes.find(node => node.props?.['aria-label'] === 'Toollery tool rep')
    const skillRepresentation = nodes.find(node => node.props?.['aria-label'] === 'Toollery skill rep')
    const toolK = nodes.find(node => node.props?.['aria-label'] === (target === 'both' ? 'Toollery tool k' : target === 'tools' ? 'Toollery k' : ''))
    const skillK = nodes.find(node => node.props?.['aria-label'] === (target === 'both' ? 'Toollery skill k' : target === 'skills' ? 'Toollery skill k' : ''))
    const applyButtons = nodes.filter(node => node.type === 'button' && /^(Apply|Update)/.test(text(node)))
    assert.equal(!!representation, target !== 'skills')
    assert.equal(!!skillRepresentation, target !== 'tools')
    assert.equal(text(controls).includes('Tool rep'), target !== 'skills')
    assert.equal(text(controls).includes('Skill rep'), target !== 'tools')
    assert.doesNotMatch(text(controls), /Indexed tool pool/)
    if (representation) assert.equal(representation.props.value, 'spec_manual', 'Keep the stored tool mode for switching back')
    assert.equal(toolK?.props.max, target === 'skills' ? undefined : 44)
    assert.match(text(card), /Tool pool 45/)
    assert.doesNotMatch(text(controls), /Skill retrieval: .*BM25/)
    if (target === 'tools') {
      assert.equal(applyButtons.length, 1)
      await applyButtons[0].props.onClick()
      assert.equal(requests[0].options.tool_k, 10)
    } else if (target === 'skills') {
      assert.equal(skillK.props.value, '80')
      assert.equal(applyButtons.length, 1)
      await applyButtons[0].props.onClick()
      assert.equal(requests[0].options.skill_k, 80)
      assert.match(text(card), /skills: spec_manual/)
    } else {
      assert.equal(applyButtons.length, 1)
      assert.equal(text(applyButtons[0]), 'Update k')
      await applyButtons[0].props.onClick()
      assert.deepEqual(requests.map(request => request.options), [{ tool_k: 10, skill_k: 80 }])
    }
    if (target === 'skills') assert.match(text(card), /skills: spec_manual/)
  }
})

test('missing skill manuals are explained before chat and generation needs a direct user click', async () => {
  const { controls, requests, blocks } = render('skills', {
    phase: 'ready', skill_representation: 'manual', skill_phase: 'approval_required', skill_manual_count: 2,
    skill_missing: 2, skill_manuals_valid: 0, skill_manuals_prepared: false,
  })
  assert.match(blocks.at(-1)[1].reason, /Approve skill-manual generation/)
  const nodes = [...descendants(controls)]
  const manualPanel = nodes.find(node => typeof node.type === 'function'
    && node.type.toString().includes('Skill contents are sent'))
  const manualCard = manualPanel.type(manualPanel.props)
  assert.match(text(manualCard), /Skill contents are sent to the configured generation service only after you click below/)
  const approve = [...descendants(manualCard)].find(node => node.type === 'button' && /^Generate 2 missing skill manuals/.test(text(node)))
  assert.ok(approve)
  await approve.props.onClick()
  assert.equal(requests[0].action, 'prepare_skills')
  assert.match(blocks.at(-1)[1].reason, /checking or preparing skill manuals/)
})

test('OFF status explains the host-eligible tool and skill boundary', () => {
  const { controls } = render('both', { enabled: false })
  assert.match(text(controls), /OFF: the next turn bypasses Toollery retrieval/)
  assert.match(text(controls), /host-eligible tools and native skill catalog remain available/)
  assert.match(text(controls), /host restrictions still apply/)
})
