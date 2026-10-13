/**
 * 把`dsh --profile headless --json`的NDJSON事件流渲染成人可读的进度输出
 */

import readline from 'node:readline'

const MAX = 400
const clip = (s, n = MAX) => {
  const t = String(s ?? '').replace(/\s+/g, ' ').trim()
  return t.length > n ? `${t.slice(0, n)}…` : t
}

const rl = readline.createInterface({ input: process.stdin })
rl.on('line', (line) => {
  const trimmed = line.trim()
  if (!trimmed) return
  let e
  try {
    e = JSON.parse(trimmed)
  } catch {
    process.stdout.write(`${line}\n`)
    return
  }

  switch (e.type) {
    case 'session':
      console.log(`[session] ${e.sessionId}`)
      break
    case 'status':
      if (e.phase === 'turn_start') console.log(`\n── turn ${e.turn}`)
      else if (e.phase === 'step_start') console.log(`   · step ${e.step}`)
      else if (e.phase === 'turn_end') {
        const kind = e.reason?.kind ?? '?'
        console.log(`── turn end (${kind})`)
      }
      break
    case 'thinking':
      console.log(`   [thinking] ${clip(e.text)}`)
      break
    case 'text':
      console.log(`\n${e.text}`)
      break
    case 'tool_call':
      console.log(`\n→ ${e.tool} ${clip(JSON.stringify(e.input ?? {}))}`)
      break
    case 'tool_result':
      console.log(`← [${e.status}] ${clip(e.result)}`)
      break
    case 'error':
      console.error(`!! error: ${clip(e.message ?? JSON.stringify(e))}`)
      break
    case 'final':
      console.log(`\n========== 最终答复 ==========\n${e.text}\n`)
      break
    default:
      break
  }
})
