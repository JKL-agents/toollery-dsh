'use strict'
const token = new URLSearchParams(location.hash.slice(1)).get('token') || ''
const form = document.querySelector('#settings')
const errorBox = document.querySelector('#error')
let loaded = false, demoWindow = null, lastUrl = '', pending = false
async function api(path, value) {
  const response = await fetch('/api/' + path, {
    method: value === undefined ? 'GET' : 'POST',
    headers: { 'X-Toollery-Token': token, 'Content-Type': 'application/json' },
    ...(value === undefined ? {} : { body: JSON.stringify(value) }),
    cache: 'no-store',
  })
  const result = await response.json()
  if (!response.ok) throw new Error(result.error || 'Request failed.')
  return result
}
function updateFields() {
  const custom = form.elements.pool.value === 'custom'
  document.querySelector('#custom-row').hidden = !custom
  document.querySelector('#api-section').hidden = custom || form.elements.offline.checked
  form.elements.k.disabled = !form.elements.enabled.checked || form.elements.budget.value === 'all'
}
function show(data) {
  if (!loaded) {
    for (const [name, value] of Object.entries(data.settings)) {
      const field = form.elements[name]
      if (field) { if (field.type === 'checkbox') field.checked = value; else field.value = value }
    }
    loaded = true
    updateFields()
  }
  document.querySelector('#chat-key-status').textContent = data.chat_key_configured ? 'A key is configured locally.' : 'No key configured yet.'
  document.querySelector('#manual-key-status').textContent = data.manual_key_configured ? 'A key is configured locally.' : 'Uses the chat key if left blank.'
  document.querySelector('#phase').textContent = { idle: 'Ready to configure', starting: 'Starting…', ready: 'Demo running', stopped: 'Demo stopped', error: 'Attention needed' }[data.phase] || data.phase
  document.querySelector('#message').textContent = data.message + (data.port ? ' Web port: ' + data.port + '.' : '')
  const link = document.querySelector('#demo-link')
  link.hidden = !data.url
  if (data.url) {
    link.href = data.url
    if (data.url !== lastUrl) {
      lastUrl = data.url
      if (demoWindow && !demoWindow.closed) demoWindow.location = data.url
    }
  }
  const active = data.phase === 'starting' || data.phase === 'ready'
  document.querySelector('#start').disabled = active || pending
  document.querySelector('#stop').disabled = !active || pending
}
form.addEventListener('change', updateFields)
form.addEventListener('submit', async event => {
  event.preventDefault()
  errorBox.textContent = ''
  pending = true
  document.querySelector('#start').disabled = true
  const body = {}
  for (const field of form.elements) {
    if (!field.name) continue
    body[field.name] = field.type === 'checkbox' ? field.checked : field.type === 'number' ? Number(field.value) : field.value
  }
  // Open from the user's click so a delayed dependency install does not trigger a popup block.
  demoWindow = window.open('about:blank', 'toollery-demo')
  if (demoWindow) { demoWindow.opener = null; demoWindow.document.title = 'Preparing Toollery demo'; demoWindow.document.body.textContent = 'Preparing DSH. This tab will open the demo when ready. Keep the setup page and terminal open.' }
  try {
    const data = await api('start', body)
    form.elements.chat_key.value = ''; form.elements.manual_key.value = ''
    pending = false
    show(data)
  } catch (error) {
    if (demoWindow && !demoWindow.closed) demoWindow.close()
    errorBox.textContent = error.message
  } finally { pending = false }
})
document.querySelector('#stop').addEventListener('click', async () => {
  errorBox.textContent = ''
  try { pending = true; show(await api('stop', {})) } catch (error) { errorBox.textContent = error.message }
  finally { pending = false }
})
async function refresh() {
  try { show(await api('status')) }
  catch (error) { errorBox.textContent = error.message + ' Keep the launcher terminal open.' }
  setTimeout(refresh, 1500)
}
refresh()
