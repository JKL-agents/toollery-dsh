// The catalog remains metadata-only in model history. Toollery may ask the
// native host loader for body content solely to build local retrieval manuals;
// the body is never projected back into the model-facing catalog.

const escapeText = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
export const catalogDescription = value => {
  const normalized = String(value).replaceAll(/\s+/g, ' ').trim()
  return normalized.length <= 500 ? normalized : `${normalized.slice(0, 497)}...`
}

export function skillCandidates(summaries) {
  if (!Array.isArray(summaries)) return []
  return summaries
    .filter(skill => skill && typeof skill.name === 'string' && typeof skill.description === 'string')
    .filter(skill => skill.invocation?.modelInvocable !== false)
    .map(skill => ({
      name: skill.name,
      description: catalogDescription(skill.description),
      ...(typeof skill.whenToUse === 'string' ? { when_to_use: skill.whenToUse } : {}),
      ...(skill.metadata && typeof skill.metadata === 'object' ? { tags: skill.metadata.tags } : {}),
    }))
}

export async function loadSkillCandidates(summaries, { skills, workingDirectory, agent, signal, cache,
  loadBodies = true } = {}) {
  const candidates = skillCandidates(summaries)
  if (!loadBodies) return candidates
  // Unit adapters and older non-native hosts may not expose the loader. Keep
  // their spec-only path usable; manual mode will report missing bodies.
  if (!skills || typeof skills.get !== 'function') return candidates
  const cwd = workingDirectory?.ensure ? await workingDirectory.ensure(agent, signal) : undefined
  const loaded = []
  for (const candidate of candidates) {
    const definition = await skills.get(candidate.name, { cwd, signal, scope: agent })
    if (!definition || typeof definition.content !== 'string' || !definition.content.trim()) {
      throw new Error(`The native loader could not load skill content for ${candidate.name}.`)
    }
    const record = { ...candidate, content: definition.content }
    cache?.set(candidate.name, { content: definition.content })
    loaded.push(record)
  }
  return loaded
}

export function catalogMessage(messages) {
  return messages.find(message => message?.source?.kind === 'skill-catalog'
    && Array.isArray(message.source.entries)
    && message.source.entries.every(entry => entry && typeof entry.name === 'string'
      && typeof entry.description === 'string'))
}

// Use admitted, still-visible history rather than a process-local "published"
// flag: a failed/cancelled pre-step may never have reached model history.
export function visibleSkillCatalog(agent) {
  const session = agent.session
  if (!session?.surface || typeof session.eventAt !== 'function') return undefined
  const visible = new Set(session.surface.nodes)
  for (let index = session.seq - 1; index >= 0; index--) {
    const event = session.eventAt(index)
    if (event?.type === 'user/message' && visible.has(event.seq)) {
      const catalog = catalogMessage([event.data])
      if (catalog) return catalog
    }
  }
}

function catalogText(entries, update) {
  const lines = update
    ? ['<system-reminder>',
      'The available skill catalog changed. This complete catalog replaces every earlier available-skills list in this session:',
      '', '<available_skills>', ...entries.map(entry => `- \`${entry.name}\`: ${escapeText(entry.description)}`),
      '</available_skills>', '',
      ...(entries.length === 0
        ? ['No skills are currently available through the `skill` tool. Do not use names from earlier skill catalogs.',
          'A user may still invoke a skill directly; its <skill_content> block then appears in this conversation. Follow it, and do not call the `skill` tool for it.']
        : ['Use only names in this replacement catalog. If the user names a listed skill, or the task clearly matches its description, call the `skill` tool with the exact skill name before acting.',
          'A user may also invoke a skill directly; its <skill_content> block then appears in this conversation. Follow it, and do not call the `skill` tool again for that skill.']),
      '</system-reminder>']
    : ['<system-reminder>',
      'A skill is a reusable set of task-specific instructions. The following skills are available in this session:',
      '', '<available_skills>', ...entries.map(entry => `- \`${entry.name}\`: ${escapeText(entry.description)}`),
      '</available_skills>', '',
      'If the user names a skill, or the task clearly matches a skill\'s description, call the `skill` tool with the exact skill name before taking task actions. Load all applicable skills, then follow their full instructions. This catalog contains summaries only; do not infer or follow a skill\'s instructions until it has been loaded.',
      'A user may also invoke a skill directly; its <skill_content> block then appears in this conversation. Follow it, and do not call the `skill` tool again for that skill.',
      '</system-reminder>']
  return lines.join('\n')
}

export function projectSkillCatalog(message, selectedNames) {
  if (!message) return message
  const selected = new Set(selectedNames)
  const entries = message.source.entries.filter(entry => selected.has(entry.name))
  const content = Array.isArray(message.content) ? message.content.map((block, index) =>
    index === 0 && block?.type === 'text' ? { ...block, text: catalogText(entries, message.source.update === true) } : block) : message.content
  return { ...message, content, source: { ...message.source, entries, toolleryProjected: true } }
}
