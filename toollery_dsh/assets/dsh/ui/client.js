window.__ModuleLoader__.load({
  id: '@local/toollery-ui',
  factory(require) {
    const React = require('react')
    const h = React.createElement
    const stores = new Map()
    let conversationService
    // Browser-only presentation preference; never change retrieval settings.
    const collapsedKey = 'toollery.panel.collapsed'
    const displayListeners = new Set()
    let panelCollapsed = false
    try { panelCollapsed = window.localStorage.getItem(collapsedKey) === 'true' } catch { /* Storage may be disabled. */ }
    function subscribeDisplay(listener) {
      displayListeners.add(listener)
      return () => displayListeners.delete(listener)
    }
    function togglePanel() {
      panelCollapsed = !panelCollapsed
      try { window.localStorage.setItem(collapsedKey, String(panelCollapsed)) } catch { /* Keep the in-memory preference. */ }
      displayListeners.forEach(listener => listener())
    }
    function usePanelCollapsed() {
      return React.useSyncExternalStore(subscribeDisplay, () => panelCollapsed, () => false)
    }
    async function request(session_id, options, action, details) {
      const response = await fetch('/toollery/ui', {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id, ...(options === undefined ? {} : { options }), ...(action ? { action } : {}), ...details }),
      })
      const body = await response.json()
      if (!response.ok) throw new Error(body.error || 'Toollery is unavailable')
      return body
    }
    function storeFor(id) {
      if (!stores.has(id)) {
        const store = { value: { data: null, busy: false, error: '', pendingSkillGate: true }, listeners: new Set(), timer: null, pending: false, sequence: 0 }
        store.publish = value => { store.value = { ...store.value, ...value }; store.listeners.forEach(fn => fn()) }
        store.refresh = async () => {
          if (store.pending || store.value.busy) return
          store.pending = true
          const sequence = store.sequence
          try { const data = await request(id); if (sequence === store.sequence) store.publish({ data, error: '', pendingSkillGate: false }) }
          catch (error) { if (sequence === store.sequence) store.publish({ error: error.message, pendingSkillGate: false }) }
          finally { store.pending = false }
        }
        store.configure = async (options, action, details) => {
          if (store.value.busy) return
          const current = store.value.data
          const target = options?.retrieval_target ?? current?.retrieval_target
          const representation = options?.skill_representation ?? current?.skill_representation
          const enabled = options?.enabled ?? current?.enabled
          const pendingSkillGate = action === 'prepare_skills' || action === 'check_skills'
            || (!!enabled && ['skills', 'both'].includes(target) && representation !== 'spec')
          if (pendingSkillGate) conversationService?.blocks?.set(id, {
            reason: 'Toollery is checking or preparing skill manuals. Please wait before starting the conversation.',
          })
          else if (options?.enabled === false || options?.skill_representation === 'spec'
              || options?.retrieval_target === 'tools') conversationService?.blocks?.set(id, undefined)
          store.sequence++
          store.publish({ busy: true, error: '', pendingSkillGate })
          try { store.publish({ data: await request(id, options, action, details), pendingSkillGate: false }); return true }
          catch (error) { store.publish({ error: error.message }); return false }
          finally { store.publish({ busy: false }) }
        }
        store.subscribe = fn => {
          store.listeners.add(fn)
          if (!store.timer) { store.refresh(); store.timer = setInterval(store.refresh, 1500) }
          return () => { store.listeners.delete(fn); if (!store.listeners.size) { clearInterval(store.timer); store.timer = null } }
        }
        stores.set(id, store)
      }
      return stores.get(id)
    }
    function useToollery(id) {
      const store = storeFor(id)
      const value = React.useSyncExternalStore(store.subscribe, () => store.value)
      return { ...value, configure: store.configure }
    }
    const panel = { border: '1px solid var(--border-color, #8885)', borderRadius: 10, padding: '10px 12px', fontSize: 12, color: 'inherit', background: 'var(--background-secondary, transparent)' }
    const input = { color: 'inherit', background: 'var(--background-primary, transparent)', border: '1px solid #8886', borderRadius: 6, padding: '3px 6px', fontSize: 12 }
    function formatDuration(seconds) {
      if (!Number.isFinite(seconds) || seconds < 0) return null
      const rounded = Math.max(1, Math.round(seconds))
      if (rounded < 60) return `${rounded}s`
      const minutes = Math.floor(rounded / 60)
      const rest = rounded % 60
      return rest ? `${minutes}m ${rest}s` : `${minutes}m`
    }
    function progressText(data) {
      if (!data || !['preparing', 'refreshing'].includes(data.phase)) return null
      const done = Number.isFinite(data.completed) ? data.completed : 0
      const total = Number.isFinite(data.total) ? data.total : 0
      const elapsed = formatDuration(data.elapsed_seconds)
      const eta = formatDuration(data.eta_seconds)
      const count = total ? `${done}/${total}` : `${done}`
      return `Manual preparation: ${count} · elapsed ${elapsed || '—'} · ${eta ? `about ${eta} remaining` : 'estimating remaining time…'}`
    }
    function skillProgressText(data) {
      if (!data || data.skill_phase !== 'preparing') return null
      const done = Number.isFinite(data.skill_completed) ? data.skill_completed : 0
      const total = Number.isFinite(data.skill_total) ? data.skill_total : (data.skill_manual_count || 0)
      const elapsed = formatDuration(data.skill_elapsed_seconds)
      const eta = formatDuration(data.skill_eta_seconds)
      return `Skill manual preparation: ${done}/${total} · elapsed ${elapsed || '—'} · ${eta ? `about ${eta} remaining` : 'estimating remaining time…'}`
    }

    function SkillInitialization({ data, busy, configure }) {
      const target = data?.retrieval_target
      const count = Number(data?.skill_manual_count || 0)
      const phase = data?.skill_phase
      const skillRunning = phase === 'preparing'
      const prepared = data?.skill_manuals_prepared === true
      const inScope = ['skills', 'both'].includes(target) && data?.skill_representation !== 'spec'
      if (!inScope) return null
      if (prepared) return h('div', { role: 'status', style: { marginTop: 10 } },
        `Skill manuals are ready (${data.skill_manuals_valid || 0}/${count}). You can start the conversation.`)
      if (phase === 'preflight_required') return h('div', { role: 'status', style: { marginTop: 10 } },
        'Checking the native skill-manual cache locally…')
      if (phase === 'checking') return h('div', { role: 'status', style: { marginTop: 10 } },
        'Checking the native skill-manual cache locally…')
      if (phase === 'preparing') return h('div', { role: 'status', style: { marginTop: 10 } },
        `Preparing ${data.skill_missing || 'missing'} skill manuals. The composer will unlock when preparation finishes.`)
      if (count < 1 && phase !== 'preflight_failed') return null
      const missing = Number(data?.skill_missing ?? Math.max(0, count - (data?.skill_manuals_valid || 0)))
      const needs = ['needs_preparation', 'approval_required', 'preflight_failed'].includes(phase)
        || (count > 0 && !prepared)
      if (!needs) return null
      return h('div', { style: { marginTop: 10 } },
        h('strong', null, 'Skill manuals'),
        h('p', null, `${data.skill_manuals_valid || 0} cached / ${count} native skills; ${missing} need generation. Each manual creates three intent queries. Skill contents are sent to the configured generation service only after you click below; API charges may apply. They are not added to chat context.`),
        data.skill_error && h('p', { role: 'alert', style: { color: '#c35d40' } }, data.skill_error),
        phase === 'preflight_failed' && h('button', { style: input, disabled: busy || skillRunning,
          onClick: () => configure(undefined, 'check_skills') }, 'Retry local cache check'),
        h('button', { style: input, disabled: busy || skillRunning || missing < 1,
          onClick: () => configure(undefined, 'prepare_skills') }, `Generate ${missing} missing skill manuals — may incur API charges`))
    }
    function Initialization({ data, busy, configure }) {
      const generator = data?.generator
      const hasToolTarget = data?.retrieval_target !== 'skills'
      const [allowDownload, setAllowDownload] = React.useState(false)
      const [model, setModel] = React.useState('deepseek-flash')
      const [url, setUrl] = React.useState('https://api.deepseek.com')
      const [key, setKey] = React.useState('')
      const [consent, setConsent] = React.useState(false)
      const [saved, setSaved] = React.useState(false)
      React.useEffect(() => {
        if (generator?.editable) { setModel(generator.model); setUrl(generator.api_url) }
      }, [generator?.model, generator?.api_url, generator?.editable])
      React.useEffect(() => { setConsent(false) }, [data?.phase, generator?.model, generator?.api_url])
      if (!data?.managed || data.phase === 'unsupported') return null
      const running = busy || ['preparing', 'refreshing', 'setting_up', 'starting'].includes(data.phase)
        || data.skill_phase === 'preparing'
      const unsaved = !!generator?.editable && (model !== generator.model || url !== generator.api_url || !!key)
      return h('div', { style: { marginTop: 10 } },
        data.phase === 'needs_setup' && h('div', null,
          h('strong', null, '1. Prepare the local worker'),
          h('p', null, 'Use an existing Python 3.12+ installation, or allow a private runtime download. DSH and its models are unchanged. No API calls are made.'),
          h('label', { style: { display: 'block', marginBottom: 8 } }, h('input', { type: 'checkbox', checked: allowDownload,
            disabled: busy, onChange: e => setAllowDownload(e.target.checked) }), ' Allow private Python download if needed (Windows x64; internet required)'),
          h('button', { style: input, disabled: busy, onClick: () => configure(undefined, 'setup', { setup_options: { allow_download: allowDownload } }) }, 'Set up local worker')),
        generator && h('details', { open: !generator.configured || data.phase === 'needs_preparation', style: { marginTop: 10 } },
          h('summary', { style: { cursor: 'pointer' } }, '2. Manual-generation model'),
          h('p', null, 'Uses OpenAI Chat Completions, independently of your DSH chat model. Saving settings makes no API calls.'),
          !generator.editable && h('p', null, 'A custom generator is configured. Saving this form replaces it with the built-in API bridge.'),
          h('form', { autoComplete: 'off', onSubmit: async e => {
            e.preventDefault(); setSaved(false)
            if (await configure(undefined, 'save_generator', { generator_settings: { model, api_url: url, api_key: key } })) { setKey(''); setSaved(true) }
          }, style: { display: 'grid', gap: 8, maxWidth: 620 } },
          h('label', null, 'Model ', h('input', { required: true, 'aria-label': 'Manual model', style: { ...input, width: '100%', boxSizing: 'border-box' },
            value: model, maxLength: 200, disabled: running, onChange: e => { setModel(e.target.value); setSaved(false) } })),
          h('label', null, 'API base URL ', h('input', { required: true, type: 'url', 'aria-label': 'Manual API URL', style: { ...input, width: '100%', boxSizing: 'border-box' },
            value: url, disabled: running, onChange: e => { setUrl(e.target.value); setSaved(false) } })),
          h('label', null, 'API key ', h('input', { type: 'password', autoComplete: 'new-password', 'aria-label': 'Manual API key',
            placeholder: generator.key_configured ? 'Key configured — leave blank to retain' : 'Enter your manual-generation key',
            style: { ...input, width: '100%', boxSizing: 'border-box' }, value: key, maxLength: 4096, disabled: running,
            onChange: e => { setKey(e.target.value); setSaved(false) } })),
          h('small', null, 'Saved locally in your DSH profile. The saved key is never returned to this form. This is local file storage, not an OS keychain.'),
          h('button', { type: 'submit', style: input, disabled: running }, 'Save manual settings'),
          saved && h('span', { role: 'status' }, 'Settings saved. The cache is being checked; no API calls were made.'))),
        data.session_required && h('p', null, '3. Start a new conversation with native tool presentation. Its Toollery panel will show the tool pool and let you confirm manual generation.'),
        (hasToolTarget && (['needs_preparation', 'approval_required', 'refresh_failed'].includes(data.phase)
          || (data.phase === 'ready' && data.manuals_prepared === false)) && h('div', { style: { marginTop: 10 } },
          h('strong', null, '3. Prepare manuals'),
          h('p', null, ['needs_preparation', 'ready'].includes(data.phase)
            ? `${data.manuals_valid} cached / ${data.tool_count} tools. Up to ${Math.max(0, data.tool_count - data.manuals_valid)} manuals need generation, with three proxy queries each. Provider retries can add requests. Tool specifications are sent to the manual provider.`
            : `${data.missing ?? 'Missing'} manuals need generation for the updated tool pool${data.pending_tool_count ? ` (${data.pending_tool_count} tools)` : ''}. Tool specifications are sent to the manual provider; retries can add charges. When ready, resubmit your message; it is not automatically replayed.`),
          unsaved && h('p', { role: 'status' }, 'Save the edited manual settings before generating.'),
          h('label', { style: { display: 'block', marginBottom: 8 } }, h('input', { type: 'checkbox', checked: consent, disabled: busy,
            onChange: e => setConsent(e.target.checked) }), ' I allow API calls and possible charges to generate missing or changed manuals.'),
          h('button', { style: input, disabled: busy || !consent || unsaved || (generator?.editable && !generator.configured),
            onClick: () => { setConsent(false); configure(undefined, 'prepare') } }, 'Generate missing manuals (uses API)'))),
        data.phase === 'ready' && !data.session_required && h('p', { role: 'status' },
          data.manuals_prepared === false ? 'Spec retrieval is ready. Prepare manuals before switching to a manual-based mode.'
            : `Ready: all ${data.tool_count} manuals are cached. ${data.enabled ? 'Toollery is ON for the next turn.' : 'Switch ON when you want to try retrieval.'}`))
    }
    function PluginSetup() {
      const { data, busy, error, configure } = useToollery(undefined)
      return h('section', { 'aria-label': 'Toollery initialization', style: panel },
        h('h3', { style: { marginTop: 0 } }, 'Toollery setup'),
        h('p', null, 'Uses your existing DSH chat model and tools. Manual generation has its own model and credentials.'),
        data && h('p', { role: 'status' }, `Worker: ${data.phase}${data.message ? ' — ' + data.message : ''}`),
        progressText(data) && h('p', { role: 'status', style: { opacity: .8 } }, progressText(data)),
        ['setting_up', 'starting', 'refreshing'].includes(data?.phase) && h('progress', { 'aria-label': 'Toollery worker setup', style: { width: '100%' } }),
        h(Initialization, { data, busy, configure }),
        data?.configuration_directory && h('small', { style: { overflowWrap: 'anywhere' } }, `Local data: ${data.configuration_directory}`),
        error && h('p', { role: 'alert', style: { color: '#c35d40' } }, error))
    }
    function Controls({ sessionId }) {
      const { data, busy, error, pendingSkillGate, configure } = useToollery(sessionId)
      const collapsed = usePanelCollapsed()
      const manualSkillMode = !!data?.enabled && ['skills', 'both'].includes(data?.retrieval_target)
        && data?.skill_representation !== 'spec'
      const blockReason = pendingSkillGate ? 'Toollery is checking or preparing skill manuals. Please wait before starting the conversation.'
        : manualSkillMode && data?.skill_manuals_prepared !== true
          ? data?.skill_phase === 'preparing' ? 'Skill manuals are being prepared. Please wait before starting the conversation.'
            : 'Approve skill-manual generation in the Toollery panel before starting the conversation.'
          : undefined
      React.useEffect(() => {
        if (!sessionId) return
        const blocks = conversationService?.blocks
        if (!blocks?.set) return
        blocks.set(sessionId, blockReason ? { reason: blockReason } : undefined)
        return () => blocks.set(sessionId, undefined)
      }, [sessionId, blockReason])
      const [draftToolK, setDraftToolK] = React.useState(String(data?.tool_k ?? data?.k ?? 3))
      const [draftSkillK, setDraftSkillK] = React.useState(String(data?.skill_k ?? data?.k ?? 3))
      React.useEffect(() => {
        if (data) {
          setDraftToolK(String(data.tool_k ?? data.k ?? 3))
          setDraftSkillK(String(data.skill_k ?? data.k ?? 3))
        }
      }, [data?.tool_k, data?.skill_k, data?.k])
      const status = data ? data.enabled ? 'ON' : 'OFF' : 'Loading'
      const current = data?.last_selection
      const target = data?.retrieval_target || 'tools'
      const currentTarget = current?.retrieval_target || target
      const currentToolK = current?.budget === 'all' ? 'all' : (current?.tool_k ?? current?.k ?? null)
      const currentSkillK = current?.budget === 'all' ? 'all' : (current?.skill_k ?? current?.k ?? null)
      const nextToolK = data?.budget === 'all' ? 'all' : (data?.tool_k ?? data?.k)
      const nextSkillK = data?.budget === 'all' ? 'all' : (data?.skill_k ?? data?.k)
      const bothTargets = target === 'both'
      const hasCurrent = !!current && (currentTarget === 'both'
        ? currentToolK !== null && currentSkillK !== null
        : currentTarget === 'skills' ? currentSkillK !== null : currentToolK !== null)
      const settingFor = (which, toolK, skillK) => which === 'both'
        ? `tool k=${toolK} · skill k=${skillK}` : `k=${which === 'skills' ? skillK : toolK}`
      const settingText = !hasCurrent
        ? `Next turn: ${settingFor(target, nextToolK, nextSkillK)}`
        : `Current turn (${currentTarget}): ${settingFor(currentTarget, currentToolK, currentSkillK)} · Next turn: ${settingFor(target, nextToolK, nextSkillK)}`
      const validK = kind => {
        const isSkill = kind === 'skill'
        const value = Number(isSkill ? draftSkillK : draftToolK)
        return Number.isInteger(value) && value >= 1
          && (isSkill || data?.retrieval_target === 'skills' || value <= data?.tool_count)
      }
      const changedK = kind => Number(kind === 'skill' ? draftSkillK : draftToolK)
        !== (kind === 'skill' ? data?.skill_k ?? data?.k : data?.tool_k ?? data?.k)
      const kControl = kind => {
        const isSkill = kind === 'skill'
        const label = bothTargets ? `${isSkill ? 'Skill' : 'Tool'} k` : 'k'
        const draft = isSkill ? draftSkillK : draftToolK
        const setDraft = isSkill ? setDraftSkillK : setDraftToolK
        const key = isSkill ? 'skill_k' : 'tool_k'
        const field = h('label', { key: `${kind}-k-label` }, `${label} `, h('input', { 'aria-label': `Toollery ${bothTargets ? `${kind} ` : isSkill ? 'skill ' : ''}k`, type: 'number', min: 1,
            max: !isSkill && data?.retrieval_target !== 'skills' ? data?.tool_count || undefined : undefined,
            value: draft, disabled: busy || !data?.enabled || data?.budget !== 'top_k',
            style: { ...input, width: 52 }, onChange: e => setDraft(e.target.value) }))
        if (bothTargets) return [field]
        return [field, h('button', { key: `${kind}-k-apply`, style: input, disabled: busy || !data?.enabled || data?.budget !== 'top_k'
          || !validK(kind) || !changedK(kind),
          onClick: () => configure({ [key]: Number(draft) }) }, 'Apply k')]
      }
      const notice = error || data?.error || data?.skill_error
        || (['approval_required', 'refresh_failed', 'needs_setup', 'needs_preparation', 'unsupported'].includes(data?.phase) ? data.phase : '')
        || (['approval_required', 'needs_preparation'].includes(data?.skill_phase) ? `skill ${data.skill_phase}` : '')
      return h('section', { 'aria-label': 'Toollery controls', style: collapsed
        ? { marginTop: 4, display: 'flex', justifyContent: 'flex-end' } : { ...panel, marginTop: 8 } },
        h('button', { type: 'button', hidden: !collapsed, 'aria-label': 'Expand Toollery panel', 'aria-expanded': false,
          title: `Toollery ${status}${notice ? ' — Needs attention' : ''}. Expand controls.`, onClick: togglePanel,
          style: { ...input, display: collapsed ? 'inline-flex' : 'none', alignItems: 'center', gap: 6,
            padding: '5px 8px', cursor: 'pointer', fontWeight: 700 } },
          h('svg', { width: 16, height: 16, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor',
            strokeWidth: 1.8, 'aria-hidden': true }, h('path', { d: 'M3 5h18l-7 8v6l-4 2v-8Z' })),
          h('span', { style: { color: data?.enabled ? 'var(--color-success, #19865b)' : 'inherit' } }, status),
          notice && h('span', { 'aria-label': 'Toollery needs attention', style: { color: '#c35d40' } }, '!')),
        // Keep controls mounted so draft fields and preparation consent survive hiding.
        h('div', { hidden: collapsed, style: { display: collapsed ? 'none' : undefined } },
        h('div', { style: { display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' } },
          h('strong', null, 'Toollery'),
          h('button', { role: 'switch', 'aria-label': 'Enable Toollery', 'aria-checked': !!data?.enabled,
            disabled: busy || !data || (!data.enabled && data.retrieval_target !== 'skills' && data.phase && !data.retrieval_ready), onClick: () => configure({ enabled: !data.enabled }),
            style: { ...input, fontWeight: 700, color: data?.enabled ? '#19865b' : 'inherit' } }, data ? data.enabled ? 'ON' : 'OFF' : 'Loading'),
          ['tools', 'both'].includes(data?.retrieval_target) && h('label', null, 'Tool rep ', h('select', { 'aria-label': 'Toollery tool rep', style: input,
            value: data?.representation || 'spec_manual', disabled: busy || !data || !data.enabled,
            onChange: e => configure({ representation: e.target.value }) },
          ...['spec', 'manual', 'spec_manual'].map(value => h('option', { key: value, value }, value)))),
          ['skills', 'both'].includes(data?.retrieval_target) && data?.skill_representation && h('label', null, 'Skill rep ', h('select', { 'aria-label': 'Toollery skill rep', style: input,
            value: data?.skill_representation || 'spec_manual', disabled: busy || !data || !data.enabled,
            onChange: e => configure({ skill_representation: e.target.value }) },
          ...['spec', 'manual', 'spec_manual'].map(value => h('option', { key: value, value }, value)))),
          h('label', null, 'Budget ', h('select', { 'aria-label': 'Toollery budget', style: input,
            value: data?.budget || 'top_k', disabled: busy || !data || !data.enabled,
            onChange: e => configure({ budget: e.target.value }) },
          h('option', { value: 'top_k' }, 'top-k'), h('option', { value: 'all' }, 'all'))),
          h('label', null, 'Target ', h('select', { 'aria-label': 'Toollery retrieval target', style: input,
            value: data?.retrieval_target || 'tools', disabled: busy || !data,
            onChange: e => configure({ retrieval_target: e.target.value }) },
          h('option', { value: 'tools' }, 'tools'), h('option', { value: 'skills' }, 'skills'),
          h('option', { value: 'both' }, 'both'))),
          ...(data?.retrieval_target === 'skills' ? kControl('skill')
            : data?.retrieval_target === 'both' ? [...kControl('tool'), ...kControl('skill'),
              h('button', { key: 'both-k-apply', style: input,
                disabled: busy || !data?.enabled || data?.budget !== 'top_k'
                  || !validK('tool') || !validK('skill') || (!changedK('tool') && !changedK('skill')),
                onClick: () => configure({ tool_k: Number(draftToolK), skill_k: Number(draftSkillK) }) }, 'Update k')]
              : kControl('tool')),
          h('button', { type: 'button', 'aria-label': 'Collapse Toollery panel', 'aria-expanded': true,
            title: 'Hide controls and turn cards. Retrieval keeps its current setting.', onClick: togglePanel,
            style: { ...input, marginLeft: 'auto', display: 'inline-flex', padding: 5, cursor: 'pointer' } },
            h('svg', { width: 16, height: 16, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor',
              strokeWidth: 2, 'aria-hidden': true }, h('path', { d: 'm6 15 6-6 6 6' })))),
        h('div', { role: 'status', style: { marginTop: 6, opacity: .75 } }, !data ? 'Loading session settings...' : busy ? 'Applying settings...' : data.enabled
          ? `Changes apply to the next turn. ${settingText}. Target: ${data.retrieval_target || 'tools'}. ${data.retrieval_target === 'skills' ? 'Skill retrieval keeps the full host-eligible tool pool.' : 'The current selection stays fixed.'}`
          : "OFF: the next turn bypasses Toollery retrieval. DSH's host-eligible tools and native skill catalog remain available; host restrictions still apply."),
        data?.phase && h('div', { style: { marginTop: 8 } },
          h('div', null, `Status: ${data.phase} · Tool manuals ${data.manuals_valid}/${data.tool_count}${['skills', 'both'].includes(data.retrieval_target) ? ` · Skill manuals ${data.skill_manuals_valid || 0}/${data.skill_manual_count || 0}` : ''}`),
          progressText(data) && h('div', { role: 'status', style: { opacity: .8, marginTop: 4 } }, progressText(data)),
          skillProgressText(data) && h('div', { role: 'status', style: { opacity: .8, marginTop: 4 } }, skillProgressText(data)),
          data.skill_phase === 'preparing' && h('progress', { 'aria-label': 'Skill manual preparation progress',
            max: data.skill_total || 1, ...(data.skill_total ? { value: data.skill_completed || 0 } : {}),
            style: { width: '100%', marginTop: 6 } }),
          ['preparing', 'refreshing', 'setting_up', 'starting'].includes(data.phase) && h('progress', {
            'aria-label': 'Toollery preparation progress', max: data.total || 1,
            ...(['preparing', 'refreshing'].includes(data.phase) && data.total ? { value: data.completed || 0 } : {}), style: { width: '100%', marginTop: 6 } }),
          data.error && h('p', { style: { margin: '6px 0', overflowWrap: 'anywhere' } }, data.error),
          data.configuration_directory && h('div', { style: { opacity: .75, overflowWrap: 'anywhere' } }, `Configuration: ${data.configuration_directory}`),
          h(Initialization, { data, busy, configure }),
          h(SkillInitialization, { data, busy, configure })),
        error && h('div', { role: 'alert', style: { color: '#c35d40', marginTop: 4 } }, error)))
    }
    function ToolList({ title, tools }) {
      return h('details', { style: { marginTop: 8 } }, h('summary', { style: { cursor: 'pointer' } }, title),
        tools.length ? h('ul', { style: { paddingLeft: 18, margin: '6px 0' } }, ...tools.map(tool =>
          h('li', { key: tool.name, style: { marginBottom: 5, overflowWrap: 'anywhere' } },
            h('code', null, tool.name), h('span', { style: { opacity: .75 } }, ` — ${tool.description || 'No description'}`))))
          : h('p', { style: { opacity: .65 } }, 'No tools in this list.'))
    }
    function TurnCard({ sessionId, turn }) {
      const { data } = useToollery(sessionId)
      const collapsed = usePanelCollapsed()
      const record = data?.turns?.filter(item => item.turn === turn.turn).at(-1)
      if (!record) return null // Never invent records for older, unobserved turns.
      const pool = record.tool_details || []
      const selected = pool.filter(tool => record.selected_names.includes(tool.name))
      const calls = record.calls || []
      const calledNames = [...new Set(calls.map(call => call.name))]
      const errors = calls.filter(call => call.status === 'error').length
      return h('section', { 'aria-label': `Toollery tool selection for turn ${record.turn}`, hidden: collapsed,
        style: { ...panel, margin: '8px 0', display: collapsed ? 'none' : undefined } },
        h('div', { style: { display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' } },
          h('strong', null, `Toollery · ${record.enabled ? 'ON' : 'OFF'}`),
          h('span', { style: { opacity: .7 } }, record.enabled ? `${record.retrieval_target || 'tools'} · ${record.retrieval_target === 'skills' ? 'skills: ' + (record.skill_representation || 'spec') : record.retrieval_target === 'both' ? 'tools: ' + record.representation + ' / skills: ' + (record.skill_representation || 'spec') : record.representation} · ${record.budget === 'all' ? 'all' : record.retrieval_target === 'both' ? 'tool k=' + (record.tool_k ?? record.k) + ' / skill k=' + (record.skill_k ?? record.k) : 'k=' + (record.retrieval_target === 'skills' ? record.skill_k ?? record.k : record.tool_k ?? record.k)} · ${(record.retrieval_target === 'skills' ? record.skill_retrieval_ms || 0 : record.retrieval_ms).toFixed(2)} ms` : 'Full pool; retrieval bypassed')),
        h('p', { style: { margin: '8px 0 2px', fontSize: 13 } },
          `Tool pool ${record.full_pool_count}${record.eligible_pool_count === undefined ? '' : ' → Allowed ' + record.eligible_pool_count} → Selected ${record.selected_names.length} → Called ${calledNames.length} tools`,
          h('span', { style: { opacity: .65, fontSize: 12 } }, ` (${calls.length} calls${errors ? ", " + errors + " errors" : ""})`)),
        record.skill_selected_names && h('p', { style: { margin: '6px 0 2px' } },
          `Skills ${Number.isFinite(record.skill_candidate_count) ? record.skill_candidate_count + ' → ' : ''}selected (${record.skill_selected_names.length})${Number.isFinite(record.skill_retrieval_ms) ? ` · ${record.skill_retrieval_ms.toFixed(2)} ms` : ''}: `,
          h('code', null, record.skill_selected_names.join(', ') || 'none')),
        h(ToolList, { title: `Selected tools (${selected.length})`, tools: selected }),
        h(ToolList, { title: `Called tools (${calledNames.length})`, tools: pool.filter(tool => calledNames.includes(tool.name)) }),
        h(ToolList, { title: `Full tool pool (${pool.length})`, tools: pool }))
    }
    return {
      inject: ['slots', 'conversation'],
      apply(ctx) {
        conversationService = ctx.conversation
        ctx.slots.inject('plugins.bundle.config', () => ctx.slots.register({
          name: 'plugins.bundle.config', key: '@jkl-agents/dsh-toollery',
        }, PluginSetup))
        ctx.slots.inject('conversation.composer.dock', () => ctx.slots.register({
          name: 'conversation.composer.dock', id: 'toollery-controls', order: 10,
        }, Controls))
        ctx.slots.inject('conversation.chat.turnTail', () => ctx.slots.register({
          name: 'conversation.chat.turnTail', id: 'toollery-turn-card', order: 10,
        }, TurnCard))
      },
    }
  },
})
