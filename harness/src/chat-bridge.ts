import type { Context } from '@deepseek-ai/cordis'
import type { RequestMessage, StreamChunk } from '@deepseek-ai/dsh-llm'

/** 取出最后一条用户输入里的文本（跳过 runtime-context 等注入消息） */
function lastUserText(messages: readonly RequestMessage[]): string {
  let fallback = ''
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i]
    if (message.role !== 'user') continue
    const text = message.content
      .filter((block) => block.type === 'text')
      .map((block) => (block.type === 'text' ? block.text : ''))
      .join('')
    if (message.source?.kind === 'user') return text
    if (fallback === '') fallback = text
  }
  return fallback
}

/** 调用 Python 子智能体并取出要展示的文本 */
async function callAgent(baseUrl: string, agentName: string, text: string): Promise<string> {
  const endpoint = `${baseUrl.replace(/\/+$/, '')}/agents/${encodeURIComponent(agentName)}`
  const response = await fetch(endpoint, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ input: { text } }),
  })
  if (!response.ok) {
    throw new Error(`Python 子智能体 "${agentName}" HTTP ${response.status}`)
  }
  const body = (await response.json()) as {
    output?: unknown
    stopReason?: string
    diagnostic?: string
  }
  if (body.stopReason !== undefined && body.stopReason !== 'completed') {
    throw new Error(body.diagnostic ?? `Python 子智能体 "${agentName}" 返回 ${body.stopReason}`)
  }
  const output = body.output
  if (typeof output === 'string') return output
  if (output && typeof output === 'object' && 'text' in output) {
    const value = (output as { text?: unknown }).text
    if (typeof value === 'string') return value
  }
  return JSON.stringify(output ?? body)
}

/** 一组合法的文本流式响应 */
async function* textStream(text: string): AsyncIterable<StreamChunk> {
  yield { type: 'block-start', index: 0, blockType: 'text' }
  yield { type: 'text-delta', index: 0, text }
  yield { type: 'block-end', index: 0, block: { type: 'text', text } }
  yield { type: 'finish', reason: { kind: 'stop' } }
}

/** 转发失败时产出一个合法的错误结束块 */
function errorChunk(error: unknown): StreamChunk {
  return {
    type: 'finish',
    reason: {
      kind: 'error',
      failure: { message: String(error), code: 'CHAT_BRIDGE_ERROR' },
    },
  }
}

async function* bridgeStream(
  baseUrl: string,
  agentName: string,
  text: string,
): AsyncIterable<StreamChunk> {
  try {
    const reply = await callAgent(baseUrl, agentName, text)
    yield* textStream(reply)
  } catch (error) {
    yield errorChunk(error)
  }
}

/**
 * 安装 Web 聊天桥。
 * @param ctx 插件上下文
 * @param baseUrl Python 服务基址
 * @param agentName 要转发的 Python 子智能体名
 */
export function installChatBridge(ctx: Context, baseUrl: string, agentName: string): void {
  ctx.on('llm/stream', (options, next) => {
    // 会话标题、压缩等辅助调用仍走真实模型
    if (options.purpose !== undefined) return next()
    return bridgeStream(baseUrl, agentName, lastUserText(options.messages))
  })
}
