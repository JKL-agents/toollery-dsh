import { test } from 'node:test'
import assert from 'node:assert/strict'
import { sameSnapshot } from '../toollery_dsh/assets/dsh/toollery.mjs'

test('snapshot comparison accepts reordered schemas and properties but detects every change', () => {
  const tools = [
    { name: 'a', description: 'raw description', parameters: { type: 'object', required: ['x', 'y'], properties: { x: { type: 'string' } } } },
    { name: 'b', description: 'other description', parameters: {} },
  ]
  const frozen = new Map(structuredClone(tools).map(tool => [tool.name, tool]))
  assert.ok(sameSnapshot([...tools].reverse(), frozen))
  const reordered = structuredClone(tools)
  reordered[0].parameters = { properties: { x: { type: 'string' } }, required: ['x', 'y'], type: 'object' }
  assert.ok(sameSnapshot(reordered, frozen))
  for (const mutate of [
    rows => { rows[0].description += ' changed' },
    rows => { rows[0].parameters.properties.x.type = 'number' },
    rows => { rows[0].parameters.required.reverse() },
    rows => { rows[0].deferLoading = true },
    rows => { rows.push(rows[0]) },
    rows => { rows[1] = rows[0] },
    rows => { rows.pop() },
    rows => { delete rows[0].description },
  ]) {
    const changed = structuredClone(tools)
    mutate(changed)
    assert.equal(sameSnapshot(changed, frozen), false)
  }
  // A mutation of the registry's original object must not mutate the baseline.
  tools[0].parameters.required.push('new')
  assert.equal(sameSnapshot(tools, frozen), false)
})
