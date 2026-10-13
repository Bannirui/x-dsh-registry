/**
 * 离线验证
 *
 * 运行：node scripts/verify.mjs
 */

import assert from 'node:assert/strict'
import { apply } from '../lib/index.js'

function makeCtx() {
  const providers = []
  const calls = []
  const tools = {}
  const ctx = {
    logger: { info() {}, warn() {} },
    // registerHttpApi会用ctx.inject等web服务；离线ctx里给个空实现，不触发回调（相当于没有webServer/agents的headless场景）
    inject() {},
    get() {
      return undefined
    },
    effect() {},
    tools: {
      register(definition) {
        tools[definition.name] = definition
        return () => {}
      },
    },
    subagents: {
      registerProvider(provider) {
        providers.push(provider.name)
        return () => {}
      },
      getProvider(name) {
        return providers.includes(name) ? { name } : undefined
      },
      async start(name, request) {
        const input = JSON.parse(request.prompt[0].text)
        calls.push({ name, input })
        let output
        if (name === 'extraction') {
          output = { items: [{ id: 'REQ-001', title: input.raw }] }
        } else if (name === 'clarification') {
          assert.ok(Array.isArray(input.items), 'clarification 必须收到 extraction 的 items')
          output = { items: input.items, questions: [{ itemId: 'REQ-001', question: '缺少验收标准？' }] }
        } else if (name === 'completion') {
          assert.ok(Array.isArray(input.questions), 'completion 必须收到 clarification 的 questions')
          output = { items: input.items, recommendations: [{ itemId: 'REQ-001', suggestion: '补充异常路径' }] }
        } else if (name === 'echo') {
          output = input // echo：原样返回
        } else {
          throw new Error(`unexpected agent ${name}`)
        }
        return {
          id: `fake-${name}`,
          localAgent: undefined,
          result: Promise.resolve({ output: [{ type: 'text', text: JSON.stringify(output) }], stopReason: 'completed' }),
          dispose: async () => {},
        }
      },
    },
  }
  return { ctx, providers, calls, tools }
}

const parent = { id: 'p' }
const signal = new AbortController().signal

async function testProviders() {
  const { ctx, providers } = makeCtx()
  apply(ctx, {})
  assert.deepEqual(
    providers,
    ['extraction', 'clarification', 'completion', 'echo'],
    '四个 provider 必须注册（含 echo）',
  )
  console.log('ok - 注册了 extraction / clarification / completion / echo')
}

async function testPipeline() {
  const { ctx, calls, tools } = makeCtx()
  apply(ctx, {})
  const tool = tools['requirements_pipeline']
  assert.ok(tool, '必须注册 requirements_pipeline')

  const result = await tool.execute({ material: '系统必须支持注册。' }, { agent: parent, signal })
  assert.equal(result.kind, 'requirements-pipeline')
  assert.deepEqual(
    calls.map((c) => c.name),
    ['extraction', 'clarification', 'completion', 'echo'],
    '流水线必须按配置顺序调用（含 echo 作为第4个子智能体）',
  )
  assert.equal(result.stages.length, 4)
  assert.equal(result.stages[0].output.items[0].id, 'REQ-001')
  assert.equal(result.stages[1].output.questions.length, 1)
  assert.equal(result.stages[2].output.recommendations.length, 1)
  // echo 原样透传，所以最终结果仍是补全阶段的产物
  assert.equal(result.final.items[0].id, 'REQ-001')
  console.log('ok - pipeline runs extraction -> clarification -> completion -> echo and chains data')
}

async function testCustomPipeline() {
  const { ctx, calls, tools } = makeCtx()
  apply(ctx, { pipeline: ['extraction', 'echo'] })
  const tool = tools['requirements_pipeline']
  const result = await tool.execute({ material: 'x' }, { agent: parent, signal })
  assert.deepEqual(calls.map((c) => c.name), ['extraction', 'echo'])
  assert.equal(result.stages.length, 2)
  console.log('ok - pipeline is config-driven (custom order works)')
}

async function testCallAgentEcho() {
  const { ctx, tools } = makeCtx()
  apply(ctx, {})
  const tool = tools['x_call_agent']
  assert.ok(tool, '必须注册 x_call_agent')

  const input = { hello: 'world', nested: { n: 1 } }
  const result = await tool.execute({ agent: 'echo', input }, { agent: parent, signal })
  assert.deepEqual(result.output, input, 'echo 必须原样返回 input')
  console.log('ok - x_call_agent echoes input through the echo provider')
}

async function testUnknownAgent() {
  const { ctx, tools } = makeCtx()
  apply(ctx, {})
  const tool = tools['x_call_agent']
  await assert.rejects(
    tool.execute({ agent: 'nope', input: {} }, { agent: parent, signal }),
    /未注册的子智能体/,
  )
  console.log('ok - unknown agent fails loudly')
}

async function testBackendError() {
  const { ctx, tools } = makeCtx()
  ctx.subagents.start = async () => ({
    id: 'x',
    localAgent: undefined,
    result: Promise.resolve({ output: [], stopReason: 'error', diagnostic: 'boom' }),
    dispose: async () => {},
  })
  apply(ctx, {})
  const tool = tools['requirements_pipeline']
  await assert.rejects(
    tool.execute({ material: 'x' }, { agent: parent, signal }),
    /失败 \(stopReason=error\): boom/,
  )
  console.log('ok - backend error propagates as a tool error')
}

await testProviders()
await testPipeline()
await testCustomPipeline()
await testCallAgentEcho()
await testUnknownAgent()
await testBackendError()
console.log('\nAll offline checks passed.')
