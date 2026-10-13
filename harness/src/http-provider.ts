/**
 * 把一个HTTP子智能体服务适配成harness的SubagentProvider
 *
 * harness的`ctx.subagents.start(name, request)`会调用这里的`start`
 * 把request.prompt的文本当作JSON输入POST给后端，再把后端返回的`output`序列化成文本ContentBlock，作为该子代理的结果
 */

import { randomUUID } from 'node:crypto'
import type { SubagentProvider, SubagentResult, SubagentRun } from '@deepseek-ai/dsh-subagent'

/** 把ContentBlock[]里的文本块拼起来 */
function promptText(prompt: readonly { type?: string; text?: string }[]): string {
  return prompt
    .filter((block) => block.type === 'text' && typeof block.text === 'string')
    .map((block) => block.text as string)
    .join('\n')
}

/** prompt约定是JSON 解析失败就当作原始文本 */
function parseInput(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return { raw: text }
  }
}

export function createHttpProvider(baseUrl: string, agentName: string): SubagentProvider {
  const endpoint = `${baseUrl.replace(/\/+$/, '')}/agents/${encodeURIComponent(agentName)}`

  return {
    name: agentName,
    // 后端不支持这些harness启动期能力
    capabilities: {
      agentOptions: false,
      outputSchema: false,
      depthLimit: false,
      toolFilter: false,
      persona: false,
    },
    inheritsParentContext: false,

    async start(request): Promise<SubagentRun> {
      const input = parseInput(promptText(request.prompt))

      const result: Promise<SubagentResult> = (async (): Promise<SubagentResult> => {
        try {
          const response = await fetch(endpoint, {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ input }),
            signal: request.signal,
          })
          if (!response.ok) {
            return {
              output: [],
              stopReason: 'error',
              diagnostic: `backend "${agentName}" HTTP ${response.status}`,
            }
          }
          const body = (await response.json()) as {
            output?: unknown
            stopReason?: string
            diagnostic?: string
          }
          if (body.stopReason !== undefined && body.stopReason !== 'completed') {
            return {
              output: [],
              stopReason: 'error',
              diagnostic: body.diagnostic ?? `backend "${agentName}" returned ${body.stopReason}`,
            }
          }
          return {
            output: [{ type: 'text', text: JSON.stringify(body.output ?? body) }],
            stopReason: 'completed',
          }
        } catch (error) {
          return {
            output: [],
            stopReason: 'error',
            diagnostic: `backend "${agentName}" unreachable: ${String(error)}`,
          }
        }
      })()

      return {
        id: `x-${agentName}-${randomUUID()}` as unknown as SubagentRun['id'],
        localAgent: undefined,
        result,
        dispose: async () => {},
      }
    },
  }
}
