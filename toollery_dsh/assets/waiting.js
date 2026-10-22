'use strict'
const token = new URLSearchParams(location.hash.slice(1)).get('token') || ''
const stages = ['checking', 'installing', 'starting']
const progress = document.querySelector('#progress')
let finished = false

function render(data) {
  const ready = data.phase === 'ready' && data.url
  const stopped = data.phase === 'error' || data.phase === 'stopped'
  document.querySelector('#message').textContent = data.message
  const seconds = Math.max(0, Math.floor(data.elapsed_seconds || 0))
  document.querySelector('#elapsed').textContent = 'Elapsed: ' + Math.floor(seconds / 60) + 'm ' + seconds % 60 + 's'
  const current = ready ? stages.length : stages.indexOf(data.stage)
  for (const [index, stage] of stages.entries()) {
    const item = document.querySelector('#' + stage)
    item.classList.toggle('done', index < current)
    if (index === current) item.setAttribute('aria-current', 'step')
    else item.removeAttribute('aria-current')
  }
  const packages = data.packages
  document.querySelector('#detail').textContent = data.stage === 'installing'
    ? packages
      ? `Packages: ${packages.resolved} resolved · ${packages.downloaded} downloaded · ${packages.reused} reused · ${packages.added} installed. Counts may increase as dependencies are discovered.`
      : 'Downloading or preparing dependencies. First use may take several minutes; subsequent starts reuse this installation.'
    : data.stage === 'starting' ? 'Web dependencies are ready. Waiting for the DSH server to start.'
      : ready ? 'DSH is ready. Opening your demo…' : 'Checking the local Web runtime and configuration.'
  const recovery = document.querySelector('#recovery')
  recovery.hidden = !stopped
  if (stopped) {
    progress.hidden = true
    recovery.textContent = data.phase === 'error'
      ? 'Startup failed. Return to the setup page to review settings and retry Start demo.'
      : 'The demo was stopped. Return to the setup page to start it again.'
    document.querySelector('#hint').textContent = 'Keep the terminal open while using the setup page.'
  } else {
    progress.hidden = false
    recovery.textContent = ''
    if (ready) progress.value = 1
    else progress.removeAttribute('value')
  }
  if (ready) {
    // Only follow the local authenticated server URL supplied by the launcher.
    const target = new URL(data.url)
    if (target.protocol !== 'http:' || target.hostname !== '127.0.0.1' || target.port !== String(data.port) || !target.searchParams.get('token')) {
      throw new Error('Invalid demo address. Return to the setup page.')
    }
    finished = true
    location.replace(target.href)
  }
}

async function refresh() {
  try {
    const response = await fetch('/api/status', { headers: { 'X-Toollery-Token': token }, cache: 'no-store' })
    if (!response.ok) throw new Error('Cannot read launcher status. Reopen the demo from the setup page.')
    render(await response.json())
  } catch (error) {
    const recovery = document.querySelector('#recovery')
    recovery.hidden = false
    recovery.textContent = error.message + ' Keep the launcher terminal open; this page will retry.'
  }
  if (!finished) setTimeout(refresh, 1500)
}
refresh()
