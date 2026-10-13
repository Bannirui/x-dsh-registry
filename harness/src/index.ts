/**
 * 宿主插件
 *
 * 职责：
 *   1. 把配置里列出的每个HTTP子智能体注册成harness的一个subagent provider（每个可以有自己的 baseUrl，用于接不同的外部服务）
 *   2. 注册编排工具`requirements_pipeline`：按配置的pipeline顺序依次调用子智能体
 *   3. 注册通用调试工具`x_call_agent`：调用任意已注册子智能体
 *
 * 业务逻辑全部在Python侧（agents/），这里只做适配与编排
 *
 * @module x-harness
 */

import type { Context } from '@deepseek-ai/cordis'
import z from '@deepseek-ai/schemastery'
import { defineTool } from '@deepseek-ai/dsh-tools'
import type { Agent } from '@deepseek-ai/dsh-agent'
import type { SubagentResult, SubagentRun } from '@deepseek-ai/dsh-subagent'
import type { JsonValue } from '@deepseek-ai/dsh-util-values'
import { createHttpProvider } from './http-provider.js'
import { registerHttpApi } from './http-api.js'

export const name = 'x-requirements'
export const inject = ['tools', 'subagents']

/** 一个子智能体端点：名字+可选的服务基址（不填则用顶层 baseUrl） */
export interface AgentEndpoint {
  name: string
  baseUrl?: string
}

export interface Config {
  /** 默认的服务基址 */
  baseUrl?: string
  /** 要注册的子智能体列表 */
  agents?: AgentEndpoint[]
  /** 子智能体顺序 每一步的输出作为下一步的输入 */
  pipeline?: string[]
  /** HTTP API鉴权用Bearer token 为空则回退环境变量X_HARNESS_TOKEN 再空则不校验 */
  token?: string
  /** HTTP请求只给material时 拼在前面的指令前缀 */
  taskPrefix?: string
  /** HTTP并发上限（同时运行的主Agent数）超出排队 默认4 <=0表示不限 */
  maxConcurrent?: number
}

export const Config = z.object({
  baseUrl: z.string().default('http://127.0.0.1:8077'),
  agents: z.array(z.object({ name: z.string().required(), baseUrl: z.string() })),
  pipeline: z.array(z.string()).default(['extraction', 'clarification', 'completion', 'echo']),
  token: z.string(),
  taskPrefix: z.string(),
  maxConcurrent: z.natural().default(4),
})

const DEFAULT_AGENTS: AgentEndpoint[] = [
  { name: 'extraction' },
  { name: 'clarification' },
  { name: 'completion' },
  { name: 'echo', baseUrl: 'http://127.0.0.1:8090' },
]
const DEFAULT_PIPELINE = ['extraction', 'clarification', 'completion', 'echo']

/** 把子代理结果的文本块还原成JSON */
function resultJson(result: SubagentResult, label: string): JsonValue {
  if (result.stopReason !== 'completed') {
    const detail = result.diagnostic ? `: ${result.diagnostic}` : ''
    throw new Error(`子智能体${label}失败 (stopReason=${result.stopReason})${detail}`)
  }
  const text = result.output
    .filter((block) => block.type === 'text')
    .map((block) => (block.type === 'text' ? block.text : ''))
    .join('')
  try {
    return JSON.parse(text) as JsonValue
  } catch {
    throw new Error(`子智能体${label}返回了非JSON结果: ${text.slice(0, 200)}`)
  }
}

export function apply(ctx: Context, config: Config): void {
  const defaultBaseUrl = config.baseUrl?.trim() || 'http://127.0.0.1:8077'
  const agents: AgentEndpoint[] =
    config.agents && config.agents.length ? config.agents : DEFAULT_AGENTS
  const pipeline: string[] =
    config.pipeline && config.pipeline.length ? config.pipeline : DEFAULT_PIPELINE

  for (const agent of agents) {
    ctx.subagents.registerProvider(createHttpProvider(agent.baseUrl?.trim() || defaultBaseUrl, agent.name))
  }

  /** 调一个子智能体 返回其JSON 输出 */
  const callAgent = async (
    agentName: string,
    input: unknown,
    parent: Agent,
    signal: AbortSignal,
  ): Promise<JsonValue> => {
    if (!ctx.subagents.getProvider(agentName)) {
      throw new Error(`未注册的子智能体: ${agentName}`)
    }
    const run: SubagentRun = await ctx.subagents.start(agentName, {
      label: `x:${agentName}`,
      prompt: [{ type: 'text', text: JSON.stringify(input) }],
      parent,
      signal,
    })
    try {
      const result = await run.result
      return resultJson(result, agentName)
    } finally {
      await run.dispose()
    }
  }

  // ---- 编排工具 需求开发流水线（按pipeline顺序跑） ----
  ctx.tools.register(
    defineTool({
      name: 'requirements_pipeline',
      description:
        '把一段原始需求材料跑完整的需求开发流水线。'
        + `当前流水线顺序：${pipeline.join(' -> ')}。`
        + '每个阶段由独立子智能体完成，上一步输出作为下一步输入，返回所有阶段的完整结果。',
      parameters: {
        material: {
          type: 'string',
          required: true,
          description: '原始需求材料（需求文档/访谈记录/工单等文本）。',
        },
      },
      output: {
        schema: {
          type: 'object',
          additionalProperties: false,
          properties: {
            kind: { type: 'string', required: true, const: 'requirements-pipeline' },
            stages: { type: 'json', required: true },
            final: { type: 'json', required: true },
          },
        },
        render: (_args, value) => {
          const stages = Array.isArray(value.stages) ? value.stages : []
          const names = stages
            .map((stage) => (stage && typeof stage === 'object' && 'agent' in stage ? String((stage as { agent: unknown }).agent) : '?'))
            .join(' -> ')
          const items = value.final && typeof value.final === 'object' && 'items' in value.final
            ? (value.final as { items?: unknown[] }).items
            : undefined
          const count = Array.isArray(items) ? items.length : 0
          return [{ type: 'text', text: `需求流水线完成(${names})：最终${count}条需求。` }]
        },
      },
      async execute(args, exec) {
        const parent = exec.agent
        if (!parent) throw new Error('requirements_pipeline 需要调用方Agent（exec.agent为空）')

        let current: JsonValue = { raw: args.material }
        const stages: Array<{ agent: string; output: JsonValue }> = []
        for (const agentName of pipeline) {
          current = await callAgent(agentName, current, parent, exec.signal)
          stages.push({ agent: agentName, output: current })
        }

        return { kind: 'requirements-pipeline' as const, stages, final: current }
      },
    }),
  )

  // ---- 通用调试工具：调用任意已注册子智能体 ----
  ctx.tools.register(
    defineTool({
      name: 'x_call_agent',
      description: '直接调用一个已注册的子智能体并返回其原始输出（调试/测试用）。agent传子智能体名，input传任意JSON。',
      parameters: {
        agent: {
          type: 'string',
          required: true,
          description: `子智能体名，如${agents.map((a) => a.name).join(' / ')}。`,
        },
        input: {
          type: 'json',
          required: true,
          description: '传给该子智能体的输入（任意JSON）。',
        },
      },
      output: {
        schema: {
          type: 'object',
          additionalProperties: false,
          properties: {
            kind: { type: 'string', required: true, const: 'x-call-agent' },
            agent: { type: 'string', required: true },
            output: { type: 'json', required: true },
          },
        },
        render: (_args, value) => [{
          type: 'text',
          text: `${value.agent} -> ${JSON.stringify(value.output).slice(0, 400)}`,
        }],
      },
      async execute(args, exec) {
        const parent = exec.agent
        if (!parent) throw new Error('x_call_agent需要调用方Agent（exec.agent为空）')
        const output = await callAgent(args.agent, args.input, parent, exec.signal)
        return { kind: 'x-call-agent' as const, agent: args.agent, output }
      },
    }),
  )

  ctx.logger.info(
    `${name}: ${agents.length} providers (${agents.map((a) => a.name).join(', ')}); `
    + `pipeline=${pipeline.join(' -> ')}`,
  )

  // 在带web服务器的profile里 把主Agent暴露成HTTP接口
  registerHttpApi(ctx, {
    token: config.token,
    taskPrefix: config.taskPrefix,
    maxConcurrent: config.maxConcurrent,
  })
}
