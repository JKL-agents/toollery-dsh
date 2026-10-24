import { spawn } from 'node:child_process'
import { existsSync, readFileSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { randomUUID, createHash } from 'node:crypto'
import { setTimeout as delay } from 'node:timers/promises'
import { isDeepStrictEqual } from 'node:util'
import { bundledWorker, setupWorker, workerVersion } from './setup-worker.mjs'

function isTimeout(error) {
  return error?.name === 'TimeoutError' || error?.code === 'ETIMEDOUT'
    || /timed out|timeout/i.test(error?.message || '')
}

// Effect-scoped by the owning Cordis plugin, never shared through process.env.
export class ToolleryService {
  constructor(directory, config = {}) {
    this.directory = resolve(directory)
    this.config = config
    this.sessions = new Map()
    this.phase = 'needs_setup'
    this.message = 'Set up the local worker. This does not generate manuals or change DSH models.'
    this.refreshTimeoutMs = Number.isFinite(config.refreshTimeoutMs)
      ? Math.max(1000, Math.min(1800000, config.refreshTimeoutMs)) : 120000
    this.child = null
    this.transport = null
    this.disposed = false
  }

  async start() {
    if (this.disposed || this.child) return
    this.phase = 'needs_setup'
    const file = join(this.directory, 'worker.json')
    if (!existsSync(file)) return
    let executable, version, wheel_sha256
    try { ({ python: executable, version, wheel_sha256 } = JSON.parse(readFileSync(file, 'utf8'))) } catch { /* Report a local setup error below. */ }
    if (typeof executable !== 'string' || !executable) {
      this.message = 'Invalid worker.json. Run worker setup again.'
      return
    }
    if (version !== workerVersion) {
      this.message = 'Worker version does not match this plugin. Run worker setup again; cache and configuration are preserved.'
      return
    }
    try {
      if (wheel_sha256 !== bundledWorker().sha256) {
        this.message = 'Worker content does not match this plugin. Run worker setup again; cache and configuration are preserved.'
        return
      }
    } catch {
      this.message = 'Bundled worker wheel is unavailable. Reinstall the plugin package, then run worker setup.'
      return
    }
    this.phase = 'starting'
    const child = spawn(executable, ['-I', '-u', '-m', 'toollery_dsh.worker', '--directory', this.directory], {
      windowsHide: true, cwd: this.directory, stdio: ['pipe', 'pipe', 'pipe'],
      // Manual credentials are inherited as normal environment variables;
      // bridge credentials are created in the child, not placed in this env.
    })
    this.child = child
    child.stdin.on('error', () => {})
    child.stderr.on('data', () => {})
    const lines = createInterface({ input: child.stdout })
    try {
      await new Promise((done, fail) => {
        const timer = setTimeout(() => fail(new Error('Worker startup timed out.')), 15000)
        const settle = fn => value => { clearTimeout(timer); fn(value) }
        child.once('error', settle(() => fail(new Error('Cannot start the configured worker. Run setup again.'))))
        child.once('exit', settle(() => fail(new Error('Worker exited during startup. Check local configuration.'))))
        lines.once('line', settle(line => {
          try {
            const handshake = JSON.parse(line)
            if (handshake.protocol !== 1 || !/^http:\/\/127\.0\.0\.1:\d+$/.test(handshake.url)
                || typeof handshake.token !== 'string' || !handshake.token) throw new Error()
            if (this.child !== child || this.disposed) throw new Error()
            this.transport = handshake
            done()
          } catch { fail(new Error('Invalid worker handshake.')) }
        }))
      })
      if (this.disposed) return this.stop()
      this.phase = 'ready'
      this.message = ''
      child.on('exit', () => {
        if (this.child !== child) return
        this.transport = null
        this.child = null
        this.phase = 'needs_setup'
        this.message = 'Worker stopped. Restart DSH or run worker setup. Turn Toollery OFF to continue without retrieval.'
      })
      for (const [id, state] of this.sessions) if (state.schemas) await this.capture(id)
    } catch (error) {
      if (this.child !== child) return
      await this.stop()
      this.phase = 'needs_setup'
      this.message = error.message
    }
  }

  async setup(options = {}) {
    if (this.disposed) throw new Error('Toollery has been unloaded.')
    if (this.setupJob) return this.setupJob
    this.setupJob = (async () => {
      await this.stop()
      this.phase = 'setting_up'
      try {
        await setupWorker(this.directory, this.config.python || process.env.TOOLLERY_PYTHON,
          message => { this.message = message }, { ...options, desktopResources: this.config.desktopResources })
        if (this.disposed) return
        await this.start()
      } catch (error) { this.phase = 'needs_setup'; this.message = error.message }
      finally { this.setupJob = null }
    })()
    return this.setupJob
  }

  stop() {
    const child = this.child
    this.transport = null
    this.child = null
    if (!child || child.exitCode !== null) return Promise.resolve()
    return new Promise(done => {
      child.stdin.end() // EOF lets Python close its HTTP server before exiting.
      const timer = setTimeout(() => { child.kill(); done() }, 3000)
      child.once('exit', () => { clearTimeout(timer); done() })
    })
  }

  dispose() { this.disposed = true; this.sessions.clear(); return this.stop() }
  enabled(id) { return this.sessions.get(id)?.enabled === true }
  snapshotHash(id) { return this.sessions.get(id)?.snapshot_hash }
  poolRevision(id) { return this.sessions.get(id)?.pool_revision || 0 }
  unregister(id) {
    this.sessions.delete(id)
    if (this.transport) void this.post('/release', { session_id: id }).catch(() => {})
  }
  offFile(id) { return join(this.directory, 'overrides', createHash('sha256').update(id).digest('hex') + '.off') }
  unsupported(id, reason) {
    this.sessions.set(id, { enabled: false, phase: 'unsupported', error: reason, schemas: null, turns: [] })
  }
  async register(id, schemas) {
    this.sessions.set(id, { schemas: structuredClone(schemas), enabled: false, turns: [], localTurns: [],
      localOff: existsSync(this.offFile(id)), phase: 'needs_preparation', pool_revision: 0,
      retrieval_target: 'tools' })
    if (this.transport) await this.capture(id)
    return {}
  }
  async capture(id) {
    const state = this.sessions.get(id)
    if (!state?.schemas) return
    try {
      const snapshot = await this.post('/snapshot', { session_id: id, schemas: state.schemas,
        discovery: { complete: true, source: 'host-agent-scope' } })
      Object.assign(state, snapshot)
      if (state.localOff) await this.post('/configure', { session_id: id, options: { enabled: false } })
      delete state.error
    } catch { state.error = 'Could not capture this tool snapshot. Restart DSH to retry.' }
  }

  async post(path, payload, signal, { timeoutMs } = {}) {
    if (!this.transport) throw new Error('Toollery worker is unavailable. Turn Toollery OFF or check worker setup.')
    const timeout = AbortSignal.timeout(timeoutMs ?? (path.startsWith('/refresh') ? this.refreshTimeoutMs : 15000))
    const response = await fetch(this.transport.url + path, {
      method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + this.transport.token },
      body: JSON.stringify(payload), signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
    })
    if (!response.ok) {
      let detail = {}
      try { detail = await response.json() } catch { /* Keep the generic message below. */ }
      const error = new Error(`Toollery ${path} failed (${response.status}). Check preparation and session status.`)
      error.code = detail.error
      error.detail = detail
      throw error
    }
    return response.json()
  }

  async settings(id) {
    const state = this.sessions.get(id)
    if (!state) throw new Error('This session is not managed by Toollery. Start a new native conversation.')
    if (this.transport && state.snapshot_hash) {
      try {
        const status = await this.post('/status', { session_id: id })
        Object.assign(state, status)
        state.error = status.error
        if (state.localOff) state.enabled = false
      } catch (error) { if (state.enabled) throw error }
    }
    return state
  }

  async status(id) {
    let generator = null
    try { if (this.transport) generator = (await this.post('/generator', {})).generator } catch { /* OFF and setup remain usable if the worker fails. */ }
    if (!id) return { phase: this.phase, message: this.message, configuration_directory: this.directory,
      generator, managed: true, session_required: true }
    const state = this.sessions.get(id)
    if (!state) throw new Error('Start a new native conversation to use Toollery.')
    let error
    try { await this.settings(id) } catch (failure) { error = failure.message }
    const turns = [...(state.turns || []), ...(state.localTurns || [])].sort((a, b) => (a.turn ?? 0) - (b.turn ?? 0))
    return {
      enabled: state.enabled, ready: state.retrieval_target === 'skills' || !state.enabled || !!state.retrieval_ready,
      retrieval_ready: !!state.retrieval_ready, representation: state.representation || 'spec_manual',
      budget: state.budget || 'top_k', k: state.k || 3,
      retrieval_target: state.retrieval_target || 'tools',
      tool_count: state.tool_count ?? state.schemas?.length ?? 0,
      tool_names: state.tool_names ?? state.schemas?.map(tool => tool.name) ?? [],
      manuals_valid: state.manuals_valid || 0, manuals_prepared: state.manuals_prepared,
      turns, last_selection: turns.at(-1),
      phase: state.phase === 'unsupported' ? 'unsupported' : this.phase === 'ready' ? state.phase : this.phase,
      error: error || (this.phase !== 'ready' ? this.message : state.error) || '', completed: state.completed, total: state.total,
      missing: state.missing, pending_tool_count: state.pending_tool_count,
      elapsed_seconds: state.elapsed_seconds, eta_seconds: state.eta_seconds,
      rate_per_second: state.rate_per_second,
      configuration_directory: this.directory,
      generator, managed: true,
    }
  }

  async saveGenerator(settings) {
    const result = await this.post('/generator', { settings })
    if (!result.ok) throw new Error(result.error || 'Manual settings could not be saved.')
    // Worker invalidates/rechecks each session's indexes without API calls.
    for (const [id, state] of this.sessions) if (state.schemas) await this.settings(id)
    return result.generator
  }

  async configure(id, options) {
    const state = this.sessions.get(id)
    if (!state || state.phase === 'unsupported') throw new Error('Toollery is not available for this agent.')
    if (Object.keys(options).length === 1 && options.enabled === false) {
      state.enabled = false
      state.localOff = true
      mkdirSync(join(this.directory, 'overrides'), { recursive: true })
      writeFileSync(this.offFile(id), '')
      // OFF remains effective without a healthy worker. Persist if reachable.
      try { await this.post('/configure', { session_id: id, options }) } catch { /* Local OFF stays effective. */ }
      return
    }
    await this.post('/configure', { session_id: id, options })
    if (options.enabled === true) {
      delete state.localOff
      rmSync(this.offFile(id), { force: true })
    }
    Object.assign(state, await this.post('/status', { session_id: id }))
    if (state.localOff) state.enabled = false
  }

  async prepare(id) {
    const state = this.sessions.get(id)
    if (!state?.schemas || !state.snapshot_hash) throw new Error('Set up the worker and capture a native tool pool first.')
    return this.post('/prepare', { session_id: id, allow_generation: true })
  }

  /**
   * Rank a provider-owned discovery batch without changing the DSH host pool.
   * The worker keeps generation jobs alive when this request's caller times out;
   * a matching retry polls the same immutable candidate snapshot.
   */
  async rankCandidates(id, prompt, candidates, options = {}, signal) {
    const state = this.sessions.get(id)
    if (!state) throw new Error('This session is not managed by Toollery.')
    if (!Array.isArray(candidates) || candidates.length < 1) {
      throw new Error('Discovery candidates must be a non-empty array.')
    }
    if (typeof prompt !== 'string' || !prompt.trim()) throw new Error('A discovery prompt is required.')
    const payload = {
      session_id: id,
      prompt,
      candidates: structuredClone(candidates),
      representation: options.representation ?? state.representation ?? 'spec_manual',
      budget: options.budget ?? state.budget ?? 'top_k',
      k: options.k ?? state.k ?? 3,
      max_auto_tools: options.maxAutoTools ?? options.max_auto_tools ?? state.refresh_max_misses ?? 15,
      allow_generation: options.allowGeneration === true || options.allow_generation === true,
      request_id: options.requestId || undefined,
    }
    let result = await this.post('/discover', payload, signal, { timeoutMs: options.timeoutMs ?? 120000 })
    if (result.status === 'approval_required' || result.status === 'ranked' || result.status === 'failed') return result
    if (result.status !== 'accepted' || typeof result.job_id !== 'string') {
      throw new Error('Toollery returned an unknown discovery state.')
    }
    const deadline = Date.now() + (options.timeoutMs ?? 120000)
    while (true) {
      if (Date.now() > deadline) {
        const error = new Error('Discovery preparation is still running. Retry to reuse the existing job.')
        error.code = 'discovery_pending'
        error.detail = { job_id: result.job_id, candidate_snapshot_hash: result.candidate_snapshot_hash }
        throw error
      }
      await delay(100, undefined, { signal })
      const status = await this.post('/discover-status', { session_id: id, job_id: result.job_id }, signal,
        { timeoutMs: Math.min(15000, Math.max(1000, deadline - Date.now())) })
      if (status.phase === 'preparing') continue
      if (status.phase !== 'completed') throw new Error('Toollery returned an unknown discovery job state.')
      result = status.result
      if (result?.status === 'failed') {
        const error = new Error('Discovery manual preparation failed. Check the model, endpoint and API key.')
        error.code = result.error || 'discovery_failed'
        error.detail = result
        throw error
      }
      return result
    }
  }

  discoverCandidates(...args) { return this.rankCandidates(...args) }

  /**
   * Rank DSH skill metadata without loading skill bodies or generating manuals.
   * The host remains responsible for loading the selected skills through its
   * normal skill service after this metadata-only projection.
   */
  async rankSkills(id, prompt, summaries, options = {}, signal) {
    if (!Array.isArray(summaries)) throw new Error('Skill summaries must be an array.')
    return this.post('/skill-search', { session_id: id, prompt, candidates: summaries,
      budget: options.budget || 'top_k', k: options.k || 3 }, signal)
  }

  async recordSkillSelection(payload) {
    const state = this.sessions.get(payload.session_id)
    const local = state?.localTurns?.find(item => item.selection_id === payload.selection_id)
    if (local) {
      Object.assign(local, { skill_selected_names: [...(payload.selected_names || [])],
        skill_candidate_count: payload.candidate_count, skill_retrieval_ms: payload.retrieval_ms })
      return
    }
    await this.post('/skill-observed', payload)
  }

  async reconcile(id, source, signal, { maxAutoTools, allowGeneration = false, representation,
    timeoutMs = this.refreshTimeoutMs } = {}) {
    const state = this.sessions.get(id)
    if (!state) throw new Error('This session is not managed by Toollery.')
    if (state.enabled && state.retrieval_ready === false) {
      throw new Error('Toollery manuals and indexes are not ready. Prepare the tool pool first.')
    }
    const startedAt = Date.now()
    const deadline = startedAt + timeoutMs
    let latestJob
    const pendingError = (job, targetCount) => {
      const error = new Error('Manual refresh is still running. Retry to reuse its progress, or turn Toollery OFF and resubmit the prompt.')
      error.code = 'refresh_pending'
      error.detail = {
        ...(job?.job_id ? { job_id: job.job_id } : {}),
        ...(job?.snapshot_hash ? { snapshot_hash: job.snapshot_hash } : {}),
        ...(Number.isFinite(job?.completed) ? { completed: job.completed } : {}),
        ...(Number.isFinite(job?.total) ? { total: job.total } : targetCount ? { total: targetCount } : {}),
        ...(Number.isFinite(job?.elapsed_seconds) ? { elapsed_seconds: job.elapsed_seconds }
          : { elapsed_seconds: (Date.now() - startedAt) / 1000 }),
        ...(Number.isFinite(job?.eta_seconds) ? { eta_seconds: job.eta_seconds } : {}),
        retryable: true,
        generation_cancelled: false,
      }
      return error
    }
    for (let attempt = 0; attempt < 2; attempt++) {
      const schemas = structuredClone(typeof source === 'function' ? await source() : source)
      const request = {
        session_id: id, schemas, snapshot_hash: state.snapshot_hash,
        pool_revision: state.pool_revision || 0, max_auto_tools: maxAutoTools ?? state.refresh_max_misses ?? 15,
        allow_generation: allowGeneration, representation,
      }
      let result
      try {
        result = await this.post('/refresh', request, signal,
          { timeoutMs: Math.max(1000, deadline - Date.now()) })
      } catch (error) {
        // A request timeout does not cancel the worker's generation job. Probe
        // once with the same immutable request so the caller can receive its
        // job identity and ETA instead of accidentally starting a duplicate.
        if (error.code === 'stale_refresh') {
          if (attempt === 1) { error.code = 'unstable_tool_pool'; throw error }
          await this.settings(id)
          continue
        }
        if (!isTimeout(error) || signal?.aborted) throw error
        try {
          result = await this.post('/refresh', request, undefined, { timeoutMs: 5000 })
        } catch (probeError) {
          if (probeError.code === 'stale_refresh' && attempt === 0) {
            await this.settings(id)
            continue
          }
          if (isTimeout(probeError)) {
            throw pendingError(latestJob, schemas.length)
          }
          if (probeError.code === 'stale_refresh') probeError.code = 'unstable_tool_pool'
          throw probeError
        }
      }
      if (result.status === 'approval_required') {
        const error = new Error(`${result.missing} tools require manual generation. Approve in Toollery's panel, wait until ready, then resubmit; or turn Toollery OFF and resubmit.`)
        error.code = 'approval_required'
        error.detail = result
        throw error
      }
      if (result.status === 'accepted') {
        latestJob = { job_id: result.job_id, snapshot_hash: result.snapshot_hash,
          completed: 0, total: schemas.length }
        let job
        do {
          if (Date.now() >= deadline) throw pendingError(latestJob, schemas.length)
          await delay(100, undefined, signal ? { signal } : undefined)
          try {
            job = await this.post('/refresh-status', { session_id: id, job_id: result.job_id }, signal,
              { timeoutMs: Math.min(15000, Math.max(1000, deadline - Date.now())) })
          } catch (error) {
            if (!isTimeout(error) || signal?.aborted) throw error
            throw pendingError(latestJob, schemas.length)
          }
          latestJob = { ...latestJob, ...job }
        } while (job.phase === 'refreshing')
        if (job.phase === 'stale_refresh') {
          if (attempt === 1) {
            const error = new Error('Tool pool kept changing during refresh.')
            error.code = 'unstable_tool_pool'
            throw error
          }
          await this.settings(id)
          continue
        }
        if (job.phase === 'failed') {
          const error = new Error('Manual refresh failed. Check the model, endpoint and API key.')
          error.code = 'refresh_failed'
          error.detail = job
          throw error
        }
        result = job.result || result
      }
      if (result.status === 'committed' || result.status === 'unchanged') {
        Object.assign(state, { snapshot_hash: result.snapshot_hash, pool_revision: result.pool_revision,
          schemas, tool_count: schemas.length, tool_names: schemas.map(tool => tool.name),
          phase: 'ready', retrieval_ready: true, error: undefined })
        if (typeof source === 'function' && !isDeepStrictEqual(schemas, await source())) {
          if (attempt === 0) continue
          const error = new Error('Tool pool kept changing during refresh. Retry once sources have stabilized.')
          error.code = 'unstable_tool_pool'
          throw error
        }
        result.schemas = schemas
        return result
      }
      throw new Error('Toollery returned an unknown refresh state.')
    }
    throw new Error('Tool pool kept changing during refresh.')
  }

  bypass(id, schemas, turn, options = {}, hostSchemas) {
    const state = this.sessions.get(id)
    const record = { session_id: id, selection_id: randomUUID(), turn, enabled: options.enabled === true,
      representation: options.representation || state.representation || 'spec_manual',
      budget: options.budget || state.budget || 'top_k', k: options.k || state.k || 3,
      retrieval_target: options.retrieval_target || state.retrieval_target || 'tools',
      selected_names: schemas.map(tool => tool.name), model_tool_names: schemas.map(tool => tool.name),
      full_pool_count: new Set([...(hostSchemas || state.schemas), ...schemas].map(tool => tool.name)).size,
      eligible_pool_count: schemas.length, retrieval_ms: 0,
      tool_details: schemas.map(({ name, description }) => ({ name, description })), calls: [] }
    state.localTurns.push(record)
    state.last_selection = record
    return record
  }

  async recordCall(payload) {
    const record = this.sessions.get(payload.session_id)?.localTurns?.find(turn => turn.selection_id === payload.selection_id)
    if (!record) return this.post('/call', payload)
    const existing = record.calls.find(call => call.call_id === payload.call_id)
    if (existing) Object.assign(existing, payload)
    else record.calls.push({ ...payload })
  }
}
