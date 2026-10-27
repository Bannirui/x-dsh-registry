import { useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import type { CSSProperties } from 'react'
import type { Context } from '@deepseek-ai/cordis'
import type { } from '@deepseek-ai/dsh-client-ui-renderer/client'
import type { } from '@deepseek-ai/dsh-client-ui-settings/client'
import type { } from '@deepseek-ai/dsh-client-ui-slots'
import type { } from '@deepseek-ai/dsh-api-remotes/client'
import type { JsonValue } from '@deepseek-ai/dsh-util-values'

interface OcrAuth {
  type: string
  apiKeyRef: string
  secretKeyRef: string
}

interface OcrProfile {
  id: string
  name: string
  mode: string
  provider: string
  language: string
  dpi: number
  maxPages: number
  concurrency: number
  baseUrl: string
  model: string
  region: string
  auth: OcrAuth
  options: Record<string, string>
  timeout: number
}

interface OcrConfig {
  enabled: boolean
  defaultProfile: string
  profiles: OcrProfile[]
}

interface SplitLlmConfig {
  mode: 'main' | 'custom'
  baseUrl: string
  model: string
  apiKeyRef: string
  timeout: number
  maxRetries: number
}

interface XDshSettings {
  ocr?: OcrConfig
  splitLlm?: SplitLlmConfig
}

interface CredentialInfo {
  configured?: boolean
  writable?: boolean
}

interface CredentialDescribeResponse {
  ok: boolean
  value?: Record<string, CredentialInfo>
}

const NS = 'x-dsh-registry'

export const inject = ['slots', 'remote', 'remote.credentials', 'configForms']

export function apply(ctx: Context): void {
  ctx.effect(
    () => ctx.configForms.whileServed([NS], () => ctx.slots.inject('settings.section', () => [
      ctx.slots.register({
        name: 'settings.section',
        id: 'x-ocr',
        order: 120,
        label: 'OCR',
      }, () => <OcrSettingsPage ctx={ctx} />),
      ctx.slots.register({
        name: 'settings.section',
        id: 'x-split-llm',
        order: 121,
        label: '语义分块 LLM',
      }, () => <SplitLlmSettingsPage ctx={ctx} />),
    ])),
    'x-dsh: OCR / split LLM settings pages',
  )
}

const SPLIT_LLM_DEFAULTS: SplitLlmConfig = {
  mode: 'main',
  baseUrl: 'https://api.deepseek.com',
  model: 'deepseek-chat',
  apiKeyRef: 'SPLIT_LLM_API_KEY',
  timeout: 120,
  maxRetries: 2,
}

function normalizeSplitLlm(raw: Partial<SplitLlmConfig> | undefined): SplitLlmConfig {
  // 兼容旧配置：没有 mode 但填过独立模型配置时，继续视为 custom。
  const legacyCustom = raw !== undefined
    && raw.mode === undefined
    && Boolean(raw.baseUrl || raw.model || raw.apiKeyRef)
  const mode = raw?.mode === 'main'
    ? 'main'
    : raw?.mode === 'custom' || legacyCustom ? 'custom' : 'main'
  return {
    mode,
    baseUrl: raw?.baseUrl || SPLIT_LLM_DEFAULTS.baseUrl,
    model: raw?.model || SPLIT_LLM_DEFAULTS.model,
    apiKeyRef: raw?.apiKeyRef || SPLIT_LLM_DEFAULTS.apiKeyRef,
    timeout: Number(raw?.timeout ?? SPLIT_LLM_DEFAULTS.timeout) || SPLIT_LLM_DEFAULTS.timeout,
    maxRetries: Number.isFinite(Number(raw?.maxRetries))
      ? Number(raw?.maxRetries)
      : SPLIT_LLM_DEFAULTS.maxRetries,
  }
}

function validateSplitLlm(config: SplitLlmConfig): string[] {
  const errors: string[] = []
  if (config.mode === 'main') return errors
  if (!/^https?:\/\/\S+$/.test(config.baseUrl)) errors.push('Base URL 必须是 http(s) 地址')
  if (!config.model) errors.push('Model 不能为空')
  if (!config.apiKeyRef) {
    errors.push('API Key 引用不能为空（默认 SPLIT_LLM_API_KEY）')
  } else if (!/^[A-Za-z0-9_.-]+$/.test(config.apiKeyRef)) {
    errors.push('API Key 引用只能包含字母、数字、下划线、点、短横线')
  }
  return errors
}

function defaultLocalProfile(): OcrProfile {
  return {
    id: 'local-rapid',
    name: '本地 RapidOCR',
    mode: 'local',
    provider: 'rapidocr',
    language: 'chi_sim',
    dpi: 180,
    maxPages: 50,
    concurrency: 2,
    baseUrl: '',
    model: '',
    region: '',
    auth: { type: 'none', apiKeyRef: '', secretKeyRef: '' },
    options: {},
    timeout: 60,
  }
}

function normalizeProfile(raw: Partial<OcrProfile> | undefined, index: number): OcrProfile {
  const mode = raw?.mode === 'cloud' || raw?.mode === 'auto' ? raw.mode : 'local'
  const provider = raw?.provider || (mode === 'local' ? 'rapidocr' : 'vision')
  const authType = raw?.auth?.type || (provider === 'baidu' ? 'ak_sk' : provider === 'vision' ? 'bearer' : 'none')
  return {
    id: raw?.id || `profile-${index + 1}`,
    name: raw?.name || '',
    mode,
    provider,
    language: raw?.language || 'chi_sim',
    dpi: Number(raw?.dpi ?? 180) || 180,
    maxPages: Number(raw?.maxPages ?? 50) || 50,
    concurrency: Number(raw?.concurrency ?? 2) || 2,
    baseUrl: raw?.baseUrl || '',
    model: raw?.model || '',
    region: raw?.region || '',
    auth: {
      type: authType,
      apiKeyRef: raw?.auth?.apiKeyRef || '',
      secretKeyRef: raw?.auth?.secretKeyRef || '',
    },
    options: raw?.options && typeof raw.options === 'object' ? { ...raw.options } : {},
    timeout: Number(raw?.timeout ?? 60) || 60,
  }
}

function normalizeConfig(raw: OcrConfig | undefined): OcrConfig {
  const profiles = Array.isArray(raw?.profiles) && raw.profiles.length
    ? raw.profiles.map((profile, index) => normalizeProfile(profile, index))
    : [defaultLocalProfile()]
  const wanted = raw?.defaultProfile || ''
  const defaultProfile = profiles.some((profile) => profile.id === wanted) ? wanted : profiles[0].id
  return { enabled: raw?.enabled ?? true, defaultProfile, profiles }
}

function profileLabel(profile: OcrProfile): string {
  if (profile.mode === 'local') return '本地 OCR - RapidOCR'
  return profile.provider === 'baidu' ? '云 OCR - 百度通用 OCR' : '云 OCR - OpenAI 兼容'
}

function derivedRefs(profile: OcrProfile): { apiKeyRef: string; secretKeyRef: string } {
  const base = `OCR_${(profile.id || 'profile').toUpperCase().replace(/[^A-Z0-9]+/g, '_')}`
  return {
    apiKeyRef: profile.auth.apiKeyRef || `${base}_API_KEY`,
    secretKeyRef: profile.auth.secretKeyRef || `${base}_SECRET_KEY`,
  }
}

function withProvider(profile: OcrProfile, kind: string): OcrProfile {
  if (kind === 'local:rapidocr') {
    return {
      ...profile,
      mode: 'local',
      provider: 'rapidocr',
      auth: { type: 'none', apiKeyRef: '', secretKeyRef: '' },
    }
  }
  const provider = kind.startsWith('cloud:') ? kind.slice('cloud:'.length) : 'vision'
  const oldDefaults = profile.provider === 'baidu'
    ? { baseUrl: 'https://aip.baidubce.com', model: 'general_basic' }
    : { baseUrl: 'https://api.openai.com/v1', model: '' }
  const nextDefaults = provider === 'baidu'
    ? { baseUrl: 'https://aip.baidubce.com', model: 'general_basic' }
    : { baseUrl: 'https://api.openai.com/v1', model: '' }
  const baseUrl = !profile.baseUrl || profile.baseUrl === oldDefaults.baseUrl
    ? nextDefaults.baseUrl
    : profile.baseUrl
  const model = !profile.model || profile.model === oldDefaults.model
    ? nextDefaults.model
    : profile.model
  return {
    ...profile,
    mode: 'cloud',
    provider,
    auth: {
      type: provider === 'baidu' ? 'ak_sk' : 'bearer',
      apiKeyRef: profile.auth.apiKeyRef,
      secretKeyRef: provider === 'baidu' ? profile.auth.secretKeyRef : '',
    },
    baseUrl,
    model,
  }
}

function validate(config: OcrConfig): string[] {
  const errors: string[] = []
  if (!config.profiles.length) errors.push('至少配置一个 OCR profile')
  const ids = new Set<string>()
  for (const profile of config.profiles) {
    if (!profile.id.trim()) errors.push('profile id 不能为空')
    if (!/^[A-Za-z0-9_-]+$/.test(profile.id)) errors.push(`${profile.id}：profile id 只能包含字母、数字、下划线、短横线`)
    if (ids.has(profile.id)) errors.push(`profile id 重复：${profile.id}`)
    ids.add(profile.id)
    if (profile.mode === 'local') {
      if (profile.provider !== 'rapidocr') errors.push(`${profile.id}：本地 OCR 只支持 rapidocr`)
    } else if (profile.mode === 'cloud' || profile.mode === 'auto') {
      if (!profile.baseUrl.trim()) errors.push(`${profile.id}：云 OCR 需要 Base URL`)
      if (!profile.model.trim()) errors.push(`${profile.id}：云 OCR 需要 Model / 服务名`)
      const refs = derivedRefs(profile)
      if (!refs.apiKeyRef) errors.push(`${profile.id}：云 OCR 需要 API Key 引用`)
      if (profile.auth.type === 'ak_sk' && !refs.secretKeyRef) {
        errors.push(`${profile.id}：AK/SK 云 OCR 需要 Secret Key 引用`)
      }
    } else {
      errors.push(`${profile.id}：未知模式 ${profile.mode}`)
    }
  }
  if (!ids.has(config.defaultProfile)) errors.push('默认 profile 不存在')
  return errors
}

function isCloud(profile: OcrProfile): boolean {
  return profile.mode === 'cloud' || profile.mode === 'auto'
}

const labelStyle: CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: 4,
  fontSize: 12,
  color: 'var(--dsh-text-secondary, #666)',
}

const inputStyle: CSSProperties = {
  minWidth: 0,
  padding: '6px 8px',
  borderRadius: 6,
  border: '1px solid var(--dsh-border, #d0d0d0)',
  background: 'var(--dsh-bg, #fff)',
  color: 'inherit',
  fontSize: 13,
}

const cardStyle: CSSProperties = {
  border: '1px solid var(--dsh-border, #e0e0e0)',
  borderRadius: 8,
  padding: 12,
  display: 'flex',
  flexDirection: 'column',
  gap: 10,
}

const rowStyle: CSSProperties = {
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
  gap: 10,
}

function OcrSettingsPage({ ctx }: { ctx: Context }) {
  const scope = useMemo(() => ctx.configForms.get<XDshSettings>(NS), [ctx])
  const subscribe = useMemo(() => (listener: () => void) => scope.subscribe(listener), [scope])
  const getSnapshot = useMemo(() => () => scope.getSnapshot(), [scope])
  const snapshot = useSyncExternalStore(subscribe, getSnapshot, getSnapshot)

  const [draft, setDraft] = useState<OcrConfig | null>(null)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState('')
  const [message, setMessage] = useState('')
  const [keys, setKeys] = useState<Record<string, string>>({})
  const [credentials, setCredentials] = useState<Record<string, CredentialInfo>>({})
  const [credentialVersion, setCredentialVersion] = useState(0)

  useEffect(() => {
    if (snapshot.status !== 'ready' || snapshot.value === undefined) return
    if (dirty) return
    setDraft(normalizeConfig(snapshot.value.ocr))
  }, [snapshot.status, snapshot.revision, snapshot.value, dirty])

  const refs = useMemo(() => {
    if (!draft) return [] as string[]
    const result = new Set<string>()
    for (const profile of draft.profiles) {
      if (!isCloud(profile)) continue
      const ref = derivedRefs(profile)
      if (ref.apiKeyRef) result.add(ref.apiKeyRef)
      if (profile.auth.type === 'ak_sk' && ref.secretKeyRef) result.add(ref.secretKeyRef)
    }
    return [...result]
  }, [draft])

  const refKey = refs.join('|')

  useEffect(() => {
    let cancelled = false
    if (!refs.length) {
      setCredentials({})
      return
    }
    ctx.remote.credentials.describe(refs).then((response: CredentialDescribeResponse) => {
      if (cancelled || !response.ok) return
      setCredentials((response.value || {}) as Record<string, CredentialInfo>)
    }).catch(() => {})
    return () => { cancelled = true }
  }, [ctx, refKey, refs.length, credentialVersion])

  if (snapshot.status === 'unavailable') {
    return <p style={{ fontSize: 13 }}>OCR 设置不可用：当前 Host 没有暴露 {NS} 配置。</p>
  }
  if (!draft) {
    return <p style={{ fontSize: 13 }}>正在加载 OCR 配置…</p>
  }

  const update = (next: OcrConfig) => {
    setDraft(next)
    setDirty(true)
    setMessage('')
  }

  const updateProfile = (index: number, patch: Partial<OcrProfile>) => {
    const profiles = draft.profiles.map((profile, current) => current === index ? { ...profile, ...patch } : profile)
    const defaultProfile = patch.id !== undefined && draft.profiles[index]?.id === draft.defaultProfile
      ? patch.id
      : draft.defaultProfile
    update({ ...draft, defaultProfile, profiles })
  }

  const addProfile = () => {
    const used = new Set(draft.profiles.map((profile) => profile.id))
    let index = draft.profiles.length + 1
    while (used.has(`profile-${index}`)) index += 1
    const profile: OcrProfile = {
      ...defaultLocalProfile(),
      id: `profile-${index}`,
      name: `Profile ${index}`,
    }
    update({
      ...draft,
      defaultProfile: draft.defaultProfile || profile.id,
      profiles: [...draft.profiles, profile],
    })
  }

  const removeProfile = (index: number) => {
    const profiles = draft.profiles.filter((_, current) => current !== index)
    if (!profiles.length) return
    const defaultProfile = profiles.some((profile) => profile.id === draft.defaultProfile)
      ? draft.defaultProfile
      : profiles[0].id
    update({ ...draft, defaultProfile, profiles })
  }

  const save = async (): Promise<OcrConfig | null> => {
    if (!draft) return null
    const normalized: OcrConfig = {
      ...draft,
      profiles: draft.profiles.map((profile) => {
        const refs = derivedRefs(profile)
        return {
          ...profile,
          auth: {
            ...profile.auth,
            apiKeyRef: isCloud(profile) ? refs.apiKeyRef : '',
            secretKeyRef: isCloud(profile) && profile.auth.type === 'ak_sk' ? refs.secretKeyRef : '',
          },
        }
      }),
    }
    const errors = validate(normalized)
    if (errors.length) {
      setMessage(errors.join('；'))
      return null
    }
    setSaving(true)
    setMessage('')
    try {
      const accepted = await scope.mutate([
        { op: 'set', path: ['ocr', 'enabled'], value: normalized.enabled },
        { op: 'set', path: ['ocr', 'defaultProfile'], value: normalized.defaultProfile },
        { op: 'set', path: ['ocr', 'profiles'], value: normalized.profiles as unknown as JsonValue },
      ], snapshot.revision)
      if (!accepted) {
        setMessage('保存失败：配置可能已被其他页面修改，请重试')
        return null
      }
      for (const profile of normalized.profiles) {
        if (!isCloud(profile)) continue
        const refs = derivedRefs(profile)
        const apiKey = (keys[refs.apiKeyRef] || '').trim()
        if (apiKey) {
          const result = await ctx.remote.credentials.set(refs.apiKeyRef, apiKey)
          if (!result.ok) throw new Error(`API Key 保存失败：${refs.apiKeyRef}`)
        }
        if (profile.auth.type === 'ak_sk') {
          const secretKey = (keys[refs.secretKeyRef] || '').trim()
          if (secretKey) {
            const result = await ctx.remote.credentials.set(refs.secretKeyRef, secretKey)
            if (!result.ok) throw new Error(`Secret Key 保存失败：${refs.secretKeyRef}`)
          }
        }
      }
      setDraft(normalized)
      setKeys({})
      setDirty(false)
      setCredentialVersion((value) => value + 1)
      setMessage('已保存')
      return normalized
    } catch (error) {
      setMessage(`保存失败：${String(error)}`)
      return null
    } finally {
      setSaving(false)
    }
  }

  const runTest = async (profileId: string) => {
    setTesting(profileId)
    setMessage('')
    try {
      const saved = await save()
      if (!saved) return
      const response = await fetch('/x/dsh/ocr/test', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ profileId, ocr: saved }),
      })
      const body = await response.json().catch(() => ({})) as {
        ok?: boolean
        provider?: string
        model?: string
        chars?: number
        error?: string
      }
      if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`)
      if (body.ok) {
        setMessage(`测试成功：${body.provider || '-'} / ${body.model || '-'}，识别 ${body.chars ?? 0} 字`)
      } else {
        setMessage(`测试失败：${body.error || '未知错误'}`)
      }
    } catch (error) {
      setMessage(`测试失败：${String(error)}`)
    } finally {
      setTesting('')
    }
  }

  const clearCredential = async (ref: string) => {
    try {
      const response = await ctx.remote.credentials.unset(ref)
      if (!response.ok) throw new Error('unset failed')
      setCredentials((current) => ({ ...current, [ref]: { configured: false, writable: true } }))
    } catch (error) {
      setMessage(`清除密钥失败：${String(error)}`)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14, maxWidth: 900 }}>
      <div>
        <h3 style={{ margin: '0 0 6px' }}>OCR</h3>
        <p style={{ margin: 0, fontSize: 13, color: 'var(--dsh-text-secondary, #666)' }}>
          选择本地 OCR 或云 OCR。云 OCR 的密钥只保存为凭据引用，不会写入配置文件或日志。
        </p>
      </div>

      <div style={cardStyle}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(event) => update({ ...draft, enabled: event.target.checked })}
          />
          启用 OCR
        </label>

        <label style={labelStyle}>
          默认 Profile
          <select
            style={inputStyle}
            value={draft.defaultProfile}
            onChange={(event) => update({ ...draft, defaultProfile: event.target.value })}
          >
            {draft.profiles.map((profile, index) => (
              <option key={profile.id} value={profile.id}>
                {profileLabel(profile)} · {index + 1}
              </option>
            ))}
          </select>
        </label>
      </div>

      {draft.profiles.map((profile, index) => {
        const refs = derivedRefs(profile)
        const apiKeyState = credentials[refs.apiKeyRef]
        const secretState = credentials[refs.secretKeyRef]
        return (
          <div key={profile.id} style={cardStyle}>
            <label style={labelStyle}>
              类型
              <select
                style={inputStyle}
                value={profile.mode === 'local' ? 'local:rapidocr' : `cloud:${profile.provider}`}
                onChange={(event) => updateProfile(index, withProvider(profile, event.target.value))}
              >
                <option value="local:rapidocr">本地 OCR - RapidOCR</option>
                <option value="cloud:vision">云 OCR - OpenAI 兼容</option>
                <option value="cloud:baidu">云 OCR - 百度通用 OCR</option>
              </select>
            </label>

            {profile.mode !== 'local' && (
              <>
                <div style={rowStyle}>
                  <label style={labelStyle}>
                    Base URL
                    <input
                      style={inputStyle}
                      value={profile.baseUrl}
                      placeholder={profile.provider === 'baidu' ? 'https://aip.baidubce.com' : 'https://api.openai.com/v1'}
                      onChange={(event) => updateProfile(index, { baseUrl: event.target.value })}
                    />
                  </label>
                  <label style={labelStyle}>
                    Model / 服务名
                    <input
                      style={inputStyle}
                      value={profile.model}
                      placeholder={profile.provider === 'baidu' ? 'general_basic' : 'gpt-4o-mini'}
                      onChange={(event) => updateProfile(index, { model: event.target.value })}
                    />
                  </label>
                </div>
                <div style={rowStyle}>
                  <label style={labelStyle}>
                    API Key
                    <input
                      style={inputStyle}
                      type="password"
                      autoComplete="new-password"
                      value={keys[refs.apiKeyRef] || ''}
                      placeholder={apiKeyState?.configured ? '已配置（输入新值可覆盖）' : '请输入 API Key'}
                      onChange={(event) => { setKeys((current) => ({ ...current, [refs.apiKeyRef]: event.target.value })); setDirty(true) }}
                    />
                    {apiKeyState?.configured && (
                      <span style={{ fontSize: 12 }}>
                        已配置
                        <button
                          type="button"
                          style={{ marginLeft: 8, fontSize: 12 }}
                          onClick={() => void clearCredential(refs.apiKeyRef)}
                        >
                          清除
                        </button>
                      </span>
                    )}
                  </label>
                  {profile.auth.type === 'ak_sk' && (
                    <label style={labelStyle}>
                      Secret Key
                      <input
                        style={inputStyle}
                        type="password"
                        autoComplete="new-password"
                        value={keys[refs.secretKeyRef] || ''}
                        placeholder={secretState?.configured ? '已配置（输入新值可覆盖）' : '请输入 Secret Key'}
                        onChange={(event) => { setKeys((current) => ({ ...current, [refs.secretKeyRef]: event.target.value })); setDirty(true) }}
                      />
                      {secretState?.configured && (
                        <span style={{ fontSize: 12 }}>
                          已配置
                          <button
                            type="button"
                            style={{ marginLeft: 8, fontSize: 12 }}
                            onClick={() => void clearCredential(refs.secretKeyRef)}
                          >
                            清除
                          </button>
                        </span>
                      )}
                    </label>
                  )}
                </div>
              </>
            )}

            <div style={{ display: 'flex', gap: 8 }}>
              <button type="button" disabled={Boolean(testing) || saving} onClick={() => void runTest(profile.id)}>
                {testing === profile.id ? '测试中…' : '测试连接'}
              </button>
              <button type="button" disabled={draft.profiles.length <= 1} onClick={() => removeProfile(index)}>
                删除
              </button>
            </div>
          </div>
        )
      })}

      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <button type="button" onClick={addProfile}>添加 Profile</button>
        <button type="button" disabled={saving} onClick={() => void save()}>
          {saving ? '保存中…' : '保存'}
        </button>
      </div>

      {message && (
        <div style={{ fontSize: 13, color: message.includes('失败') || message.includes('错误') ? '#c0392b' : 'inherit' }}>
          {message}
        </div>
      )}
    </div>
  )
}

function SplitLlmSettingsPage({ ctx }: { ctx: Context }) {
  const scope = useMemo(() => ctx.configForms.get<XDshSettings>(NS), [ctx])
  const subscribe = useMemo(() => (listener: () => void) => scope.subscribe(listener), [scope])
  const getSnapshot = useMemo(() => () => scope.getSnapshot(), [scope])
  const snapshot = useSyncExternalStore(subscribe, getSnapshot, getSnapshot)

  const [draft, setDraft] = useState<SplitLlmConfig | null>(null)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [message, setMessage] = useState('')
  const [key, setKey] = useState('')
  const [credentials, setCredentials] = useState<Record<string, CredentialInfo>>({})
  const [credentialVersion, setCredentialVersion] = useState(0)

  useEffect(() => {
    if (snapshot.status !== 'ready' || snapshot.value === undefined) return
    if (dirty) return
    setDraft(normalizeSplitLlm(snapshot.value.splitLlm))
  }, [snapshot.status, snapshot.revision, snapshot.value, dirty])

  const apiKeyRef = draft?.mode === 'custom' ? SPLIT_LLM_DEFAULTS.apiKeyRef : ''

  useEffect(() => {
    let cancelled = false
    if (!apiKeyRef) {
      setCredentials({})
      return
    }
    ctx.remote.credentials.describe([apiKeyRef]).then((response: CredentialDescribeResponse) => {
      if (cancelled || !response.ok) return
      setCredentials((response.value || {}) as Record<string, CredentialInfo>)
    }).catch(() => {})
    return () => { cancelled = true }
  }, [ctx, apiKeyRef, credentialVersion])

  if (snapshot.status === 'unavailable') {
    return <p style={{ fontSize: 13 }}>语义分块 LLM 设置不可用：当前 Host 没有暴露 {NS} 配置。</p>
  }
  if (!draft) {
    return <p style={{ fontSize: 13 }}>正在加载语义分块 LLM 配置…</p>
  }

  const update = (patch: Partial<SplitLlmConfig>) => {
    setDraft({ ...draft, ...patch })
    setDirty(true)
    setMessage('')
  }

  const save = async (): Promise<SplitLlmConfig | null> => {
    if (!draft) return null
    const normalized: SplitLlmConfig = {
      mode: draft.mode,
      baseUrl: draft.baseUrl.trim(),
      model: draft.model.trim(),
      apiKeyRef: SPLIT_LLM_DEFAULTS.apiKeyRef,
      timeout: draft.timeout,
      maxRetries: draft.maxRetries,
    }
    const errors = validateSplitLlm(normalized)
    if (errors.length) {
      setMessage(errors.join('；'))
      return null
    }
    setSaving(true)
    setMessage('')
    try {
      const accepted = await scope.mutate([
        { op: 'set', path: ['splitLlm', 'mode'], value: normalized.mode },
        { op: 'set', path: ['splitLlm', 'baseUrl'], value: normalized.baseUrl },
        { op: 'set', path: ['splitLlm', 'model'], value: normalized.model },
        { op: 'set', path: ['splitLlm', 'apiKeyRef'], value: normalized.apiKeyRef },
        { op: 'set', path: ['splitLlm', 'timeout'], value: normalized.timeout },
        { op: 'set', path: ['splitLlm', 'maxRetries'], value: normalized.maxRetries },
      ], snapshot.revision)
      if (!accepted) {
        setMessage('保存失败：配置可能已被其他页面修改，请重试')
        return null
      }
      const apiKey = key.trim()
      if (normalized.mode === 'custom' && apiKey) {
        const result = await ctx.remote.credentials.set(normalized.apiKeyRef, apiKey)
        if (!result.ok) throw new Error(`API Key 保存失败：${normalized.apiKeyRef}`)
      }
      setDraft(normalized)
      setKey('')
      setDirty(false)
      setCredentialVersion((value) => value + 1)
      setMessage('已保存，下一次拆分任务生效')
      return normalized
    } catch (error) {
      setMessage(`保存失败：${String(error)}`)
      return null
    } finally {
      setSaving(false)
    }
  }

  const runTest = async () => {
    setTesting(true)
    setMessage('')
    try {
      const saved = await save()
      if (!saved) return
      // 主 Agent 模式由 Host 在测试时即时解析 Models/默认模型配置。
      const response = await fetch('/x/dsh/llm/test', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({}),
      })
      const body = await response.json().catch(() => ({})) as {
        ok?: boolean
        baseUrl?: string
        model?: string
        duration?: number
        textPreview?: string
        error?: string
      }
      if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`)
      if (body.ok) {
        setMessage(
          `测试成功：${body.model || '-'} @ ${body.baseUrl || '-'}，`
          + `耗时 ${body.duration ?? 0}s，回复：${body.textPreview || '-'}`,
        )
      } else {
        setMessage(`测试失败：${body.error || '未知错误'}`)
      }
    } catch (error) {
      setMessage(`测试失败：${String(error)}`)
    } finally {
      setTesting(false)
    }
  }

  const clearCredential = async (ref: string) => {
    try {
      const response = await ctx.remote.credentials.unset(ref)
      if (!response.ok) throw new Error('unset failed')
      setCredentials((current) => ({ ...current, [ref]: { configured: false, writable: true } }))
    } catch (error) {
      setMessage(`清除密钥失败：${String(error)}`)
    }
  }

  const state = credentials[apiKeyRef]
  const savedConfig = snapshot.value?.splitLlm
  const configured = savedConfig !== undefined && Object.keys(savedConfig).length > 0

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14, maxWidth: 900 }}>
      <div>
        <h3 style={{ margin: '0 0 6px' }}>语义分块 LLM</h3>
        <p style={{ margin: 0, fontSize: 13, color: 'var(--dsh-text-secondary, #666)' }}>
          配置需求语义分块使用的模型。可以跟随主Agent的默认模型，也可以单独添加一套OpenAI兼容配置。
        </p>
      </div>

      <div style={cardStyle}>
        <label style={labelStyle}>
          模型来源
          <select
            style={inputStyle}
            value={draft.mode}
            onChange={(event) => update({ mode: event.target.value === 'custom' ? 'custom' : 'main' })}
          >
            <option value="main">使用主Agent模型</option>
            <option value="custom">自定义模型</option>
          </select>
        </label>

        {draft.mode === 'main' ? (
          <p style={{ margin: 0, fontSize: 13, color: 'var(--dsh-text-secondary, #666)' }}>
            使用主Agent当前的默认模型与API Key。
          </p>
        ) : (
          <>
            <div style={rowStyle}>
              <label style={labelStyle}>
                Base URL
                <input
                  style={inputStyle}
                  value={draft.baseUrl}
                  placeholder={SPLIT_LLM_DEFAULTS.baseUrl}
                  onChange={(event) => update({ baseUrl: event.target.value })}
                />
              </label>
              <label style={labelStyle}>
                Model
                <input
                  style={inputStyle}
                  value={draft.model}
                  placeholder={SPLIT_LLM_DEFAULTS.model}
                  onChange={(event) => update({ model: event.target.value })}
                />
              </label>
            </div>
            <label style={labelStyle}>
              API Key
              <input
                style={inputStyle}
                type="password"
                autoComplete="new-password"
                value={key}
                placeholder={state?.configured ? '已配置（输入新值可覆盖）' : '请输入 API Key'}
                onChange={(event) => { setKey(event.target.value); setDirty(true) }}
              />
              {state?.configured && (
                <span style={{ fontSize: 12 }}>
                  已配置
                  <button
                    type="button"
                    style={{ marginLeft: 8, fontSize: 12 }}
                    onClick={() => void clearCredential(apiKeyRef)}
                  >
                    清除
                  </button>
                </span>
              )}
            </label>
          </>
        )}

        {!configured && (
          <p style={{ margin: 0, fontSize: 12, color: 'var(--dsh-text-secondary, #666)' }}>
            当前未保存本页配置，仍在使用服务端环境变量；点击“保存”后启用上面的模型来源。
          </p>
        )}
        <div style={{ display: 'flex', gap: 8 }}>
          <button type="button" disabled={testing || saving} onClick={() => void runTest()}>
            {testing ? '测试中…' : '测试连接'}
          </button>
          <button type="button" disabled={saving} onClick={() => void save()}>
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </div>

      {message && (
        <div style={{ fontSize: 13, color: message.includes('失败') || message.includes('错误') ? '#c0392b' : 'inherit' }}>
          {message}
        </div>
      )}
    </div>
  )
}
