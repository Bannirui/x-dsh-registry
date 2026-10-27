import type { Context } from '@deepseek-ai/cordis'
import z from '@deepseek-ai/schemastery'

/** main: 跟随主 Agent 的模型选择；custom: 使用下面填写的独立模型配置。 */
export type SplitLlmMode = 'main' | 'custom'

/**
 * 语义分块（Python 子智能体）使用的 LLM 配置。
 *
 * 由 Web 设置页写入 profile 的 cordis.patch.yml；密钥只保存为凭据引用，
 * 每次任务随 payload 下发给 Python，未配置时 Python 回退到环境变量默认值。
 */
export interface SplitLlmConfig {
  mode: SplitLlmMode
  /** OpenAI 兼容端点根地址，如 https://api.deepseek.com */
  baseUrl: string
  /** 模型名，如 deepseek-chat */
  model: string
  /** 凭据库引用名（不是密钥本身）；未配置对应密钥时回退到共享 DEEPSEEK_API_KEY */
  apiKeyRef: string
  /** 单次调用超时（秒） */
  timeout: number
  /** 失败重试次数 */
  maxRetries: number
}

export const SplitLlmConfigSchema = z.object({
  mode: z.string(),
  baseUrl: z.string(),
  model: z.string(),
  apiKeyRef: z.string(),
  timeout: z.number(),
  maxRetries: z.natural(),
}).volatile()

/** 随任务下发给 Python 的部分配置；只包含设置页真正保存过的字段。 */
export type SplitLlmPayload = Partial<Omit<SplitLlmConfig, 'mode'>>

/**
 * 读取当前语义分块 LLM 配置。
 *
 * 不设字段默认值：未在设置页保存过时 schema 返回空对象，这里再判空返回
 * undefined，让 Python 继续使用 DEEPSEEK_BASE_URL / XAGENTS_MODEL。
 */
export function splitLlmInput(
  config: { splitLlm?: { get(): unknown } },
): Partial<SplitLlmConfig> | undefined {
  try {
    const value = config.splitLlm?.get()
    if (value === null || typeof value !== 'object') return undefined
    const parsed = JSON.parse(JSON.stringify(value)) as Partial<SplitLlmConfig>
    const result: Partial<SplitLlmConfig> = {}
    if (parsed.mode === 'main' || parsed.mode === 'custom') result.mode = parsed.mode
    if (parsed.baseUrl) result.baseUrl = parsed.baseUrl
    if (parsed.model) result.model = parsed.model
    if (parsed.apiKeyRef) result.apiKeyRef = parsed.apiKeyRef
    if (typeof parsed.timeout === 'number' && parsed.timeout > 0) result.timeout = parsed.timeout
    if (typeof parsed.maxRetries === 'number' && parsed.maxRetries >= 0) {
      result.maxRetries = parsed.maxRetries
    }
    return Object.keys(result).length ? result : undefined
  } catch {
    return undefined
  }
}

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function pickPath(root: unknown, path: readonly string[]): unknown {
  let current: unknown = root
  for (const key of path) {
    current = asRecord(current)[key]
  }
  return current
}

/** deepseek-official 用 Anthropic Messages 端点；Python 走 OpenAI 协议，需要去掉后缀。 */
function openAiCompatibleBase(raw: string): string {
  const base = raw.trim().replace(/\/+$/, '')
  if (base.endsWith('/anthropic')) return base.slice(0, -'/anthropic'.length) || 'https://api.deepseek.com'
  return base
}

/** 从 Models 页面的 provider 配置里解析出 Python 可用的端点与凭据引用。 */
function resolveMainSplitLlm(ctx: Context): SplitLlmPayload {
  const selection = ctx.get('agentDefaultModel')?.currentSelection()
  if (!selection) return {}
  const payload: SplitLlmPayload = { model: selection.model }
  const info = ctx.llm.listConfigurableProviders()
    .find((entry) => entry.provider === selection.provider)
  if (!info) return payload

  let profile: Record<string, unknown> = {}
  const settings = ctx.get('settings')
  if (settings) {
    try {
      const descriptor = settings.describe().find(
        (entry: { ns: string; value?: unknown }) => entry.ns === info.settingsNs,
      )
      profile = asRecord(pickPath(descriptor?.value, info.settingsPath))
    } catch {
      // 设置读取失败时退回共享 DEEPSEEK_API_KEY / Python 环境配置。
    }
  }
  const apiKeyRef = typeof profile.apiKeyEnv === 'string'
    ? profile.apiKeyEnv
    : typeof profile.apiKeyRef === 'string' ? profile.apiKeyRef : ''
  const configuredBase = typeof profile.baseURL === 'string'
    ? profile.baseURL
    : typeof profile.baseUrl === 'string' ? profile.baseUrl : ''

  if (selection.provider === 'deepseek-official') {
    payload.apiKeyRef = apiKeyRef || 'DEEPSEEK_API_KEY'
    const rawBase = configuredBase || process.env.DEEPSEEK_BASE_URL || ''
    if (rawBase) payload.baseUrl = openAiCompatibleBase(rawBase)
    return payload
  }

  // 自定义 provider 只有 OpenAI 兼容协议能直接交给 Python 的 openai SDK；
  // 其他协议回退到 Python 侧环境变量配置，避免用错 key/端点。
  const protocol = typeof profile.api === 'string' ? profile.api : ''
  if (protocol !== 'openai-completions') return {}
  if (apiKeyRef) payload.apiKeyRef = apiKeyRef
  if (configuredBase) payload.baseUrl = configuredBase
  return payload
}

/** 按设置页的 mode 生成随任务下发的 LLM 配置。 */
export function runtimeSplitLlm(
  config: { splitLlm?: { get(): unknown } },
  ctx: Context,
): SplitLlmPayload | undefined {
  const saved = splitLlmInput(config)
  if (saved === undefined) return undefined

  const extras: SplitLlmPayload = {}
  if (typeof saved.timeout === 'number') extras.timeout = saved.timeout
  if (typeof saved.maxRetries === 'number') extras.maxRetries = saved.maxRetries

  if (saved.mode === 'main') {
    return { ...resolveMainSplitLlm(ctx), ...extras }
  }
  const payload: SplitLlmPayload = { ...extras }
  if (saved.baseUrl) payload.baseUrl = saved.baseUrl
  if (saved.model) payload.model = saved.model
  if (saved.apiKeyRef) payload.apiKeyRef = saved.apiKeyRef
  return Object.keys(payload).length ? payload : undefined
}
