// Explicit user action, never an npm install/prepare hook. No shell commands.
import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, readdirSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { managedPython } from './managed-python.mjs'

export const workerVersion = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8')).version

function bundledPython(desktopResources) {
  if (typeof desktopResources !== 'string' || !desktopResources.trim()) return null
  const roots = [
    resolve(desktopResources),
    resolve(desktopResources, 'office-runtime'),
  ]
  for (const runtime of roots) {
    const root = join(runtime, 'primary-runtime', 'dependencies', 'python')
    const executable = process.platform === 'win32' ? join(root, 'python.exe') : join(root, 'bin', 'python3')
    if (existsSync(executable)) return { executable, root, runtime }
  }
  return null
}

export function run(command, args, options = {}) {
  return new Promise((done, fail) => {
    const { timeout = 120000, ...spawnOptions } = options
    const child = spawn(command, args, { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'], ...spawnOptions })
    let output = ''
    child.stdout?.on('data', chunk => { if (output.length < 8192) output += chunk })
    child.stderr?.on('data', () => {}) // Provider/configuration paths must not leak through errors.
    const timer = setTimeout(() => { child.kill(); fail(new Error('Worker setup timed out. Check the network/Python environment and retry.')) }, timeout)
    child.once('error', () => { clearTimeout(timer); fail(new Error('Cannot start the setup executable. Check Python or retry the private runtime download.')) })
    child.once('close', code => {
      clearTimeout(timer)
      code === 0 ? done(output.trim()) : fail(new Error('Worker setup failed. Check Python 3.12+, venv/pip support, and the bundled wheel.'))
    })
  })
}

export async function setupWorker(directory, python, progress = () => {}, { allow_download = false, desktopResources } = {}) {
  const home = resolve(directory)
  mkdirSync(home, { recursive: true })
  const lock = join(home, 'setup.lock')
  try { mkdirSync(lock) } catch { throw new Error('Worker setup is already running. If an earlier setup was interrupted, remove setup.lock and retry.') }
  try {
    let interpreter
    const bundled = !python && bundledPython(desktopResources)
    if (bundled) {
      try {
        await run(bundled.executable, ['-I', '-c', 'import sys; assert sys.version_info >= (3,12)'], { cwd: home, timeout: 10000 })
        interpreter = [bundled.executable]
      } catch { /* Fall through to a user-installed interpreter. */ }
    }
    const candidates = python ? [[python]] : process.platform === 'win32'
      ? [['py', '-3.12'], ['py', '-3'], ['python']] : [['python3'], ['python']]
    for (const candidate of candidates) {
      if (interpreter) break
      try {
        await run(candidate[0], [...candidate.slice(1), '-I', '-c', 'import sys; assert sys.version_info >= (3,12)'], { cwd: home, timeout: 10000 })
        interpreter = candidate
        break
      } catch { /* Try the next documented system interpreter. */ }
    }
    if (!interpreter && python) throw new Error('The configured Python executable is unavailable. Correct the plugin python option before retrying.')
    if (!interpreter && !allow_download) throw new Error('Python 3.12+ was not found. Enable the private Python download and retry, or install Python with venv/pip.')
    const managed = !interpreter ? await managedPython(home, run, progress) : null
    const useBundled = Boolean(bundled && interpreter?.[0] === bundled.executable)
    const venv = join(home, 'venv')
    const executable = join(venv, process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
    progress('Creating the profile-local Python environment')
    if (!existsSync(executable)) {
      if (useBundled) await run(interpreter[0], ['-I', '-m', 'venv', '--without-pip', venv], { cwd: home })
      else await run(interpreter[0], [...interpreter.slice(1), '-I', '-m', 'venv', venv], { cwd: home })
    }
    await run(executable, ['-I', '-c', 'import sys; assert sys.version_info >= (3,12)'], { cwd: home })
    const wheelDir = fileURLToPath(new URL('./worker/', import.meta.url))
    const wheels = readdirSync(wheelDir).filter(name => /^toollery_dsh-.*-py3-none-any\.whl$/.test(name))
    if (wheels.length !== 1) throw new Error('The plugin must contain exactly one worker wheel. Use a built release bundle.')
    progress('Installing the bundled worker (offline; no DSH runtime)')
    // Do not let checkout metadata, PYTHONPATH or user pip configuration redirect
    // installation. Reinstall even at the same version during explicit setup.
    if (managed) {
      await run(managed.uv, ['--no-config', 'pip', 'install', '--python', executable, '--no-index', '--no-deps', '--reinstall', join(wheelDir, wheels[0])], { cwd: home, env: managed.env })
    } else if (useBundled) {
      // Desktop's primary runtime intentionally omits ensurepip. Its bundled
      // pip can still install the wheel offline into the private venv.
      const sitePackages = process.platform === 'win32'
        ? join(venv, 'Lib', 'site-packages')
        : join(venv, 'lib', 'python3.12', 'site-packages')
      await run(interpreter[0], ['-I', '-m', 'pip', '--isolated', 'install', '--no-index', '--no-deps', '--target', sitePackages, '--force-reinstall', join(wheelDir, wheels[0])], { cwd: home })
    } else {
      // A previous setup may have created a private uv venv without pip.
      try { await run(executable, ['-I', '-m', 'ensurepip'], { cwd: home }) } catch { /* pip below gives the supported setup diagnostic. */ }
      await run(executable, ['-I', '-m', 'pip', '--isolated', 'install', '--no-index', '--no-deps', '--force-reinstall', join(wheelDir, wheels[0])], { cwd: home })
    }
    await run(executable, ['-I', '-c',
      'import sys; from pathlib import Path; from importlib.metadata import version; import toollery_dsh.worker as w; assert version("toollery-dsh") == sys.argv[2]; assert Path(w.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()); w.initialize(sys.argv[1])',
      home, workerVersion], { cwd: home })
    const pending = join(home, 'worker.json.tmp')
    writeFileSync(pending, JSON.stringify({ python: executable, version: workerVersion }, null, 2) + '\n')
    renameSync(pending, join(home, 'worker.json'))
    return executable
  } finally { rmSync(lock, { recursive: true }) }
}
