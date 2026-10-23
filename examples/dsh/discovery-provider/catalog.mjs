// A provider-owned catalog, not DSH registrations. Return a complete batch:
// no query matching or prefiltering happens in this provider.
import { createHash } from 'node:crypto'

const number = description => ({ type: 'number', description })
const string = description => ({ type: 'string', description })
const records = new Map()
function add(id, description, properties, execute) {
  records.set(id, {
    candidate: {
      tool_id: `local-demo:${id}`, provider_id: 'local-demo', candidate_ref: `local-demo-v1:${id}`,
      schema: { name: id, description, parameters: {
        type: 'object', properties, required: Object.keys(properties), additionalProperties: false,
      } },
    },
    execute,
  })
}
add('add_numbers', 'Add two numbers and return their sum.',
  { a: number('First number'), b: number('Second number') }, ({ a, b }) => ({ sum: a + b }))
add('multiply_numbers', 'Multiply two numbers and return their product.',
  { a: number('First factor'), b: number('Second factor') }, ({ a, b }) => ({ product: a * b }))
add('celsius_to_fahrenheit', 'Convert a temperature from Celsius to Fahrenheit.',
  { celsius: number('Temperature in Celsius') }, ({ celsius }) => ({ fahrenheit: celsius * 9 / 5 + 32 }))
add('kilometers_to_miles', 'Convert a distance from kilometers to miles.',
  { kilometers: number('Distance in kilometers') }, ({ kilometers }) => ({ miles: kilometers / 1.609344 }))
add('sha256_text', 'Compute the SHA-256 hexadecimal hash of UTF-8 text.',
  { text: string('Text to hash') }, ({ text }) => ({ sha256: createHash('sha256').update(text, 'utf8').digest('hex') }))
add('word_count', 'Count whitespace-separated words in text.',
  { text: string('Text to count') }, ({ text }) => ({ words: text.trim() ? text.trim().split(/\s+/u).length : 0 }))

export async function fetchCandidateBatch(signal) {
  signal?.throwIfAborted()
  // A real provider would fetch its authenticated catalog here. Only schema
  // data and opaque references cross into Toollery; callbacks stay here.
  return [...records.values()].map(record => structuredClone(record.candidate))
}

export function resolveCandidate(toolId, candidateRef) {
  const record = [...records.values()].find(record =>
    record.candidate.tool_id === toolId && record.candidate.candidate_ref === candidateRef)
  // Return detached metadata; callers cannot mutate the provider's catalog.
  return record && { ...structuredClone(record.candidate), execute: record.execute }
}
