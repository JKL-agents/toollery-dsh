import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const providerName = 'toollery-sample-skills'
// Match DSH's bundled-skill precedence without adding a module-resolution
// dependency on @deepseek-ai/dsh-skill inside the optional sample provider.
const bundledSkillRank = 600
const packageDirectory = dirname(fileURLToPath(import.meta.url))
const skillDirectory = join(packageDirectory, 'sample-skills')

export function registerSampleSkills(ctx, root = skillDirectory) {
  const manifestPath = join(root, 'manifest.json')
  const candidates = readFile(manifestPath, 'utf8').then(text => JSON.parse(text).map(item => ({
    name: `sample-${item.id}`,
    description: item.description,
    invocation: { modelInvocable: true, userInvocable: true },
    provider: providerName,
    source: 'bundled',
    rank: bundledSkillRank,
    resourceBase: { kind: 'directory', path: join(root, item.id) },
    locator: join(root, item.id, 'SKILL.md'),
  })))

  ctx.skills.registerProvider(() => ({
    name: providerName,
    async list() { return candidates },
    async get(selected, options = {}) {
      const candidate = (await candidates).find(item => item.name === selected.name)
      if (!candidate) return undefined
      const source = await readFile(candidate.locator, { encoding: 'utf8', signal: options.signal })
      const { rank: _rank, locator: _locator, ...summary } = candidate
      return { ...summary, content: source.replace(/^---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/u, '').trim() }
    },
  }))
}
