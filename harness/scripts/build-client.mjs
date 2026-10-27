import { execFileSync } from 'node:child_process'
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const root = process.cwd()
const tsc = join(root, 'node_modules', '.bin', process.platform === 'win32' ? 'tsc.cmd' : 'tsc')
const temp = join(root, 'lib', '.client-cjs')
const output = join(root, 'lib', 'client.js')

execFileSync(tsc, ['-p', 'tsconfig.client-cjs.json'], { cwd: root, stdio: 'inherit' })
const body = readFileSync(join(temp, 'client.js'), 'utf8')
const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'))
const id = String(pkg.name)

const wrapped = `window.__ModuleLoader__.load({
\tid: ${JSON.stringify(id)},
\tfactory: (require) => {
\t\tvar module = { exports: {} };
\t\tvar exports = module.exports;
${body}
\t\treturn module.exports;
\t}
});
`

mkdirSync(join(root, 'lib'), { recursive: true })
writeFileSync(output, wrapped, 'utf8')
writeFileSync(join(root, 'lib', 'client.d.ts'), `import type { Context } from '@deepseek-ai/cordis'

export declare const inject: string[]
export declare function apply(ctx: Context): void
`)
rmSync(temp, { recursive: true, force: true })
console.log(`built ${output}`)
