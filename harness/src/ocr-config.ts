import z from '@deepseek-ai/schemastery'

/** One OCR provider profile, used by both the settings page and the task payload. */
export interface OcrAuth {
  /** none for local OCR, bearer for OpenAI-compatible cloud OCR, ak_sk for native vendors. */
  type: string
  /** Credential reference (environment-variable name), never the secret itself. */
  apiKeyRef: string
  /** Second credential reference for AK/SK vendors such as Baidu. */
  secretKeyRef: string
}

export interface OcrProfile {
  id: string
  name: string
  /** local | cloud | auto */
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

export interface OcrConfig {
  enabled: boolean
  defaultProfile: string
  profiles: OcrProfile[]
}

/** Settings namespace is the profile entry id from cordis.patch.yml. */
export const OCR_SETTINGS_NAMESPACE = 'x-dsh-registry'

export const OcrConfigSchema = z.object({
  enabled: z.boolean().default(true),
  defaultProfile: z.string().default(''),
  profiles: z.array(z.object({
    id: z.string().required(),
    name: z.string().default(''),
    mode: z.string().default('local'),
    provider: z.string().default('rapidocr'),
    language: z.string().default('chi_sim'),
    dpi: z.natural().default(180),
    maxPages: z.natural().default(50),
    concurrency: z.natural().default(2),
    baseUrl: z.string().default(''),
    model: z.string().default(''),
    region: z.string().default(''),
    auth: z.object({
      type: z.string().default('none'),
      apiKeyRef: z.string().default(''),
      secretKeyRef: z.string().default(''),
    }).default({}),
    options: z.dict(z.string()).default({}),
    timeout: z.number().default(60),
  })).default([]),
}).volatile()

/** Read the live OCR section and detach it from the cordis config reference. */
export function ocrInput(config: { ocr?: { get(): unknown } }): OcrConfig | undefined {
  try {
    const value = config.ocr?.get()
    return value === undefined ? undefined : JSON.parse(JSON.stringify(value)) as OcrConfig
  } catch {
    return undefined
  }
}
