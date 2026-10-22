// Executable fixtures for the interactive demo; these are not ToolRet tools.
import { createHash } from 'node:crypto'

export const name = 'toollery-demo-tools'
export const inject = ['tools']

const number = description => ({ type: 'number', description })
const text = description => ({ type: 'string', description })

export function apply(ctx) {
  function register(name, description, properties, calculate) {
    ctx.tools.register({
      name, description,
      parameters: { type: 'object', properties, required: Object.keys(properties), additionalProperties: false },
      output: { schema: { type: 'string' }, render: (_args, value) => [{ type: 'text', text: value }] },
      async execute(args, execution) {
        execution.signal.throwIfAborted()
        return JSON.stringify(calculate(args))
      },
    })
  }
  register('add_numbers', 'Add two numbers and return their sum.',
    { a: number('First number'), b: number('Second number') }, ({ a, b }) => ({ sum: a + b }))
  register('multiply_numbers', 'Multiply two numbers and return their product.',
    { a: number('First factor'), b: number('Second factor') }, ({ a, b }) => ({ product: a * b }))
  register('celsius_to_fahrenheit', 'Convert a temperature from degrees Celsius to degrees Fahrenheit.',
    { celsius: number('Temperature in Celsius') }, ({ celsius }) => ({ fahrenheit: celsius * 9 / 5 + 32 }))
  register('kilometers_to_miles', 'Convert a distance in kilometers to miles.',
    { kilometers: number('Distance in kilometers') }, ({ kilometers }) => ({ miles: kilometers / 1.609344 }))
  register('compound_interest', 'Calculate final savings balance with annual compound interest, with no additional deposits.',
    { principal: number('Initial balance'), annual_rate_percent: number('Annual interest rate as a percent'), years: number('Years') },
    ({ principal, annual_rate_percent, years }) => ({ balance: principal * (1 + annual_rate_percent / 100) ** years }))
  register('monthly_loan_payment', 'Calculate the fixed monthly payment for an amortizing loan.',
    { principal: number('Loan amount'), annual_rate_percent: number('Annual interest rate as a percent'), months: number('Number of monthly payments') },
    ({ principal, annual_rate_percent, months }) => {
      if (principal < 0 || annual_rate_percent < 0 || months < 1 || !Number.isInteger(months)) throw new Error('Invalid loan terms')
      const rate = annual_rate_percent / 1200
      return { monthly_payment: rate === 0 ? principal / months : principal * rate / (1 - (1 + rate) ** -months) }
    })
  register('days_between_dates', 'Count elapsed calendar days between two ISO dates (YYYY-MM-DD).',
    { start: text('Start date'), end: text('End date') }, ({ start, end }) => {
      const parse = value => {
        if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) throw new Error('Expected YYYY-MM-DD')
        const date = new Date(value + 'T00:00:00Z')
        if (!Number.isFinite(+date) || date.toISOString().slice(0, 10) !== value) throw new Error('Invalid date')
        return +date
      }
      return { days: (parse(end) - parse(start)) / 86400000 }
    })
  register('word_count', 'Count whitespace-separated words in a piece of text.',
    { text: text('Text to count') }, ({ text }) => ({ words: text.trim() ? text.trim().split(/\s+/u).length : 0 }))
  register('replace_text', 'Replace all literal occurrences of a substring in text.',
    { text: text('Original text'), search: text('Literal substring to replace'), replacement: text('Replacement text') },
    ({ text, search, replacement }) => {
      if (!search) throw new Error('Search text must not be empty')
      return { text: text.split(search).join(replacement) }
    })
  register('sha256_text', 'Compute the SHA-256 hexadecimal hash of UTF-8 text.',
    { text: text('Text to hash') }, ({ text }) => ({ sha256: createHash('sha256').update(text, 'utf8').digest('hex') }))
  register('pretty_json', 'Parse a JSON string and format it with two-space indentation.',
    { json: text('Valid JSON text') }, ({ json }) => ({ formatted: JSON.stringify(JSON.parse(json), null, 2) }))
  register('unique_strings', 'Remove duplicate strings from an array while preserving their first occurrence.',
    { items: { type: 'array', items: { type: 'string' }, description: 'Input strings' } }, ({ items }) => ({ items: [...new Set(items)] }))
}
