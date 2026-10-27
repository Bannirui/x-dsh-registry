/**
 * 随插件生命周期管理 Python 子智能体服务。
 *
 * 插件加载时（Web 启动）先探测 ``/health``：已有人工启动的服务就直接复用；
 * 否则拉起 ``server/.venv/bin/python -m src``，插件卸载/Web 退出时收回进程。
 */

import { spawn, type ChildProcess } from 'node:child_process'
import { closeSync, existsSync, mkdirSync, openSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export interface PythonServiceConfig {
  /** 是否在插件加载时自动拉起；默认 true */
  autoStart?: boolean
  /** 服务目录（含 src/__main__.py）；默认依次尝试插件同级的 ../server、cwd/server */
  dir?: string
  /** Python 可执行文件；默认使用服务目录下的 .venv */
  executable?: string
  /** 等待 /health 就绪的超时秒数；默认 60 */
  startupTimeout?: number
  /** 服务日志文件；默认 $DSH_HOME/x-agents.log */
  logFile?: string
}

export interface PythonServiceOptions {
  /** Python 服务基址（插件配置里的 baseUrl） */
  baseUrl: string
  config?: PythonServiceConfig
  logger: {
    info(message: string): void
    warn(message: string): void
  }
  dshHome?: string
}

const LOOPBACK_HOSTS = new Set(['127.0.0.1', 'localhost', '::1', '0.0.0.0', '::'])

function delay(ms: number): Promise<void> {
  return new Promise((done) => setTimeout(done, ms))
}

function parseServiceUrl(raw: string): { local: boolean; port: number } {
  try {
    const url = new URL(raw)
    const host = url.hostname.replace(/^\[|\]$/g, '')
    const port = Number(url.port || (url.protocol === 'https:' ? 443 : 80))
    return { local: LOOPBACK_HOSTS.has(host), port }
  } catch {
    return { local: false, port: 0 }
  }
}

export class PythonService {
  private readonly baseUrl: string
  private readonly config: PythonServiceConfig
  private readonly logger: PythonServiceOptions['logger']
  private readonly dshHome: string
  private child: ChildProcess | null = null
  private logFd: number | null = null
  private starting: Promise<void> | null = null
  private disposed = false
  private owned = false

  constructor(options: PythonServiceOptions) {
    this.baseUrl = options.baseUrl.replace(/\/+$/, '')
    this.config = options.config ?? {}
    this.logger = options.logger
    this.dshHome = options.dshHome?.trim() || process.env.DSH_HOME || join(homedir(), '.dsh')
  }

  /** 确保服务可用；并发调用共享同一次启动过程。 */
  async ensure(): Promise<void> {
    if (this.disposed || this.config.autoStart === false) return
    if (this.child !== null) return
    if (await this.healthy()) return
    if (this.starting === null) {
      this.starting = this.startOnce().finally(() => {
        this.starting = null
      })
    }
    await this.starting
  }

  /** 停止由本插件拉起的进程；人工启动的服务不受影响。 */
  async dispose(): Promise<void> {
    this.disposed = true
    const child = this.child
    this.child = null
    if (!child || !this.owned) {
      this.closeLog()
      return
    }
    await this.terminate(child)
    this.closeLog()
  }

  private async healthy(): Promise<boolean> {
    try {
      const response = await fetch(`${this.baseUrl}/health`, {
        signal: AbortSignal.timeout(1500),
      })
      return response.ok
    } catch {
      return false
    }
  }

  private resolveDir(): string | null {
    const candidates: string[] = []
    if (this.config.dir?.trim()) candidates.push(resolve(this.config.dir.trim()))
    // lib/index.js -> harness/lib -> harness -> 仓库根 -> server
    const moduleDir = dirname(fileURLToPath(import.meta.url))
    candidates.push(resolve(moduleDir, '..', '..', 'server'))
    candidates.push(resolve(process.cwd(), 'server'))
    for (const dir of candidates) {
      if (existsSync(join(dir, 'src', '__main__.py'))) return dir
    }
    return null
  }

  private resolveExecutable(dir: string): string {
    if (this.config.executable?.trim()) return this.config.executable.trim()
    const venv = process.platform === 'win32'
      ? join(dir, '.venv', 'Scripts', 'python.exe')
      : join(dir, '.venv', 'bin', 'python')
    if (existsSync(venv)) return venv
    const fallback = process.platform === 'win32' ? 'python' : 'python3'
    this.logger.warn(`未找到 ${venv}，回退到 ${fallback}（依赖可能缺失，请先执行 ./build.sh）`)
    return fallback
  }

  private openLog(): number | null {
    const path = this.config.logFile?.trim()
      || join(this.dshHome, 'x-agents.log')
    try {
      mkdirSync(dirname(path), { recursive: true })
      this.logFd = openSync(path, 'a')
      return this.logFd
    } catch (error) {
      this.logger.warn(`无法写入 Python 服务日志 ${path}：${String(error)}`)
      return null
    }
  }

  private closeLog(): void {
    const fd = this.logFd
    this.logFd = null
    if (fd !== null) {
      try {
        closeSync(fd)
      } catch {
        // ignore
      }
    }
  }

  private async startOnce(): Promise<void> {
    const { local, port } = parseServiceUrl(this.baseUrl)
    if (!local) {
      this.logger.info(`x-agents 服务 ${this.baseUrl} 不是本机地址，跳过自动启动`)
      return
    }
    const dir = this.resolveDir()
    if (!dir) {
      this.logger.warn(
        '未找到 Python 服务目录（server/src/__main__.py）：'
        + '请在插件配置 pythonService.dir 指定绝对路径，或先执行 ./build.sh',
      )
      return
    }
    if (this.disposed) return

    const executable = this.resolveExecutable(dir)
    const log = this.openLog()
    this.logger.info(`启动 Python 子智能体服务：${executable} -m src（cwd=${dir}, port=${port}）`)
    let spawnError: Error | null = null
    const child = spawn(executable, ['-m', 'src'], {
      cwd: dir,
      env: {
        ...process.env,
        DSH_HOME: this.dshHome,
        XAGENTS_PORT: String(port),
      },
      stdio: ['ignore', log ?? 'ignore', log ?? 'ignore'],
    })
    this.child = child
    this.owned = true
    // 父进程异常退出时同步杀掉子进程，避免残留孤儿 Python 服务。
    const killOnExit = (): void => {
      try {
        child.kill('SIGKILL')
      } catch {
        // ignore
      }
    }
    process.once('exit', killOnExit)
    child.once('exit', () => process.removeListener('exit', killOnExit))
    if (this.disposed) {
      void this.terminate(child)
      return
    }

    child.once('error', (error) => {
      spawnError = error
      if (this.child === child) this.child = null
      this.logger.warn(`Python 服务启动失败：${String(error)}`)
    })
    child.once('exit', (code, signal) => {
      if (this.child === child) this.child = null
      if (!this.disposed) {
        this.logger.warn(`Python 服务已退出（code=${code ?? '-'}, signal=${signal ?? '-'}）`)
      }
    })

    const timeout = Math.max(1, this.config.startupTimeout ?? 60)
    const deadline = Date.now() + timeout * 1000
    while (Date.now() < deadline) {
      if (this.disposed) return
      if (spawnError) throw spawnError
      if (this.child === null) throw new Error('Python 服务进程已退出，请查看 x-agents.log')
      if (await this.healthy()) {
        this.logger.info(`Python 服务已就绪：${this.baseUrl}（pid=${child.pid ?? '-'}）`)
        return
      }
      await delay(500)
    }
    this.logger.warn(
      `等待 Python 服务超时（${timeout}s）：${this.baseUrl}，`
      + `日志见 ${this.config.logFile?.trim() || join(this.dshHome, 'x-agents.log')}`,
    )
  }

  private async terminate(child: ChildProcess): Promise<void> {
    if (child.exitCode !== null || child.signalCode !== null) return
    await new Promise<void>((done) => {
      let settled = false
      const finish = (): void => {
        if (settled) return
        settled = true
        done()
      }
      child.once('exit', finish)
      try {
        child.kill('SIGTERM')
      } catch {
        finish()
      }
      const timer = setTimeout(() => {
        try {
          child.kill('SIGKILL')
        } catch {
          // ignore
        }
        finish()
      }, 5000)
      timer.unref()
    })
  }
}
