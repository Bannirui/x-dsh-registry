/**
 * 端到端冒烟
 * 用真实的HTTP provider直接调用一个子智能体服务
 * 不经过harness 专门验证TS适配层<->HTTP后端的契约
 * 注意：echo等非LLM子智能体无需模型key；extraction/clarification/completion走真实模型，需DEEPSEEK_API_KEY
 *
 * usage：
 *   node scripts/smoke-backend.mjs [baseUrl] [agent] [rawOrJson]
 * samples：
 *   node scripts/smoke-backend.mjs http://127.0.0.1:8077 extraction "系统必须支持用户注册。"
 *   node scripts/smoke-backend.mjs http://127.0.0.1:8090 echo '{"hello":"world"}'
 */

import { createHttpProvider } from '../lib/http-provider.js'

const baseUrl = process.argv[2] ?? 'http://127.0.0.1:8077'
const agent = process.argv[3] ?? 'extraction'
const raw = process.argv[4] ?? '系统必须支持用户注册。登录响应时间小于500ms。'

let input
try {
  input = JSON.parse(raw)
} catch {
  input = { raw }
}

const provider = createHttpProvider(baseUrl, agent)
const run = await provider.start({
  label: `smoke:${agent}`,
  prompt: [{ type: 'text', text: JSON.stringify(input) }],
  parent: {},
  signal: new AbortController().signal,
})

const result = await run.result
console.log('agent:', agent)
console.log('stopReason:', result.stopReason)
if (result.diagnostic) console.log('diagnostic:', result.diagnostic)
console.log('output:', result.output.filter((b) => b.type === 'text').map((b) => b.text).join(''))
