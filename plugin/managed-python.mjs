// Optional, user-authorized Windows runtime. No PATH or machine-wide changes.
import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, statSync, renameSync, rmSync, existsSync } from 'node:fs'
import { join } from 'node:path'

const UV_URL = 'https://github.com/astral-sh/uv/releases/download/0.9.6/uv-x86_64-pc-windows-msvc.zip'
const UV_SHA256 = '4cde4a7907214053320d5ab4b6f9add77b7aca6eb39cc7e4928b61704c1328b1'

export async function managedPython(home, run, progress) {
  if (process.platform !== 'win32' || process.arch !== 'x64') {
    throw new Error('Automatic Python download currently supports Windows x64. Install Python 3.12+ with venv/pip, then retry setup.')
  }
  const tools = join(home, 'runtime')
  mkdirSync(tools, { recursive: true })
  const uv = join(tools, 'uv.exe')
  if (!existsSync(uv)) {
    progress('Downloading the private runtime manager from GitHub (no API calls)')
    const archive = join(tools, 'uv.zip')
    const extracted = join(tools, 'uv-extracted')
    const powershell = join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
    try {
      // Use the Windows network stack, including its system proxy settings.
      // Node fetch can time out where the user's browser/PowerShell succeeds.
      try {
        await run(powershell, ['-NoProfile', '-NonInteractive', '-Command',
          "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; Invoke-WebRequest -UseBasicParsing -Uri $env:TOOLLERY_UV_URL -OutFile $env:TOOLLERY_UV_ARCHIVE -TimeoutSec 120"],
        { cwd: home, timeout: 135000, env: { ...process.env, TOOLLERY_UV_URL: UV_URL, TOOLLERY_UV_ARCHIVE: archive } })
      } catch { throw new Error('Private runtime download failed. Check GitHub access and Windows proxy settings, then retry setup.') }
      if (statSync(archive).size > 32 * 1024 * 1024) throw new Error('Runtime download exceeded its size limit.')
      if (createHash('sha256').update(readFileSync(archive)).digest('hex') !== UV_SHA256) throw new Error('Runtime download checksum mismatch. Retry setup.')
      // Paths are environment values, never interpolated into shell code.
      await run(powershell, ['-NoProfile', '-NonInteractive', '-Command',
        'Expand-Archive -LiteralPath $env:TOOLLERY_UV_ARCHIVE -DestinationPath $env:TOOLLERY_UV_EXTRACTED -Force'],
      { cwd: home, env: { ...process.env, TOOLLERY_UV_ARCHIVE: archive, TOOLLERY_UV_EXTRACTED: extracted } })
      renameSync(join(extracted, 'uv.exe'), uv)
    } finally { rmSync(archive, { force: true }); rmSync(extracted, { recursive: true, force: true }) }
  }
  const env = { ...process.env, UV_PYTHON_INSTALL_DIR: join(tools, 'python'), UV_CACHE_DIR: join(tools, 'cache'),
    UV_PYTHON_DOWNLOADS: 'automatic', UV_NO_CONFIG: '1' }
  progress('Preparing private Python 3.12.12; the first download may take several minutes')
  const venv = join(home, 'venv')
  if (!existsSync(join(venv, 'Scripts', 'python.exe'))) {
    await run(uv, ['--no-config', 'venv', '--managed-python', '--python', '3.12.12', venv], { cwd: home, env, timeout: 600000 })
  }
  return { executable: join(venv, 'Scripts', 'python.exe'), uv, env }
}
