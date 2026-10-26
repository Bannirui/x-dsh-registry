/**
 * 暴露成一个HTTP接口
 *
 *   默认情况下harness的每次运行都是headless/CLI驱动 在web服务器上注册一个路由 收到请求后
 *     1. 用ctx.agents.create(...) 新建一个主Agent（root session）；
 *     2. agent.followup(用户消息) 提交任务；
 *     3. await agent.whenIdle()等它跑完（主Agent会调用requirements_pipeline等工具）；
 *     4. 从会话事件里取最后一条assistant文本返回。
 */

import { randomUUID } from 'node:crypto'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { createUserMessage } from '@deepseek-ai/dsh-llm'
import { SessionId, SessionSeq } from '@deepseek-ai/dsh-session'
import type { Session } from '@deepseek-ai/dsh-session'
import type { Context } from '@deepseek-ai/cordis'
// 下面这些import只为触发Context上的服务类型增强
import type { } from '@deepseek-ai/dsh-host-webserver'
import type { } from '@deepseek-ai/dsh-agent'
import type { } from '@deepseek-ai/dsh-agent-default-model'
import type { } from '@deepseek-ai/dsh-fs'

export interface HttpApiConfig {
  /** Bearer token 未配置时回退到环境变量X_HARNESS_TOKEN 为空则不校验 */
  token?: string
  /** 当请求只给material时 拼在material前面的指令前缀 */
  taskPrefix?: string
  /** 同时在跑的主Agent上限 超出排队 <=0或省略表示不限 */
  maxConcurrent?: number
}

const DEFAULT_PREFIX = '只调用requirements_pipeline一次处理这段需求，然后用中文总结结果并结束，不要调用其它工具：'

/** 简单的并发闸门：同时最多max个任务，超出的排队（FIFO） */
function createLimiter(max: number): <T>(fn: () => Promise<T>) => Promise<T> {
  if (!Number.isFinite(max) || max <= 0) return (fn) => fn()
  let running = 0
  const waiters: Array<() => void> = []
  return async function limit<T>(fn: () => Promise<T>): Promise<T> {
    if (running >= max) await new Promise<void>((resolve) => waiters.push(resolve))
    running++
    try {
      return await fn()
    } finally {
      running--
      const next = waiters.shift()
      if (next) next()
    }
  }
}

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = []
    req.on('data', (chunk: Buffer) => chunks.push(chunk))
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')))
    req.on('error', reject)
  })
}

async function readJson(req: IncomingMessage): Promise<Record<string, unknown>> {
  const raw = await readBody(req)
  if (!raw.trim()) return {}
  try {
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : {}
  } catch {
    return {}
  }
}

function writeJson(res: ServerResponse, code: number, payload: unknown): void {
  const data = JSON.stringify(payload)
  res.writeHead(code, { 'content-type': 'application/json; charset=utf-8' })
  res.end(data)
}

/** 一次运行区间内turn的结束原因（结构性子集，避免暴露内部类型） */
type TurnReason = { kind?: string; error?: { message?: string } }

/** 取出一次运行区间内最后一条非空assistant文本与turn结束原因（复制headless的summarize）。 */
function summarize(session: Session, firstSeq: number): { text: string; reason?: TurnReason } {
  let started = false
  let text = ''
  let reason: TurnReason | undefined
  const length = session.seq
  for (let seq = firstSeq; seq < length; seq++) {
    const event = session.eventAt(SessionSeq(seq))
    if (!event) continue
    if (event.type === 'turn/start') {
      started = true
      continue
    }
    if (!started) continue
    if (event.type === 'assistant/message') {
      const joined = event.data.message.content
        .filter((block) => block.type === 'text')
        .map((block) => (block.type === 'text' ? block.text : ''))
        .join('')
      if (joined !== '') text = joined
    }
    if (event.type === 'turn/end') reason = event.data.reason as TurnReason
  }
  return { text, reason }
}

export function registerHttpApi(ctx: Context, config: HttpApiConfig = {}): void {
  const token = (config.token?.trim() || process.env.X_HARNESS_TOKEN || '').trim()

  // 等webServer/agents/sessions/agentDefaultModel都就绪再注册路由。
  ctx.inject(['webServer', 'agents', 'sessions', 'agentDefaultModel'], (c) => {
    const { webServer, agents, sessions, agentDefaultModel } = c
    const limit = createLimiter(config.maxConcurrent ?? 4)

    /** 主Agent的工作目录 优先用harness的fs服务解析当前目录 */
    const resolveCwd = async (): Promise<string> => {
      const fs = c.get('fs')
      if (fs !== undefined) {
        try {
          return fs.processPath(await fs.resolve('.'))
        } catch {
          // 落到进程cwd
        }
      }
      return process.cwd()
    }

    const runMainAgent = async (task: string) => {
      const selection = agentDefaultModel.currentSelection()
      const { agent, dispose: disposeAgent } = await agents.create({
        sessionId: SessionId(`session-${randomUUID()}`),
        meta: { cwd: await resolveCwd() },
        agentOptions: { provider: selection.provider, model: selection.model },
      })
      try {
        await agent.whenIdle()
        const firstSeq = agent.session.seq
        agent.followup(createUserMessage({
          content: [{ type: 'text', text: task }],
          source: { kind: 'user' },
        }))
        await agent.whenIdle()
        await sessions.flush(agent.session)
        return summarize(agent.session, firstSeq)
      } finally {
        // 一次性请求：跑完释放该主Agent 避免会话/资源在内存里累积
        await disposeAgent().catch(() => { })
      }
    }

    const dispose = webServer.register({
      kind: 'exact',
      path: '/x/dsh/registry',
      handler: async (req, res) => {
        if (token && req.headers.authorization !== `Bearer ${token}`) {
          writeJson(res, 401, { error: 'unauthorized' })
          return
        }
        if (req.method !== 'POST') {
          writeJson(res, 405, { error: 'method not allowed; use POST' })
          return
        }

        const body = await readJson(req)
        const material = typeof body.material === 'string' ? body.material : ''
        const explicitTask = typeof body.task === 'string' ? body.task.trim() : ''
        if (!material && !explicitTask) {
          writeJson(res, 400, { error: 'body must contain "material" (string) or "task" (string)' })
          return
        }
        const task = explicitTask || `${config.taskPrefix?.trim() || DEFAULT_PREFIX}${material}`

        try {
          const outcome = await limit(() => runMainAgent(task))
          const kind = outcome.reason?.kind
          if (kind !== undefined && kind !== 'completed') {
            writeJson(res, 502, {
              error: `agent run ended: ${kind}`,
              detail: outcome.reason?.error?.message ?? null,
              text: outcome.text,
            })
            return
          }
          writeJson(res, 200, { text: outcome.text })
        } catch (error) {
          writeJson(res, 500, { error: String(error) })
        }
      },
    })

    c.effect(() => dispose, 'x-harness: http-api route')
    const concurrency = config.maxConcurrent && config.maxConcurrent > 0 ? config.maxConcurrent : 'unlimited'
    c.logger.info(
      `x-harness: HTTP API at POST /x/dsh/registry`
      + `${token ? ' (bearer auth)' : ' (NO AUTH)'}, maxConcurrent=${concurrency}`,
    )
  })
}
