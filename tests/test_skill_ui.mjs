// Exercise the browser module's rendered controls without a Desktop profile.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'

const source = readFileSync(new URL('../toollery_dsh/assets/dsh/ui/client.js', import.meta.url), 'utf8')

function render(target) {
  const record = { turn: 1, enabled: true, retrieval_target: target, representation: 'spec_manual',
    budget: 'top_k', k: 3, retrieval_ms: 0, skill_retrieval_ms: .26, full_pool_count: 45,
    eligible_pool_count: 45, selected_names: [], skill_selected_names: ['meeting-minutes'], skill_candidate_count: 110 }
  const data = { enabled: true, retrieval_target: target, representation: 'spec_manual', budget: 'top_k',
    k: 3, tool_count: 44, last_selection: record, turns: [record] }
  const slots = new Map()
  const requests = []
  const React = {
    createElement: (type, props, ...children) => ({ type, props: props || {}, children }),
    useState: () => ['80', () => {}], // User edits k beyond the indexed tool count.
    useEffect: () => {},
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
  const ctx = { slots: { inject: (_name, apply) => apply(), register: (slot, component) => slots.set(slot.name, component) } }
  plugin.apply(ctx)
  return { controls: slots.get('conversation.composer.dock')({ sessionId: 'test' }),
    card: slots.get('conversation.chat.turnTail')({ sessionId: 'test', turn: { turn: 1 } }), requests }
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

test('Skills/Both identify metadata retrieval and can apply k above the tool count', async () => {
  for (const target of ['skills', 'both', 'tools']) {
    const { controls, card, requests } = render(target)
    const nodes = [...descendants(controls)]
    const representation = nodes.find(node => node.props['aria-label'] === 'Toollery representation')
    const k = nodes.find(node => node.props['aria-label'] === 'Toollery k')
    const apply = nodes.find(node => node.type === 'button' && text(node) === 'Apply k')
    assert.equal(representation.props.disabled, target === 'skills')
    assert.equal(representation.props.value, 'spec_manual', 'Keep the stored tool mode for switching back')
    assert.equal(apply.props.disabled, target === 'tools')
    assert.equal(k.props.max, target === 'tools' ? 44 : undefined)
    assert.match(text(card), /Tool pool 45/)
    if (target !== 'tools') {
      assert.match(text(controls), /Skill retrieval: metadata BM25/)
      assert.match(text(card), /metadata BM25/)
      await apply.props.onClick()
      assert.equal(requests[0].options.k, 80)
    }
    if (target === 'both') assert.match(text(controls), /Tool representation/)
    if (target === 'skills') assert.doesNotMatch(text(card), /spec_manual/)
  }
})
