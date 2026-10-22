window.__ModuleLoader__.load({
  id: '@local/toollery-ui',
  factory(require) {
    const React = require('react')
    const h = React.createElement
    const stores = new Map()
    async function request(session_id, options) {
      const response = await fetch('/toollery/ui', {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id, ...(options === undefined ? {} : { options }) }),
      })
      const body = await response.json()
      if (!response.ok) throw new Error(body.error || 'Toollery is unavailable')
      return body
    }
    function storeFor(id) {
      if (!stores.has(id)) {
        const store = { value: { data: null, busy: false, error: '' }, listeners: new Set(), timer: null, pending: false, sequence: 0 }
        store.publish = value => { store.value = { ...store.value, ...value }; store.listeners.forEach(fn => fn()) }
        store.refresh = async () => {
          if (store.pending || store.value.busy) return
          store.pending = true
          const sequence = store.sequence
          try { const data = await request(id); if (sequence === store.sequence) store.publish({ data, error: '' }) }
          catch (error) { if (sequence === store.sequence) store.publish({ error: error.message }) }
          finally { store.pending = false }
        }
        store.configure = async options => {
          if (store.value.busy) return
          store.sequence++
          store.publish({ busy: true, error: '' })
          try { store.publish({ data: await request(id, options) }) }
          catch (error) { store.publish({ error: error.message }) }
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
    function Controls({ sessionId }) {
      const { data, busy, error, configure } = useToollery(sessionId)
      const [draftK, setDraftK] = React.useState('3')
      React.useEffect(() => { if (data) setDraftK(String(data.k)) }, [data?.k])
      return h('section', { 'aria-label': 'Toollery controls', style: { ...panel, marginTop: 8 } },
        h('div', { style: { display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' } },
          h('strong', null, 'Toollery'),
          h('button', { role: 'switch', 'aria-label': 'Enable Toollery', 'aria-checked': !!data?.enabled,
            disabled: busy || !data, onClick: () => configure({ enabled: !data.enabled }),
            style: { ...input, fontWeight: 700, color: data?.enabled ? '#19865b' : 'inherit' } }, data ? data.enabled ? 'ON' : 'OFF' : 'Loading'),
          h('label', null, 'Representation ', h('select', { 'aria-label': 'Toollery representation', style: input,
            value: data?.representation || 'spec_manual', disabled: busy || !data || !data.enabled,
            onChange: e => configure({ representation: e.target.value }) },
          ...['spec', 'manual', 'spec_manual'].map(value => h('option', { key: value, value }, value)))),
          h('label', null, 'Budget ', h('select', { 'aria-label': 'Toollery budget', style: input,
            value: data?.budget || 'top_k', disabled: busy || !data || !data.enabled,
            onChange: e => configure({ budget: e.target.value }) },
          h('option', { value: 'top_k' }, 'top-k'), h('option', { value: 'all' }, 'all'))),
          h('label', null, 'k ', h('input', { 'aria-label': 'Toollery k', type: 'number', min: 1, max: data?.tool_count || undefined,
            value: draftK, disabled: busy || !data?.enabled || data?.budget !== 'top_k',
            style: { ...input, width: 52 }, onChange: e => setDraftK(e.target.value) })),
          h('button', { style: input, disabled: busy || !data?.enabled || data?.budget !== 'top_k'
            || !Number.isInteger(Number(draftK)) || Number(draftK) < 1 || Number(draftK) > data?.tool_count || Number(draftK) === data?.k,
            onClick: () => configure({ k: Number(draftK) }) }, 'Apply k'),
          h('span', { style: { opacity: .65 } }, data ? `Full pool: ${data.tool_count}` : 'Waiting for session')),
        h('div', { role: 'status', style: { marginTop: 6, opacity: .75 } }, !data ? 'Loading session settings...' : busy ? 'Applying settings...' : data.enabled
          ? 'Changes apply to the next turn. The current selection stays fixed.' : 'OFF: the next turn receives the full tool pool without retrieval.'),
        error && h('div', { role: 'alert', style: { color: '#c35d40', marginTop: 4 } }, error))
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
      const record = data?.turns?.filter(item => item.turn === turn.turn).at(-1)
      if (!record) return null // Never invent records for older, unobserved turns.
      const pool = record.tool_details || []
      const selected = pool.filter(tool => record.selected_names.includes(tool.name))
      const calls = record.calls || []
      const calledNames = [...new Set(calls.map(call => call.name))]
      const errors = calls.filter(call => call.status === 'error').length
      return h('section', { 'aria-label': `Toollery tool selection for turn ${record.turn}`, style: { ...panel, margin: '8px 0' } },
        h('div', { style: { display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' } },
          h('strong', null, `Toollery · ${record.enabled ? 'ON' : 'OFF'}`),
          h('span', { style: { opacity: .7 } }, record.enabled ? `${record.representation} · ${record.budget === 'all' ? 'all' : 'k=' + record.k} · ${record.retrieval_ms.toFixed(2)} ms` : 'Full pool; retrieval bypassed')),
        h('p', { style: { margin: '8px 0 2px', fontSize: 13 } },
          `Pool ${record.full_pool_count} → Selected ${record.selected_names.length} → Called ${calledNames.length} tools`,
          h('span', { style: { opacity: .65, fontSize: 12 } }, ` (${calls.length} calls${errors ? ", " + errors + " errors" : ""})`)),
        h(ToolList, { title: `Selected tools (${selected.length})`, tools: selected }),
        h(ToolList, { title: `Called tools (${calledNames.length})`, tools: pool.filter(tool => calledNames.includes(tool.name)) }),
        h(ToolList, { title: `Full tool pool (${pool.length})`, tools: pool }))
    }
    return {
      inject: ['slots'],
      apply(ctx) {
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
