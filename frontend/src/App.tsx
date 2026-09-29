import { useEffect, useMemo, useState } from 'react'
import {
  Activity,
  Aperture,
  ArrowDownToLine,
  BookOpen,
  Check,
  ChevronRight,
  Download,
  ImagePlus,
  Palette,
  Plus,
  Power,
  RefreshCw,
  Save,
  Settings2,
  ShieldCheck,
  Sparkles,
  Trash2,
  Upload,
  X,
} from 'lucide-react'

// 模型给出的参数越界时，后端会把溢出清单回传模型重试；暗角这类合法但会在画面上画出
// 硬边圆形的参数也会被回传并最终收敛。两种情况都随建议一起返回这份报告。
type OverflowReport = {
  attempts: number
  rounds: number
  repaired: boolean
  resolved: string[]
  unresolved: string[]
  dropped_keys: string[]
  adjusted: string[]
  notice: string
}
type Suggestion = { summary: string; settings: Record<string, SettingValue>; overflow?: OverflowReport }
type Health = { status: string; provider: 'ollama' | 'openai'; model: string; model_active: boolean; model_message: string; lightroom: string }
type ModelConfiguration = {
  provider: 'ollama' | 'openai'
  model_name: string
  ollama_base_url: string
  openai_base_url: string
  api_key: string
  api_key_configured?: boolean
}
type LocalModel = { name: string; size: number; modified_at: string; family: string; parameter_size: string }
type LightroomStatus = { connected: boolean; selected_filename: string; last_seen_seconds: number | null }
type LightroomAction = 'preview' | 'export'
type DevelopKind = 'number' | 'enum' | 'bool' | 'curve'
type SettingValue = number | string | boolean | number[][]
type DevelopControl = {
  key: string
  label: string
  group: string
  controller: string
  kind: DevelopKind
  minimum: number
  maximum: number
  step: number
  default: SettingValue
  choices: [number | string, string][]
  core: boolean
  experimental: boolean
  model_default: boolean
  description: string
}
type DevelopGroup = { id: string; label: string; controls: DevelopControl[] }
type DevelopRegistry = { groups: DevelopGroup[]; model_keys: string[]; core_keys: string[]; mask_keys: string[]; total: number }

const API = 'http://127.0.0.1:8000'
// 静态使用指南：随 Vite public/ 目录一起发布，地址 /使用指南.html
const GUIDE_URL = encodeURI('/使用指南.html')
const RAW_EXTENSIONS = new Set([
  '.3fr', '.ari', '.arw', '.bay', '.cap', '.cr2', '.cr3', '.crw', '.dcs', '.dcr',
  '.dng', '.drf', '.eip', '.erf', '.fff', '.gpr', '.iiq', '.k25', '.kdc', '.mef',
  '.mos', '.mrw', '.nef', '.nrw', '.obm', '.orf', '.pef', '.ptx', '.pxn', '.raf',
  '.raw', '.rwl', '.rw2', '.rwz', '.sr2', '.srf', '.srw', '.x3f',
])
const PHOTO_ACCEPT = `image/*,${Array.from(RAW_EXTENSIONS).join(',')}`

function isRawPhoto(filename: string) {
  return RAW_EXTENSIONS.has(filename.slice(filename.lastIndexOf('.')).toLowerCase())
}

function formatModelSize(bytes: number) {
  if (!bytes) return '大小未知'
  return bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(1)} GB` : `${(bytes / 1024 ** 2).toFixed(0)} MB`
}

const defaultModelConfig: ModelConfiguration = {
  provider: 'ollama',
  model_name: 'qwen2.5vl:7b',
  ollama_base_url: 'http://127.0.0.1:11434',
  openai_base_url: 'https://api.openai.com/v1',
  api_key: '',
}
// 预设调色方向（创作说明模板）来自项目根目录的 color_presets.json：网页通过
// GET /api/presets 读取，抽屉里新建与删除都会写回同一个文件，因此前端不再硬编码文案。
// 后端已经把每条预设的 steps / constraints / params_hint 合成好一句 instruction：点卡片就是把它
// 填进创作说明输入框，之后仍可自由改写；没选预设时输入框就是一段自由文本，两种方式共用同一个框，
// 所以「纯文本调色」这条路径一直保留。params_hint 合成的「参考参数」只是建议，不是强制规范。
type PresetPrompt = {
  steps: string[]
  constraints: string[]
  params_hint: Record<string, string>
  text: string
  structured: boolean
}
type ColorPreset = {
  id: string
  name: string
  tags: string[]
  summary: string
  prompt: PresetPrompt
  instruction: string
  default_intensity?: number | null
  created_at?: string
}
type PresetRegistry = { version?: string; presets: ColorPreset[]; file: string; path?: string; params_hint_options?: PresetHintOption[] }
// 「参数参考」的候选参数名：name 写进 JSON 的键，label 是后端合成创作说明时的标注
// （highlights -> 高光(Highlights2012)），所以表单预览与最终发给模型的那一行完全一致。
type PresetHintOption = { name: string; label: string }
// 表单里的一行参数参考：参数名 + 建议区间；两格都填了才写进 params_hint。
type PresetHintRow = { name: string; value: string }
type PresetDraft = { name: string; tag: string; summary: string; prompt: string; hints: PresetHintRow[] }

const PROMPT_MAX_LENGTH = 500
const PRESET_NAME_MAX_LENGTH = 24
const PRESET_TAG_MAX_LENGTH = 8
const PRESET_SUMMARY_MAX_LENGTH = 60
// 与 backend/app/presets.py 的 PRESET_HINT_* 保持一致。
const PRESET_HINT_MAX_COUNT = 16
const PRESET_HINT_KEY_MAX_LENGTH = 24
const PRESET_HINT_VALUE_MAX_LENGTH = 32
// 参考参数那一行的固定标题，与后端 presets.PARAMS_HINT_TITLE 一致（llm.py 靠它识别建议块）。
const PARAMS_HINT_TITLE = '参考参数（建议，非强制）：'
// 每次都发一份全新的草稿：hints 是数组，共用同一个常量容易被就地改到。
const emptyPresetDraft = (): PresetDraft => ({
  name: '',
  tag: '',
  summary: '',
  prompt: '',
  hints: [{ name: '', value: '' }],
})

// 蒙版 = 效果面板里的「裁剪后暗角」五项。默认关闭：关闭时大模型不会被允许给出这组参数，
// 已经加入本次渲染的蒙版值也会被移除。强度快捷键给出的取值都落在不会画出圆形硬边的窗口内
// （圆度 0、羽化 60、中点 50，数量由按钮决定），用户可以再在参数里手动微调。
const MASK_STRENGTHS: { id: string; label: string; amount: number }[] = [
  { id: 'light', label: '轻', amount: -15 },
  { id: 'medium', label: '中', amount: -30 },
  { id: 'strong', label: '强', amount: -50 },
]
const MASK_FRAME: Record<string, SettingValue> = {
  PostCropVignetteFeather: 60,
  PostCropVignetteMidpoint: 50,
  PostCropVignetteRoundness: 0,
  PostCropVignetteStyle: 1,
}
// 调色参数表由后端注册表提供（GET /api/develop/controls），前端只保留兜底与解析工具。
function genericControl(key: string): DevelopControl {
  return {
    key,
    label: key,
    group: 'basic',
    controller: key,
    kind: 'number',
    minimum: -100,
    maximum: 100,
    step: 1,
    default: 0,
    choices: [],
    core: false,
    experimental: false,
    model_default: true,
    description: '',
  }
}

function parseReportList(report: string, prefix: string): string[] {
  const line = report.split('\n').find((item) => item.startsWith(prefix))
  if (!line) return []
  return line.slice(prefix.length).split(',').map((item) => item.trim()).filter(Boolean)
}

function curveToText(value: SettingValue): string {
  if (!Array.isArray(value)) return ''
  return value.map((point) => `${point[0]},${point[1]}`).join(';')
}

function parseCurveText(text: string): number[][] | undefined {
  const points: number[][] = []
  for (const chunk of text.split(';')) {
    const trimmed = chunk.trim()
    if (!trimmed) continue
    const parts = trimmed.split(',').map((part) => Number(part.trim()))
    if (parts.length !== 2 || parts.some((part) => !Number.isFinite(part))) return undefined
    if (parts[0] < 0 || parts[0] > 255 || parts[1] < 0 || parts[1] > 255) return undefined
    points.push([parts[0], parts[1]])
  }
  if (points.length < 2) return undefined
  if (points.some((point, index) => index > 0 && point[0] <= points[index - 1][0])) return undefined
  return points
}

function formatSettingValue(value: SettingValue | undefined): string {
  if (typeof value === 'number') return Number.isInteger(value) ? `${value}` : value.toFixed(2)
  if (typeof value === 'boolean') return value ? '开' : '关'
  if (typeof value === 'string') return value
  return value ? curveToText(value) : ''
}

// 抽屉卡片的两行预览：正文取步骤（纯文本预设就取它那段话），另一行标注强度与建议参数条数。
// 真正发给模型的是后端合成好的 instruction，卡片刻意不铺开整段，免得列表太长。
function presetPreview(preset: ColorPreset) {
  return preset.prompt.structured ? preset.prompt.steps.join('；') : preset.prompt.text
}

function presetMeta(preset: ColorPreset) {
  const hints = Object.keys(preset.prompt.params_hint ?? {}).length
  const parts: string[] = []
  if (hints > 0) parts.push(`参考参数 ${hints} 项`)
  if (preset.default_intensity) parts.push(`强度 ${preset.default_intensity}`)
  return parts.join(' · ')
}

// 后端自检的报错是中文 detail 字符串；FastAPI 的请求体校验失败会给 detail 数组。
function readFailure(data: unknown, fallback: string): string {
  if (data && typeof data === 'object' && 'detail' in data) {
    const detail = (data as { detail: unknown }).detail
    if (typeof detail === 'string' && detail) return detail
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: unknown }
      if (typeof first?.msg === 'string') return `${fallback}（${first.msg}）`
    }
  }
  return fallback
}

export default function App() {
  const [photo, setPhoto] = useState<File | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string>()
  const [previewLoading, setPreviewLoading] = useState(false)
  // 输入模式：'prompt' 只用文字方向；'reference' 额外上传一张参考图，让模型借用它的调色风格。
  const [inputMode, setInputMode] = useState<'prompt' | 'reference'>('prompt')
  const [referencePhoto, setReferencePhoto] = useState<File | null>(null)
  const [referencePreviewUrl, setReferencePreviewUrl] = useState<string>()
  const [referenceLoading, setReferenceLoading] = useState(false)
  const [lightroomPreviewUrl, setLightroomPreviewUrl] = useState<string>()
  const [previewMode, setPreviewMode] = useState<'original' | 'lightroom'>('original')
  const [prompt, setPrompt] = useState('自然通透，保留天空层次，绿色不要过饱和')
  const [suggestion, setSuggestion] = useState<Suggestion>()
  const [health, setHealth] = useState<Health>()
  const [lightroomStatus, setLightroomStatus] = useState<LightroomStatus>({ connected: false, selected_filename: '', last_seen_seconds: null })
  const [lightroomBusy, setLightroomBusy] = useState(false)
  const [lightroomMessage, setLightroomMessage] = useState('')
  const [lightroomError, setLightroomError] = useState('')
  const [exportFormat, setExportFormat] = useState<'JPEG' | 'PNG' | 'TIFF'>('JPEG')
  const [exportQuality, setExportQuality] = useState(92)
  const [exportMaxDimension, setExportMaxDimension] = useState(6000)
  const [modelConfig, setModelConfig] = useState<ModelConfiguration>(defaultModelConfig)
  const [localModels, setLocalModels] = useState<LocalModel[]>([])
  const [localModelsLoading, setLocalModelsLoading] = useState(false)
  const [localModelsError, setLocalModelsError] = useState('')
  const [modelListRefreshKey, setModelListRefreshKey] = useState(0)
  const [showModelSettings, setShowModelSettings] = useState(false)
  const [modelBusy, setModelBusy] = useState(false)
  const [modelMessage, setModelMessage] = useState('')
  const [modelError, setModelError] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [registry, setRegistry] = useState<DevelopRegistry>()
  // 允许大模型调整的参数集合：默认状态由后端注册表的 model_default 决定（实验参数与
  // 蒙版即裁剪后暗角默认关闭），面板开关只控制这一范围。
  const [allowedKeys, setAllowedKeys] = useState<Record<string, boolean>>({})
  const [settings, setSettings] = useState<Record<string, SettingValue>>({})
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({ basic: true })
  const [curveDrafts, setCurveDrafts] = useState<Record<string, string>>({})
  const [probeBusy, setProbeBusy] = useState(false)
  const [probeResult, setProbeResult] = useState<{ supported: string[]; unsupported: string[]; detail: string }>()
  const [lightroomWarning, setLightroomWarning] = useState('')
  // 预设抽屉只改写创作说明（提示词），不直接写入 LrC 参数，抽屉关闭后提示仍可编辑。
  const [showPresets, setShowPresets] = useState(false)
  const [presetNotice, setPresetNotice] = useState('')
  // 预设来自后端读取的 color_presets.json；新建与删除都写回这个文件，所以列表以接口返回为准。
  const [presets, setPresets] = useState<ColorPreset[]>([])
  const [presetsFile, setPresetsFile] = useState('color_presets.json')
  const [presetsError, setPresetsError] = useState('')
  const [presetFormOpen, setPresetFormOpen] = useState(false)
  const [presetDraft, setPresetDraft] = useState<PresetDraft>(emptyPresetDraft())
  // 新建表单的「参数参考」候选名（后端随预设一起返回），用于 datalist 与合成预览。
  const [presetHintOptions, setPresetHintOptions] = useState<PresetHintOption[]>([])
  const [presetBusy, setPresetBusy] = useState(false)
  const [pendingDelete, setPendingDelete] = useState('')

  const controlIndex = useMemo(() => {
    const index: Record<string, DevelopControl> = {}
    registry?.groups.forEach((group) => group.controls.forEach((control) => { index[control.key] = control }))
    return index
  }, [registry])

  // 蒙版 = 后端注册表里的 mask_keys（效果面板的裁剪后暗角五项）。
  const maskKeys = useMemo(() => registry?.mask_keys ?? [], [registry])

  // 参数参考的候选名 -> 后端标注：数据来自 presets.params_hint_options()，与预设同一个请求返回，
  // 所以表单里预览的那一行和后端合成进创作说明的那一行写法完全一致。
  const presetHintLabels = useMemo(
    () => new Map(presetHintOptions.map((option) => [option.name, option.label])),
    [presetHintOptions],
  )

  useEffect(() => {
    let cancelled = false
    let objectUrl: string | undefined
    if (!photo) {
      setPreviewUrl(undefined)
      setPreviewLoading(false)
      return () => { cancelled = true }
    }
    const selectedPhoto = photo
    const raw = isRawPhoto(selectedPhoto.name)
    setPreviewUrl(undefined)
    setError('')
    setPreviewLoading(raw)

    async function loadPreview() {
      try {
        let image: Blob = selectedPhoto
        if (raw) {
          const body = new FormData()
          body.append('photo', selectedPhoto)
          const response = await fetch(`${API}/api/images/preview`, { method: 'POST', body })
          if (!response.ok) {
            const data = await response.json()
            throw new Error(data.detail || '无法生成 RAW 预览。')
          }
          image = await response.blob()
        }
        const url = URL.createObjectURL(image)
        if (cancelled) {
          URL.revokeObjectURL(url)
          return
        }
        objectUrl = url
        setPreviewUrl(url)
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : '无法读取这张照片。')
      } finally {
        if (!cancelled) setPreviewLoading(false)
      }
    }

    void loadPreview()
        return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [photo])

  useEffect(() => {
    return () => {
      if (lightroomPreviewUrl) URL.revokeObjectURL(lightroomPreviewUrl)
    }
  }, [lightroomPreviewUrl])

  // 参考图和目标图走同一套逻辑：RAW 交给后端转 JPEG 预览，普通图片本地直接建 objectURL。
  useEffect(() => {
    let cancelled = false
    let objectUrl: string | undefined
    if (!referencePhoto) {
      setReferencePreviewUrl(undefined)
      setReferenceLoading(false)
      return () => { cancelled = true }
    }
    const selected = referencePhoto
    const raw = isRawPhoto(selected.name)
    setReferencePreviewUrl(undefined)
    setReferenceLoading(raw)

    async function loadReferencePreview() {
      try {
        let image: Blob = selected
        if (raw) {
          const body = new FormData()
          body.append('photo', selected)
          const response = await fetch(`${API}/api/images/preview`, { method: 'POST', body })
          if (!response.ok) {
            const data = await response.json()
            throw new Error(data.detail || '无法生成参考图预览。')
          }
          image = await response.blob()
        }
        const url = URL.createObjectURL(image)
        if (cancelled) {
          URL.revokeObjectURL(url)
          return
        }
        objectUrl = url
        setReferencePreviewUrl(url)
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : '无法读取这张参考图。')
      } finally {
        if (!cancelled) setReferenceLoading(false)
      }
    }

    void loadReferencePreview()
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [referencePhoto])

  function selectPhoto(file: File | undefined) {
    setPhoto(file ?? null)
    setSuggestion(undefined)
    setLightroomPreviewUrl(undefined)
    setPreviewMode('original')
    setError('')
  }

  function selectReference(file: File | undefined) {
    setReferencePhoto(file ?? null)
    setSuggestion(undefined)
    setLightroomPreviewUrl(undefined)
    setPreviewMode('original')
    setError('')
  }

  // 切到“文字方向”时清掉参考图，避免离开参考图模式后仍把旧图发给模型。
  function switchInputMode(mode: 'prompt' | 'reference') {
    setInputMode(mode)
    if (mode === 'prompt') setReferencePhoto(null)
    setError('')
  }

  useEffect(() => {
    Promise.all([
      fetch(`${API}/api/health`).then((response) => response.json() as Promise<Health>),
      fetch(`${API}/api/model/config`).then((response) => response.json() as Promise<Partial<ModelConfiguration>>),
    ]).then(([status, config]) => {
      setHealth(status)
      setModelConfig({ ...defaultModelConfig, ...config, api_key: '' })
    }).catch(() => setHealth(undefined))
  }, [])

  useEffect(() => {
    let cancelled = false
    fetch(`${API}/api/develop/controls`)
      .then((response) => response.json() as Promise<DevelopRegistry>)
      .then((data) => {
        if (cancelled) return
        setRegistry(data)
        setAllowedKeys(
          Object.fromEntries(
            data.groups
              .flatMap((group) => group.controls)
              .map((control) => [control.key, control.model_default]),
          ),
        )
      })
      .catch(() => { if (!cancelled) setRegistry(undefined) })
    return () => { cancelled = true }
  }, [])

  // 调色预设与参数表一样只在挂载时拉一次：抽屉里的新建与删除用接口返回的最新列表覆盖。
  useEffect(() => {
    let cancelled = false
    fetch(`${API}/api/presets`)
      .then(async (response) => {
        const data = await response.json()
        if (!response.ok) throw new Error(readFailure(data, '读取预设文件失败。'))
        return data as PresetRegistry
      })
      .then((data) => {
        if (cancelled) return
        setPresets(data.presets ?? [])
        setPresetsFile(data.file || 'color_presets.json')
        setPresetHintOptions(data.params_hint_options ?? [])
        setPresetsError('')
      })
      .catch((cause) => {
        if (!cancelled) setPresetsError(cause instanceof Error ? cause.message : '读取预设文件失败。')
      })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (!showModelSettings || modelConfig.provider !== 'ollama') return
    let cancelled = false
    setLocalModelsLoading(true)
    setLocalModelsError('')
    fetch(`${API}/api/models/local`)
      .then(async (response) => {
        const data = await response.json()
        if (!response.ok) throw new Error(data.detail || '检测本地模型失败。')
        return data.models as LocalModel[]
      })
      .then((models) => {
        if (!cancelled) setLocalModels(models)
      })
      .catch((cause) => {
        if (!cancelled) setLocalModelsError(cause instanceof Error ? cause.message : '检测本地模型失败。')
      })
      .finally(() => {
        if (!cancelled) setLocalModelsLoading(false)
      })
    return () => { cancelled = true }
  }, [showModelSettings, modelConfig.provider, modelListRefreshKey])

  useEffect(() => {
    let cancelled = false
    const refreshStatus = async () => {
      try {
        const response = await fetch(`${API}/api/lightroom/status`)
        if (!response.ok) return
        const status = await response.json() as LightroomStatus
        if (!cancelled) setLightroomStatus(status)
      } catch {
        if (!cancelled) setLightroomStatus({ connected: false, selected_filename: '', last_seen_seconds: null })
      }
    }
    void refreshStatus()
    const interval = window.setInterval(() => { void refreshStatus() }, 2500)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [])

  // 抽屉打开时按 Esc 关闭，与模型设置对话框的交互保持一致；新建表单展开时先收起表单。
  useEffect(() => {
    if (!showPresets) return
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      if (presetFormOpen) {
        setPresetFormOpen(false)
        return
      }
      setShowPresets(false)
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [showPresets, presetFormOpen])

  // 填入预设后的提示会自己消失，避免长期占用创作说明下方的位置。
  useEffect(() => {
    if (!presetNotice) return
    const timer = window.setTimeout(() => setPresetNotice(''), 4000)
    return () => window.clearTimeout(timer)
  }, [presetNotice])

  // 点卡片只改写创作说明：填入的是后端按 steps / constraints / params_hint 合成好的整段文字，
  // 用户可以继续删改，也可以直接清空改成自己的纯文本方向。
  function applyPreset(preset: ColorPreset) {
    setPrompt(preset.instruction.slice(0, PROMPT_MAX_LENGTH))
    setPresetNotice(`已填入预设「${preset.name}」，可继续修改`)
    setShowPresets(false)
  }

  // 表单里的参数参考：成对填写的行才进 params_hint，整块留空就完全不写。
  const presetHintItems = presetDraft.hints
    .map((row) => ({ name: row.name.trim(), value: row.value.trim() }))
    .filter((row) => row.name && row.value)
    .map((row) => `${presetHintLabels.get(row.name) ?? row.name} ${row.value}`)
  // 合成预览与长度：与后端 _compose_instruction() 一致，参考参数另起一行接在正文后面。
  const presetHintLine = presetHintItems.length ? PARAMS_HINT_TITLE + presetHintItems.join('；') : ''
  const presetDraftLength = presetDraft.prompt.trim().length + (presetHintLine ? presetHintLine.length + 1 : 0)

  function updateHintRow(index: number, patch: Partial<PresetHintRow>) {
    setPresetDraft((draft) => ({
      ...draft,
      hints: draft.hints.map((row, position) => (position === index ? { ...row, ...patch } : row)),
    }))
  }

  function addHintRow() {
    setPresetDraft((draft) => (
      draft.hints.length >= PRESET_HINT_MAX_COUNT ? draft : { ...draft, hints: [...draft.hints, { name: '', value: '' }] }
    ))
  }

  function removeHintRow(index: number) {
    // 始终留一行：想清空时直接删掉输入内容即可，不必先删行再加行。
    setPresetDraft((draft) => ({
      ...draft,
      hints: draft.hints.length > 1
        ? draft.hints.filter((_row, position) => position !== index)
        : [{ name: '', value: '' }],
    }))
  }

  // 新建表单用当前创作说明预填提示词：多数时候用户是在改写一段已有的意图。
  // 只填提示词时按纯文本形态写入；再填上「参数参考」，后端就写成 { text, params_hint } 对象。
  function openPresetForm() {
    setPresetFormOpen(true)
    setPendingDelete('')
    setPresetsError('')
    setPresetDraft({ ...emptyPresetDraft(), prompt: prompt.slice(0, PROMPT_MAX_LENGTH) })
  }

  async function createPresetFromDraft() {
    if (presetBusy) return
    // 参数参考要么整行留空、要么两格都填：半行会合成出没有取值的建议。
    const rows = presetDraft.hints
      .map((row) => ({ name: row.name.trim(), value: row.value.trim() }))
      .filter((row) => row.name || row.value)
    if (rows.some((row) => !row.name || !row.value)) {
      setPresetsError('参数参考要成对填写：每行都写上参数名与建议区间，用不到的行请删掉。')
      return
    }
    if (new Set(rows.map((row) => row.name)).size !== rows.length) {
      setPresetsError('参数参考里有重复的参数名，请合并成一行。')
      return
    }
    if (presetDraftLength > PROMPT_MAX_LENGTH) {
      setPresetsError(`提示词加上参考参数合成后共 ${presetDraftLength} 个字，超过 ${PROMPT_MAX_LENGTH} 字，请精简后再保存。`)
      return
    }
    setPresetBusy(true)
    setPresetsError('')
    try {
      const response = await fetch(`${API}/api/presets`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: presetDraft.name,
          tag: presetDraft.tag,
          summary: presetDraft.summary,
          prompt: presetDraft.prompt,
          // 留空就是空对象：后端据此决定写成字符串还是 { text, params_hint } 对象。
          params_hint: Object.fromEntries(rows.map((row) => [row.name, row.value])),
        }),
      })
      const data = await response.json()
      if (!response.ok) throw new Error(readFailure(data, '新建预设失败。'))
      const created = data.preset as ColorPreset
      setPresets(data.presets as ColorPreset[])
      setPresetDraft(emptyPresetDraft())
      setPresetFormOpen(false)
      setPresetNotice(`已把「${created.name}」写入 ${presetsFile}`)
    } catch (cause) {
      setPresetsError(cause instanceof Error ? cause.message : '新建预设失败。')
    } finally {
      setPresetBusy(false)
    }
  }

  async function removePreset(preset: ColorPreset) {
    if (presetBusy) return
    setPresetBusy(true)
    setPresetsError('')
    try {
      const response = await fetch(`${API}/api/presets/${encodeURIComponent(preset.id)}`, { method: 'DELETE' })
      const data = await response.json()
      if (!response.ok) throw new Error(readFailure(data, '删除预设失败。'))
      setPresets(data.presets as ColorPreset[])
      setPendingDelete('')
      setPresetNotice(`已从 ${presetsFile} 删除「${preset.name}」`)
    } catch (cause) {
      setPresetsError(cause instanceof Error ? cause.message : '删除预设失败。')
    } finally {
      setPresetBusy(false)
    }
  }

  async function persistModelConfig() {
    const response = await fetch(`${API}/api/model/config`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(modelConfig),
    })
    const data = await response.json()
    if (!response.ok) throw new Error(data.detail || '保存模型配置失败。')
    setModelConfig({ ...defaultModelConfig, ...data, api_key: '' })
    setModelMessage('配置已保存到本机 JSON 文件。')
    if (modelConfig.provider === 'ollama') setModelListRefreshKey((key) => key + 1)
    const healthResponse = await fetch(`${API}/api/health`)
    if (healthResponse.ok) setHealth(await healthResponse.json() as Health)
  }

  async function saveModelConfig() {
    setModelBusy(true)
    setModelError('')
    setModelMessage('')
    try {
      await persistModelConfig()
    } catch (cause) {
      setModelError(cause instanceof Error ? cause.message : '保存模型配置失败。')
    } finally {
      setModelBusy(false)
    }
  }

  async function startModel() {
    setModelBusy(true)
    setModelError('')
    setModelMessage('正在保存配置并启动模型…')
    try {
      await persistModelConfig()
      const response = await fetch(`${API}/api/model/start`, { method: 'POST' })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || '模型启动失败。')
      setModelMessage(data.message)
      const healthResponse = await fetch(`${API}/api/health`)
      if (healthResponse.ok) setHealth(await healthResponse.json() as Health)
    } catch (cause) {
      setModelError(cause instanceof Error ? cause.message : '模型启动失败。')
      const healthResponse = await fetch(`${API}/api/health`).catch(() => undefined)
      if (healthResponse?.ok) setHealth(await healthResponse.json() as Health)
    } finally {
      setModelBusy(false)
    }
  }

  async function stopModel() {
    setModelBusy(true)
    setModelError('')
    try {
      const response = await fetch(`${API}/api/model/stop`, { method: 'POST' })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || '停止模型失败。')
      setModelMessage(data.message)
      const healthResponse = await fetch(`${API}/api/health`)
      if (healthResponse.ok) setHealth(await healthResponse.json() as Health)
    } catch (cause) {
      setModelError(cause instanceof Error ? cause.message : '停止模型失败。')
    } finally {
      setModelBusy(false)
    }
  }

  async function generate() {
    if (!health?.model_active) {
      setError('请先打开模型设置并启动模型。')
      return
    }
    if (!photo) {
      setError('先选择一张照片，再生成调色建议。')
      return
    }
    if (inputMode === 'reference' && !referencePhoto) {
      setError('参考图模式需要先上传一张参考图，或切回文字方向模式。')
      return
    }
    const allowed = Object.entries(allowedKeys)
      .filter(([, value]) => value)
      .map(([key]) => key)
    if (registry && allowed.length === 0) {
      setError('请先在「全部 LrC 调色参数」面板里允许至少一个参数参与 AI 调整。')
      return
    }
    setBusy(true)
    setError('')
    const body = new FormData()
    body.append('photo', photo)
    body.append('prompt', prompt)
    if (inputMode === 'reference' && referencePhoto) body.append('reference', referencePhoto)
    if (registry) body.append('allowed_keys', JSON.stringify(allowed))
    try {
      const response = await fetch(`${API}/api/suggestions`, { method: 'POST', body })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || '请求失败，请检查模型服务。')
      const parsed = data as Suggestion
      const accepted = registry
        ? Object.fromEntries(
            Object.entries(parsed.settings).filter(([key]) => allowedKeys[key] !== false),
          )
        : parsed.settings
      setSuggestion({ ...parsed, settings: accepted })
      setSettings({ ...accepted })
      setCurveDrafts({})
      setLightroomWarning('')
      setLightroomPreviewUrl(undefined)
      setPreviewMode('original')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '无法连接调色服务。')
    } finally {
      setBusy(false)
    }
  }

  function resetLightroomPreview() {
    setLightroomPreviewUrl(undefined)
    setPreviewMode('original')
  }

  function setSetting(key: string, value: SettingValue) {
    setSettings((current) => ({ ...current, [key]: value }))
    setLightroomWarning('')
    resetLightroomPreview()
  }

  function removeSetting(key: string) {
    setSettings((current) => {
      const next = { ...current }
      delete next[key]
      return next
    })
    resetLightroomPreview()
  }

  function addSetting(control: DevelopControl) {
    setSetting(control.key, control.default)
    // 手动把蒙版参数加入本次渲染，等同于打开蒙版开关，避免开关状态与实际写入不一致。
    if (maskKeys.includes(control.key)) setMaskAllowed(true)
  }

  // 一次设置蒙版五个参数的「允许大模型调整」状态。
  function setMaskAllowed(enabled: boolean) {
    setAllowedKeys((current) => {
      const next = { ...current }
      maskKeys.forEach((key) => { next[key] = enabled })
      return next
    })
  }

  function toggleAllowed(control: DevelopControl) {
    const allowed = allowedKeys[control.key] ?? control.model_default
    setAllowedKeys((current) => ({ ...current, [control.key]: !allowed }))
    // 不再允许模型调整的参数也退出本次渲染，避免开关与写入值不一致。
    if (allowed && control.key in settings) removeSetting(control.key)
  }

  // 蒙版开关：一次改变效果面板里全部暗角参数的允许状态；关闭时同时把本次渲染里的
  // 暗角值清掉，让「开关状态」与「实际写入 LrPhoto 的参数」始终一致。
  function setMaskEnabled(enabled: boolean) {
    if (maskKeys.length === 0) return
    setMaskAllowed(enabled)
    if (!enabled) {
      setSettings((current) => Object.fromEntries(
        Object.entries(current).filter(([key]) => !maskKeys.includes(key)),
      ))
    }
    setLightroomWarning('')
    resetLightroomPreview()
  }

  // 强度快捷键：打开蒙版并把安全窗口内的暗角参数写进本次渲染。
  function applyMaskStrength(amount: number) {
    if (maskKeys.length === 0) return
    setMaskAllowed(true)
    setSettings((current) => ({ ...current, ...MASK_FRAME, PostCropVignetteAmount: amount }))
    setLightroomWarning('')
    resetLightroomPreview()
  }

  function controlFor(key: string) {
    return controlIndex[key] ?? genericControl(key)
  }

  function pickChoice(control: DevelopControl, raw: string): SettingValue {
    const choice = control.choices.find(([value]) => String(value) === raw)
    return choice ? choice[0] : raw
  }

  function updateCurve(key: string, text: string) {
    setCurveDrafts((current) => ({ ...current, [key]: text }))
    const points = parseCurveText(text)
    if (points) setSetting(key, points)
  }

  function renderValueEditor(control: DevelopControl, value: SettingValue) {
    if (control.kind === 'bool') {
      return (
        <label className="value-switch">
          <input type="checkbox" checked={value === true} onChange={(event) => setSetting(control.key, event.target.checked)} />
          <span>{value === true ? '开' : '关'}</span>
        </label>
      )
    }
    if (control.kind === 'enum') {
      return (
        <select className="value-select" value={String(value)} onChange={(event) => setSetting(control.key, pickChoice(control, event.target.value))}>
          {control.choices.map(([choice, label]) => <option value={String(choice)} key={String(choice)}>{label}</option>)}
        </select>
      )
    }
    if (control.kind === 'curve') {
      return (
        <input
          className="value-text"
          value={curveDrafts[control.key] ?? curveToText(value)}
          onChange={(event) => updateCurve(control.key, event.target.value)}
          placeholder="0,0;64,52;128,132;255,255"
          spellCheck={false}
        />
      )
    }
    return (
      <input
        type="range"
        min={control.minimum}
        max={control.maximum}
        step={control.step}
        value={typeof value === 'number' ? value : Number(control.default)}
        onChange={(event) => setSetting(control.key, Number(event.target.value))}
      />
    )
  }

  async function probeLightroom() {
    if (!lightroomStatus.connected) {
      setLightroomError('LrC 插件未连接；请在 Lightroom Classic 菜单中启动 aiLr Bridge。')
      return
    }
    setProbeBusy(true)
    setProbeResult(undefined)
    setLightroomError('')
    try {
      const createResponse = await fetch(`${API}/api/lightroom/jobs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'probe', settings: {} }),
      })
      const created = await createResponse.json()
      if (!createResponse.ok) throw new Error(created.detail || '无法创建参数自检任务。')

      let report = ''
      for (let attempt = 0; attempt < 30; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 1000))
        const statusResponse = await fetch(`${API}/api/lightroom/jobs/${created.job_id}`)
        const status = await statusResponse.json()
        if (!statusResponse.ok) throw new Error(status.detail || '无法读取参数自检状态。')
        if (status.status === 'failed') throw new Error(status.error || '参数自检失败。')
        if (status.report) {
          report = status.report as string
          break
        }
      }
      if (!report) throw new Error('参数自检超时；请确认 LrC 已选中照片且 aiLr Bridge 正在运行。')

      const supported = parseReportList(report, 'SUPPORTED=')
      const unsupported = parseReportList(report, 'UNSUPPORTED=')
      setProbeResult({ supported, unsupported, detail: report })
      setLightroomMessage(`参数自检完成：当前 LrC 接受 ${supported.length} 项，拒绝 ${unsupported.length} 项`)
    } catch (cause) {
      setLightroomError(cause instanceof Error ? cause.message : '参数自检失败。')
    } finally {
      setProbeBusy(false)
    }
  }

  async function renderWithLightroom(action: LightroomAction) {
    if (!photo) return
    setLightroomError('')
    setLightroomMessage('')
    setLightroomWarning('')
    if (Object.keys(settings).length === 0) {
      setLightroomError('请先生成调色建议，或在下方参数面板手动加入至少一个参数。')
      return
    }
    if (!lightroomStatus.connected) {
      setLightroomError('LrC 插件未连接；请在 Lightroom Classic 菜单中启动 aiLr Bridge。')
      return
    }
    if (lightroomStatus.selected_filename.toLowerCase() !== photo.name.toLowerCase()) {
      setLightroomError(`LrC 当前选中的是「${lightroomStatus.selected_filename || '无照片'}」，请选中与网页照片同名的文件「${photo.name}」。`)
      return
    }

    setLightroomBusy(true)
    setLightroomMessage(action === 'preview' ? '正在让 Lightroom 应用参数并渲染预览…' : '正在通过 Lightroom 渲染导出文件…')
    try {
      const createResponse = await fetch(`${API}/api/lightroom/jobs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action,
          settings,
          format: action === 'preview' ? 'JPEG' : exportFormat,
          quality: exportQuality,
          max_dimension: action === 'preview' ? 2560 : exportMaxDimension,
        }),
      })
      const created = await createResponse.json()
      if (!createResponse.ok) throw new Error(created.detail || '无法创建 Lightroom 渲染任务。')

      let completed: { status: string; error?: string; filename?: string; report?: string } | undefined
      for (let attempt = 0; attempt < 240; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 1000))
        const statusResponse = await fetch(`${API}/api/lightroom/jobs/${created.job_id}`)
        const status = await statusResponse.json()
        if (!statusResponse.ok) throw new Error(status.detail || '无法读取 Lightroom 渲染状态。')
        if (status.status === 'failed') throw new Error(status.error || 'Lightroom 渲染失败。')
        if (status.status === 'completed') {
          completed = status
          break
        }
      }
      if (!completed) throw new Error('Lightroom 渲染超时；请确认 LrC 仍打开且 aiLr Bridge 正在运行。')

      // The plug-in reports the parameters this Lightroom build refused; the
      // render still succeeds, so this stays a warning instead of an error.
      const rejected = parseReportList(completed.report ?? '', 'REJECTED=')
      if (rejected.length > 0) {
        setLightroomWarning(`当前 LrC 拒绝了 ${rejected.length} 项参数并已跳过：${rejected.join('、')}`)
      }

      const imageResponse = await fetch(`${API}/api/lightroom/jobs/${created.job_id}/image${action === 'export' ? '?download=true' : ''}`)
      if (!imageResponse.ok) throw new Error('Lightroom 已完成渲染，但网页无法读取输出文件。')
      const image = await imageResponse.blob()
      if (action === 'preview') {
        setLightroomPreviewUrl(URL.createObjectURL(image))
        setPreviewMode('lightroom')
        setLightroomMessage(`Lightroom 预览完成：${completed.filename || photo.name}`)
      } else {
        const url = URL.createObjectURL(image)
        const link = document.createElement('a')
        link.href = url
        link.download = completed.filename || `${photo.name.replace(/\.[^.]+$/, '')}-LrC.${exportFormat.toLowerCase()}`
        link.click()
        window.setTimeout(() => URL.revokeObjectURL(url), 1000)
        setLightroomMessage(`已从 Lightroom 导出：${link.download}`)
      }
    } catch (cause) {
      setLightroomError(cause instanceof Error ? cause.message : 'Lightroom 渲染失败。')
      setLightroomMessage('')
    } finally {
      setLightroomBusy(false)
    }
  }

  function exportSettings() {
    if (Object.keys(settings).length === 0) return
    const payload = { format: 'ailr-develop-settings-v1', summary: suggestion?.summary ?? '', settings }
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${photo?.name.replace(/\.[^.]+$/, '') || 'ailr'}-develop.json`
    link.click()
    URL.revokeObjectURL(url)
  }

  const connected = Boolean(health)
  const hasSettings = Object.keys(settings).length > 0
  const allowedTotal = Object.values(allowedKeys).filter(Boolean).length
  // 蒙版开关状态跟随这组参数的「允许大模型调整」开关；强度由当前裁剪后暗角数量决定。
  const maskOn = maskKeys.some((key) => allowedKeys[key] === true)
  const maskAmount = typeof settings.PostCropVignetteAmount === 'number' ? settings.PostCropVignetteAmount : undefined
  const maskStrength = MASK_STRENGTHS.find((preset) => preset.amount === maskAmount)?.id
  const maskHint = !maskOn
    ? '默认关闭：关闭时 AI 不会使用蒙版，本次渲染也不会带蒙版参数'
    : typeof maskAmount === 'number'
      ? `已开启 · 当前裁剪后暗角数量 ${maskAmount}，可在下方参数里继续微调`
      : '已开启，但本次渲染还没有蒙版参数；点「轻 / 中 / 强」快速加入'
  const activePreset = presets.find((preset) => preset.instruction === prompt)
  const displayedPreview = previewMode === 'lightroom' && lightroomPreviewUrl ? lightroomPreviewUrl : previewUrl

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="#workspace" aria-label="aiLr 工作台">
          <span className="brand-mark"><Aperture size={19} strokeWidth={2.2} /></span>
          <span>ai<span className="brand-accent">Lr</span></span>
        </a>
        <div className="topbar-center"><span className="crumb-muted">工作区</span><span className="crumb-divider">/</span><span>智能调色</span></div>
        <div className="topbar-right">
          <span className={`connection ${health?.model_active ? 'is-online' : ''}`}><i />{health?.model_active ? '模型已启动' : connected ? '模型未启动' : '等待连接'}</span>
          <a className="guide-trigger" href={GUIDE_URL} target="_blank" rel="noreferrer" title="安装、联动与排查说明"><BookOpen size={15} />使用指南</a>
          <button className="settings-trigger" onClick={() => setShowModelSettings(true)}><Settings2 size={15} />模型设置</button>
        </div>
      </header>

      <section className="workspace" id="workspace">
        <div className="section-heading">
          <div>
            <div className="eyebrow"><span className="eyebrow-line" />AI COLOR STUDIO <span className="version">BETA 01</span></div>
            <h1>让灵感先行，<em>色彩随后。</em></h1>
          </div>
          <div className="model-pill"><span className="model-dot" /><span>{health ? `${health.provider === 'ollama' ? 'Ollama 本地' : '云端兼容'} · ${health.model}` : '模型服务未连接'}</span><small>{health?.model_active ? '运行中' : '已停止'}</small></div>
        </div>

        <div className="editor-grid">
          <section className="photo-panel" aria-label="照片预览">
            <div className="panel-toolbar">
              <div className="panel-title"><span className="step-number">01</span><span>照片画布</span></div>
              {lightroomPreviewUrl ? <div className="preview-mode-switch" role="group" aria-label="预览模式">
                <button className={previewMode === 'original' ? 'selected' : ''} onClick={() => setPreviewMode('original')}>原图</button>
                <button className={previewMode === 'lightroom' ? 'selected' : ''} onClick={() => setPreviewMode('lightroom')}>LrC 渲染</button>
              </div> : <span className="toolbar-note">原图预览</span>}
            </div>
            <div className={`photo-stage ${displayedPreview ? 'has-photo' : ''}`}>
              {previewLoading ? (
                <div className="raw-preview-loading"><span className="spinner" /><span>正在解码 RAW 预览…</span></div>
              ) : displayedPreview ? (
                <>
                  <img className="photo-preview" src={displayedPreview} alt={previewMode === 'lightroom' ? 'Lightroom 实际渲染预览' : '待调色原图预览'} />
                  <div className="image-badge"><Activity size={13} />{previewMode === 'lightroom' ? 'Lightroom 渲染 · ' : ''}{photo?.name}</div>
                  <label className="replace-photo" title="替换照片"><Upload size={15} /><input type="file" accept={PHOTO_ACCEPT} onChange={(event) => selectPhoto(event.target.files?.[0])} /></label>
                </>
              ) : (
                <label className="upload-prompt">
                  <input type="file" accept={PHOTO_ACCEPT} onChange={(event) => selectPhoto(event.target.files?.[0])} />
                  <span className="upload-icon"><ImagePlus size={22} /></span>
                  <strong>把照片放进画布</strong>
                  <span>选择照片或相机 RAW 开始调色</span>
                  <small>JPG / PNG ≤12 MB · RAW ≤100 MB</small>
                </label>
              )}
              <div className="stage-corner corner-tl" /><div className="stage-corner corner-br" />
            </div>
            <div className="photo-footer"><span>{photo ? `${isRawPhoto(photo.name) ? 'RAW · ' : ''}${(photo.size / 1024 / 1024).toFixed(1)} MB` : '尚未载入照片'}</span><span className="footer-separator" /><span>{photo && isRawPhoto(photo.name) ? '原片不改写 · 转换预览用于 AI 分析' : 'Lightroom 参数格式'}</span></div>
          </section>

          <section className="controls-panel" aria-label="AI 调色控制台">
            <div className="panel-toolbar">
              <div className="panel-title"><span className="step-number">02</span><span>调色方向</span></div>
              <div className="toolbar-actions">
                <Sparkles size={16} className="sparkle-icon" />
                <button
                  className={`preset-trigger ${activePreset ? 'is-active' : ''}`}
                  onClick={() => setShowPresets(true)}
                  aria-haspopup="dialog"
                  aria-expanded={showPresets}
                  title="打开调色预设抽屉，点击即可把提示词填入创作说明"
                >
                  <Palette size={13} />
                  <span className="preset-trigger-label">{activePreset ? activePreset.name : '调色预设'}</span>
                </button>
              </div>
            </div>
            <div className="prompt-block">
              <div className="input-mode-switch" role="group" aria-label="调色输入方式">
                <button
                  className={inputMode === 'prompt' ? 'selected' : ''}
                  onClick={() => switchInputMode('prompt')}
                  title="只用文字描述想要的调色方向"
                ><Sparkles size={12} />文字方向</button>
                <button
                  className={inputMode === 'reference' ? 'selected' : ''}
                  onClick={() => switchInputMode('reference')}
                  title="再上传一张参考图，让模型借用它的调色风格"
                ><ImagePlus size={12} />参考图</button>
              </div>
              <label htmlFor="creative-brief">告诉 AI 你想要的感觉</label>
              <textarea id="creative-brief" value={prompt} onChange={(event) => setPrompt(event.target.value)} maxLength={PROMPT_MAX_LENGTH} placeholder={inputMode === 'reference' ? '例如：照着参考图的色调，让目标图也有那种暖调胶片感……' : '例如：胶片感的暖调人像，肤色自然……'} />
              <div className="prompt-meta">
                <span className={presetNotice ? 'is-notice' : ''}>{presetNotice || '建议具体描述氛围、主体和需要保留的细节'}</span>
                <span>{prompt.length}/{PROMPT_MAX_LENGTH}</span>
              </div>
            </div>
            {inputMode === 'reference' && (
              <div className="reference-block">
                {referencePreviewUrl || referenceLoading ? (
                  <div className="reference-preview">
                    {referenceLoading
                      ? <div className="reference-loading"><span className="spinner" /><span>正在解码参考图…</span></div>
                      : <img src={referencePreviewUrl} alt="参考图预览" />}
                    <label className="reference-replace" title="替换参考图"><Upload size={13} /><input type="file" accept={PHOTO_ACCEPT} onChange={(event) => selectReference(event.target.files?.[0])} /></label>
                    <button className="reference-remove" onClick={() => selectReference(undefined)} title="移除参考图" aria-label="移除参考图"><X size={13} /></button>
                  </div>
                ) : (
                  <label className="reference-prompt">
                    <input type="file" accept={PHOTO_ACCEPT} onChange={(event) => selectReference(event.target.files?.[0])} />
                    <span className="reference-icon"><ImagePlus size={16} /></span>
                    <span className="reference-copy"><strong>上传参考图</strong><small>AI 分析它的调色风格，应用到左边的目标图</small></span>
                  </label>
                )}
                <p className="reference-hint">参考图只提供色调与氛围，不会照搬内容；生成建议时会连同目标图一起发给模型。</p>
              </div>
            )}
            <button className="generate-button" onClick={generate} disabled={busy || previewLoading || referenceLoading || !health?.model_active}>
              {busy || previewLoading ? <span className="spinner" /> : <Sparkles size={17} />}
              {previewLoading ? '正在准备 RAW…' : busy ? '正在分析照片…' : health?.model_active ? '生成调色方案' : '先启动模型'}
              {!busy && !previewLoading && <span className="button-shortcut">↵</span>}
            </button>
            {error && <div className="error-message" role="alert">{error}</div>}
            <div className="controls-divider"><span>参数微调</span><span>{hasSettings ? `${Object.keys(settings).length} 项待应用` : '等待 AI 建议或手动添加'}</span></div>
            {maskKeys.length > 0 && (
              <section className={`mask-strip ${maskOn ? 'is-on' : ''}`} aria-label="蒙版">
                <div className="mask-strip-head">
                  <label className="mask-toggle" title="蒙版 = 效果面板的「裁剪后暗角」五项。默认关闭：关闭时 AI 不会给出这组参数，本次渲染也不会带蒙版。">
                    <input type="checkbox" checked={maskOn} onChange={(event) => setMaskEnabled(event.target.checked)} />
                    <span className="mask-title">蒙版</span>
                    <em className={`mask-state ${maskOn ? 'is-on' : ''}`}>{maskOn ? '已开启' : '默认关闭'}</em>
                  </label>
                  <div className="mask-strength" role="group" aria-label="蒙版强度">
                    <button className={maskOn ? '' : 'selected'} onClick={() => setMaskEnabled(false)} title="关闭蒙版，并从本次渲染里移除暗角参数">关闭</button>
                    {MASK_STRENGTHS.map((preset) => (
                      <button
                        key={preset.id}
                        className={maskOn && maskStrength === preset.id ? 'selected' : ''}
                        onClick={() => applyMaskStrength(preset.amount)}
                        title={`打开蒙版并把「裁剪后暗角 数量」设为 ${preset.amount}（羽化 60、圆度 0、中点 50，不会出现圆形硬边）`}
                      >{preset.label}</button>
                    ))}
                  </div>
                </div>
                <p className="mask-hint">{maskHint}</p>
              </section>
            )}
            {suggestion && <p className="suggestion-summary">{suggestion.summary}</p>}
            {suggestion?.overflow && (
              <div className={`overflow-report ${suggestion.overflow.unresolved.length > 0 ? 'is-warning' : ''}`} role="status">
                <p className="overflow-notice"><RefreshCw size={12} />{suggestion.overflow.notice}</p>
                <details className="overflow-detail">
                  <summary>查看参数修正明细（模型调用 {suggestion.overflow.attempts} 次）</summary>
                  {suggestion.overflow.unresolved.length > 0 && (
                    <div className="overflow-block">
                      <span>重试后仍不合规、已从本次方案丢弃</span>
                      <ul>{suggestion.overflow.unresolved.map((item) => <li key={item}>{item}</li>)}</ul>
                    </div>
                  )}
                  {suggestion.overflow.resolved.length > 0 && (
                    <div className="overflow-block">
                      <span>已按范围重新生成</span>
                      <ul>{suggestion.overflow.resolved.map((item) => <li key={item}>{item}</li>)}</ul>
                    </div>
                  )}
                  {suggestion.overflow.adjusted.length > 0 && (
                    <div className="overflow-block">
                      <span>已按防伪蒙版规则收敛，避免画面上出现圆形硬边</span>
                      <ul>{suggestion.overflow.adjusted.map((item) => <li key={item}>{item}</li>)}</ul>
                    </div>
                  )}
                </details>
              </div>
            )}
            {hasSettings ? (
              <>
                <div className="slider-list">
                  {Object.entries(settings).map(([name, value]) => {
                    const control = controlFor(name)
                    return <div className={`slider-row kind-${control.kind}`} key={name}>
                      <span className="slider-label" title={`${control.key} → LrC ${control.controller}`}>{control.label}</span>
                      {renderValueEditor(control, value)}
                      <span className="slider-value">{formatSettingValue(value)}</span>
                      <button className="slider-remove" onClick={() => removeSetting(name)} title="从本次渲染中移除该参数" aria-label={`移除 ${control.label}`}><X size={11} /></button>
                    </div>
                  })}
                </div>
                <section className="lightroom-output" aria-label="Lightroom 渲染与导出">
                  <div className="lightroom-output-heading">
                    <span>Lightroom Classic</span>
                    <span className={`bridge-status ${lightroomStatus.connected ? 'is-connected' : ''}`}><i />{lightroomStatus.connected ? '插件已连接' : '插件未连接'}</span>
                  </div>
                  <p className="lightroom-selection">当前选中：{lightroomStatus.selected_filename || '无'} · 网页与 LrC 文件名需一致</p>
                  <button className="lrc-preview-button" onClick={() => renderWithLightroom('preview')} disabled={lightroomBusy || !lightroomStatus.connected}>
                    {lightroomBusy ? <span className="spinner" /> : <Aperture size={15} />}
                    {lightroomBusy ? '等待 Lightroom 渲染…' : '应用到 LrC 并预览'}
                  </button>
                  <div className="export-options">
                    <label><span>格式</span><select value={exportFormat} onChange={(event) => setExportFormat(event.target.value as 'JPEG' | 'PNG' | 'TIFF')}><option value="JPEG">JPEG</option><option value="PNG">PNG</option><option value="TIFF">TIFF</option></select></label>
                    {exportFormat === 'JPEG' && <label><span>质量</span><input type="range" min="50" max="100" value={exportQuality} onChange={(event) => setExportQuality(Number(event.target.value))} /><b>{exportQuality}</b></label>}
                    <label><span>最长边</span><input className="dimension-input" type="number" min="256" max="12000" step="256" value={exportMaxDimension} onChange={(event) => setExportMaxDimension(Math.max(256, Math.min(12000, Number(event.target.value) || 256)))} /><b>px</b></label>
                    <button className="lrc-export-button" onClick={() => renderWithLightroom('export')} disabled={lightroomBusy || !lightroomStatus.connected}><Download size={14} />LrC 渲染并下载</button>
                  </div>
                  {lightroomError && <div className="lightroom-feedback feedback-error" role="alert">{lightroomError}</div>}
                  {lightroomWarning && <div className="lightroom-feedback feedback-warning" role="status">{lightroomWarning}</div>}
                  {lightroomMessage && <div className="lightroom-feedback" role="status">{lightroomMessage}</div>}
                  <p className="lightroom-disclaimer">预览/导出会把参数写入 LrC 当前照片的 Develop 历史，可在 Lightroom 中撤销。<a className="guide-inline-link" href={GUIDE_URL} target="_blank" rel="noreferrer">首次接入 / 报错排查 →</a></p>
                </section>
                <button className="export-button" onClick={exportSettings}><ArrowDownToLine size={16} />导出调色参数<span>.JSON</span></button>
              </>
            ) : (
              <div className="empty-controls"><span className="empty-mark"><Sparkles size={17} /></span><span>生成后，参数会在这里逐项呈现<br />也可以直接在下方参数面板手动添加</span></div>
            )}

            <section className="develop-browser" aria-label="全部 Lightroom 调色参数">
              <div className="develop-browser-head">
                <div>
                  <strong>全部 LrC 调色参数</strong>
                  <span>{registry ? `共 ${registry.total} 项 · 已允许 ${allowedTotal} 项参与 AI，按 ＋ 加入本次渲染` : '参数表未加载（请确认后端已启动）'}</span>
                </div>
                <div className="develop-browser-actions">
                  <button className="probe-button" onClick={probeLightroom} disabled={probeBusy || !lightroomStatus.connected}>
                    {probeBusy ? <span className="spinner" /> : <ShieldCheck size={13} />}
                    {probeBusy ? '检测中…' : '检测 LrC 支持'}
                  </button>
                  {hasSettings && <button className="clear-button" onClick={() => { setSettings({}); setCurveDrafts({}); resetLightroomPreview() }}>清空参数</button>}
                </div>
              </div>
              {probeResult && (
                <p className="probe-summary">
                  当前 LrC 接受 {probeResult.supported.length} 项、拒绝 {probeResult.unsupported.length} 项
                  {probeResult.unsupported.length > 0 && `：${probeResult.unsupported.slice(0, 6).join('、')}${probeResult.unsupported.length > 6 ? ' 等' : ''}`}
                </p>
              )}
              {registry?.groups.map((group) => {
                const open = openGroups[group.id] ?? false
                const activeCount = group.controls.filter((control) => control.key in settings).length
                const allowedCount = group.controls.filter((control) => allowedKeys[control.key] ?? control.model_default).length
                return (
                  <div className={`develop-group ${open ? 'is-open' : ''}`} key={group.id}>
                    <button className="develop-group-head" onClick={() => setOpenGroups((current) => ({ ...current, [group.id]: !open }))} aria-expanded={open}>
                      <ChevronRight size={13} className="group-chevron" />
                      <span>{group.label}</span>
                      <small>{activeCount > 0 ? `允许 ${allowedCount} · 已加入 ${activeCount}` : `${allowedCount}/${group.controls.length} 项允许 AI`}</small>
                    </button>
                    {open && <div className="develop-group-body">
                      {group.controls.map((control) => {
                        const active = control.key in settings
                        const allowed = allowedKeys[control.key] ?? control.model_default
                        const rejected = probeResult?.unsupported.includes(control.key) ?? false
                        return (
                          <div className={`develop-row ${active ? 'is-active' : ''} ${rejected ? 'is-rejected' : ''} ${allowed ? '' : 'is-disallowed'}`} key={control.key}>
                            <label className="develop-row-toggle" title={`${allowed ? '已允许' : '未允许'}大模型调整 · ${control.key} → LrC ${control.controller}${control.description ? ` · ${control.description}` : ''}`}>
                              <input type="checkbox" checked={allowed} disabled={rejected} onChange={() => toggleAllowed(control)} />
                              <span className="develop-row-label">
                                {control.label}
                                {control.experimental && <em className="develop-tag">实验</em>}
                                {maskKeys.includes(control.key) && <em className="develop-tag">蒙版</em>}
                                {rejected && <em className="develop-tag is-warning">宿主不支持</em>}
                              </span>
                            </label>
                            {active
                              ? renderValueEditor(control, settings[control.key])
                              : <span className="develop-row-idle">{control.kind === 'number' ? `${control.default}` : control.kind === 'bool' ? (control.default ? '开' : '关') : ''}</span>}
                            <span className="develop-row-key">{control.key}</span>
                            <button
                              className={`develop-row-action ${active ? 'is-remove' : ''}`}
                              onClick={() => { if (active) removeSetting(control.key); else addSetting(control) }}
                              title={active ? '从本次渲染中移除该参数' : '以当前值加入本次渲染'}
                              aria-label={active ? `移除 ${control.label}` : `加入 ${control.label}`}
                            >
                              {active ? <X size={11} /> : <Plus size={11} />}
                            </button>
                          </div>
                        )
                      })}
                    </div>}
                  </div>
                )
              })}
            </section>
          </section>
        </div>

        <footer className="workspace-footer">
          <span><span className="footer-live" />{health?.provider === 'openai' ? '照片将发送至所选云端模型' : '本地模型仅在点击启动后加载'}</span>
          <span className="lrc-state"><Check size={13} />{lightroomStatus.connected ? `LrC 已连接 · ${lightroomStatus.selected_filename || '未选中照片'}` : '等待 Lightroom Classic 插件连接'}</span>
        </footer>
      </section>

      <footer className="app-footer">
        <span>Copyright 2026 <a href="">qincnd</a> All Rights Reserved</span>
      </footer>

      {showPresets && <div className="drawer-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setShowPresets(false) }}>
        <aside className="preset-drawer" role="dialog" aria-modal="true" aria-labelledby="preset-drawer-title">
          <header className="drawer-heading">
            <div>
              <span className="dialog-kicker">COLOUR DIRECTIONS</span>
              <h2 id="preset-drawer-title">调色预设</h2>
            </div>
            <div className="drawer-heading-actions">
              <button
                className={`preset-new ${presetFormOpen ? 'is-open' : ''}`}
                onClick={() => (presetFormOpen ? setPresetFormOpen(false) : openPresetForm())}
                aria-expanded={presetFormOpen}
                title={`新建一个调色方向，保存到项目根目录的 ${presetsFile}`}
              ><Plus size={13} />新建预设</button>
              <button className="drawer-close" onClick={() => setShowPresets(false)} aria-label="关闭调色预设抽屉"><X size={18} /></button>
            </div>
          </header>
          <p className="drawer-intro">选一个方向，它的调色步骤、约束与参考参数会合成一段创作说明填入「告诉 AI 你想要的感觉」，生成前仍可自由改写；不选预设时也可以直接写一段纯文本。内置与自建方向都保存在项目根目录的 <code>{presetsFile}</code>，也可以直接编辑这个文件。</p>
          {presetFormOpen && <form className="preset-form" onSubmit={(event) => { event.preventDefault(); void createPresetFromDraft() }}>
            <div className="preset-form-row">
              <label className="preset-field"><span>名称</span><input autoFocus value={presetDraft.name} maxLength={PRESET_NAME_MAX_LENGTH} onChange={(event) => setPresetDraft({ ...presetDraft, name: event.target.value })} placeholder="例如：暮色江面" /></label>
              <label className="preset-field"><span>分类</span><input value={presetDraft.tag} maxLength={PRESET_TAG_MAX_LENGTH} onChange={(event) => setPresetDraft({ ...presetDraft, tag: event.target.value })} placeholder="留空记作「自建」" /></label>
            </div>
            <label className="preset-field"><span>一句话描述 <small>{presetDraft.summary.length}/{PRESET_SUMMARY_MAX_LENGTH}</small></span><input value={presetDraft.summary} maxLength={PRESET_SUMMARY_MAX_LENGTH} onChange={(event) => setPresetDraft({ ...presetDraft, summary: event.target.value })} placeholder="留空则取提示词前 48 字" /></label>
            <label className="preset-field"><span>提示词 <small>{presetDraft.prompt.length}/{PROMPT_MAX_LENGTH}</small></span><textarea value={presetDraft.prompt} maxLength={PROMPT_MAX_LENGTH} onChange={(event) => setPresetDraft({ ...presetDraft, prompt: event.target.value })} placeholder="氛围 + 光影 + 颜色 + 要保留的细节 + 要避免的问题" /></label>
            <div className="preset-field">
              <span>参数参考（可选）<small>{presetHintItems.length}/{PRESET_HINT_MAX_COUNT} 项</small></span>
              {presetDraft.hints.map((row, index) => (
                <div className="preset-hint-row" key={index}>
                  <input list="preset-hint-names" value={row.name} maxLength={PRESET_HINT_KEY_MAX_LENGTH} onChange={(event) => updateHintRow(index, { name: event.target.value })} placeholder="参数名，如 highlights" aria-label={`第 ${index + 1} 行参数名`} />
                  <input value={row.value} maxLength={PRESET_HINT_VALUE_MAX_LENGTH} onChange={(event) => updateHintRow(index, { value: event.target.value })} placeholder="建议，如 +5~+10" aria-label={`第 ${index + 1} 行参数建议`} />
                  <button type="button" className="preset-hint-remove" onClick={() => removeHintRow(index)} aria-label={`删掉第 ${index + 1} 行参数参考`} title="删掉这一行"><X size={11} /></button>
                </div>
              ))}
              <button type="button" className="preset-hint-add" onClick={addHintRow} disabled={presetDraft.hints.length >= PRESET_HINT_MAX_COUNT}><Plus size={11} />加一行参数</button>
              <datalist id="preset-hint-names">
                {presetHintOptions.map((option) => <option value={option.name} label={option.label} key={option.name} />)}
              </datalist>
              <p className="preset-hint">整块留空就不写参考参数（预设按纯文本保存）。参数名可写短名 <code>highlights</code>、键名 <code>Highlights2012</code> 或中文标签，短名与键名会补成 <code>高光(Highlights2012)</code>；建议写成区间 <code>+5~+10</code> 或单值 <code>10</code>。参数名对照表与更多写法见 <a className="guide-inline-link" href={`${GUIDE_URL}#preset-params-hint`} target="_blank" rel="noreferrer">使用指南 · 参数参考</a>。</p>
              {presetHintLine && <p className="preset-hint-preview">{presetHintLine}<small>合成后 {presetDraftLength}/{PROMPT_MAX_LENGTH} 字</small></p>}
            </div>
            <p className="preset-hint">要分步骤（<code>steps</code>）或写约束（<code>constraints</code>），直接编辑项目根目录的 {presetsFile}；表单填了参数参考就写成 <code>text</code> + <code>params_hint</code> 对象，没填则仍是一段字符串。</p>
            <div className="preset-form-actions">
              <button type="submit" className="preset-save" disabled={presetBusy || !presetDraft.name.trim() || !presetDraft.prompt.trim() || presetDraftLength > PROMPT_MAX_LENGTH}>{presetBusy ? '保存中…' : `保存到 ${presetsFile}`}</button>
              <button type="button" className="preset-cancel" onClick={() => setPresetFormOpen(false)}>取消</button>
            </div>
          </form>}
          {presetsError && <p className="preset-error" role="alert">{presetsError}</p>}
          <div className="preset-list">
            {presets.length === 0 && !presetsError && <p className="preset-empty">还没有调色方向：点右上角「新建预设」写一个，或直接编辑 {presetsFile}。</p>}
            {presets.map((preset) => {
              const applied = activePreset?.id === preset.id
              return (
                <div className={`preset-card ${applied ? 'is-active' : ''}`} key={preset.id}>
                  <button
                    className="preset-card-pick"
                    onClick={() => applyPreset(preset)}
                    aria-pressed={applied}
                    title={`把「${preset.name}」的创作说明填入输入框`}
                  >
                    <span className="preset-card-head">
                      <strong>{preset.name}</strong>
                      <span className="preset-tags">
                        {preset.tags.map((tag) => <em className="preset-tag" key={tag}>{tag}</em>)}
                      </span>
                    </span>
                    <span className="preset-summary">{preset.summary}</span>
                    <span className="preset-prompt">{presetPreview(preset)}</span>
                    {presetMeta(preset) && <span className="preset-params">{presetMeta(preset)}</span>}
                  </button>
                  <div className="preset-card-foot">
                    {applied
                      ? <span className="preset-applied"><Check size={11} />已填入</span>
                      : <span className="preset-origin">{preset.created_at ? `自建 · ${preset.created_at}` : '内置方向'}</span>}
                    {pendingDelete === preset.id ? <>
                      <button className="preset-delete is-confirm" onClick={() => void removePreset(preset)} disabled={presetBusy}>确认删除</button>
                      <button className="preset-delete" onClick={() => setPendingDelete('')}>取消</button>
                    </> : (
                      <button className="preset-delete" onClick={() => setPendingDelete(preset.id)} title={`从 ${presetsFile} 中删除这个方向`}><Trash2 size={11} />删除</button>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
          <footer className="drawer-footer">
            <span>预设只改写创作说明：模型连接、参数授权与 LrC 渲染仍按右侧面板的当前设置执行。参考参数（强度与区间）只是建议，AI 会按画面实际情况判断。</span>
            <button className="preset-clear" onClick={() => { setPrompt(''); setPresetNotice(''); setShowPresets(false) }} disabled={!prompt}>清空创作说明</button>
          </footer>
        </aside>
      </div>}

      {showModelSettings && <div className="dialog-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setShowModelSettings(false) }}>
        <section className="model-dialog" role="dialog" aria-modal="true" aria-labelledby="model-dialog-title">
          <header className="dialog-heading">
            <div><span className="dialog-kicker">MODEL CONTROL</span><h2 id="model-dialog-title">模型连接</h2></div>
            <button className="dialog-close" onClick={() => setShowModelSettings(false)} aria-label="关闭模型设置"><X size={18} /></button>
          </header>
          <p className="dialog-intro">选择推理方式并填写连接参数。保存配置不会启动模型。</p>

          <div className="provider-switch" role="group" aria-label="模型部署方式">
            <button className={modelConfig.provider === 'ollama' ? 'selected' : ''} onClick={() => setModelConfig({ ...modelConfig, provider: 'ollama' })}><span className="provider-symbol">L</span><span><strong>本地模型</strong><small>Ollama · 数据留在本机</small></span></button>
            <button className={modelConfig.provider === 'openai' ? 'selected' : ''} onClick={() => setModelConfig({ ...modelConfig, provider: 'openai' })}><span className="provider-symbol cloud-symbol">↗</span><span><strong>云端模型</strong><small>OpenAI-compatible API</small></span></button>
          </div>

          <div className="model-fields">
            {modelConfig.provider === 'ollama' ? <>
              <div className="model-field"><span><label htmlFor="local-model-select">本地模型</label><button className="model-refresh" onClick={() => setModelListRefreshKey((key) => key + 1)} disabled={localModelsLoading} title="重新检测本地模型" aria-label="重新检测本地模型"><RefreshCw size={13} className={localModelsLoading ? 'refreshing' : ''} />{localModelsLoading ? '检测中' : '重新检测'}</button></span>
                <select id="local-model-select" value={modelConfig.model_name} onChange={(event) => setModelConfig({ ...modelConfig, model_name: event.target.value })} disabled={localModelsLoading || localModels.length === 0}>
                  {!localModels.some((model) => model.name === modelConfig.model_name) && <option value={modelConfig.model_name}>{modelConfig.model_name}（当前配置）</option>}
                  {localModels.map((model) => <option value={model.name} key={model.name}>{model.name} · {formatModelSize(model.size)}</option>)}
                </select>
                <small className={`model-list-status ${localModelsError ? 'has-error' : ''}`}>{localModelsError || (localModelsLoading ? '正在读取 Ollama 已下载模型，不会加载模型' : `检测到 ${localModels.length} 个本地模型 · 仅读取清单` )}</small>
              </div>
              <label className="model-field"><span>Ollama 地址</span><input value={modelConfig.ollama_base_url} onChange={(event) => setModelConfig({ ...modelConfig, ollama_base_url: event.target.value })} placeholder="http://127.0.0.1:11434" /></label>
            </> : <>
              <label className="model-field"><span>模型名称</span><input list="cloud-model-options" value={modelConfig.model_name} onChange={(event) => setModelConfig({ ...modelConfig, model_name: event.target.value })} placeholder="输入服务端模型 ID" /><datalist id="cloud-model-options">{['gpt-4o-mini', 'gpt-4o', 'qwen-vl-max'].map((model) => <option value={model} key={model} />)}</datalist></label>
              <label className="model-field"><span>API Base URL</span><input value={modelConfig.openai_base_url} onChange={(event) => setModelConfig({ ...modelConfig, openai_base_url: event.target.value })} placeholder="https://api.openai.com/v1" /></label>
              <label className="model-field"><span>API Key <small>{modelConfig.api_key_configured ? '已保存，留空则保留当前密钥' : '密钥仅保存到本机后端'}</small></span><input type="password" autoComplete="new-password" value={modelConfig.api_key} onChange={(event) => setModelConfig({ ...modelConfig, api_key: event.target.value, api_key_configured: Boolean(event.target.value) || modelConfig.api_key_configured })} placeholder={modelConfig.api_key_configured ? '已设置' : '粘贴 API Key'} /></label>
            </>}
          </div>

          <div className="config-note"><span className="note-rule" />配置写入 <code>backend/data/model_config.json</code>；API Key 保存在本机文件中且不会由读取接口返回。云端模式会将照片发送给所选服务。</div>
          {(modelError || modelMessage) && <div className={`model-feedback ${modelError ? 'feedback-error' : ''}`} role={modelError ? 'alert' : 'status'}>{modelError || modelMessage}</div>}
          <footer className="dialog-actions">
            {health?.model_active && <button className="stop-button" onClick={stopModel} disabled={modelBusy}><Power size={15} />停止</button>}
            <button className="save-button" onClick={saveModelConfig} disabled={modelBusy}><Save size={15} />保存配置</button>
            <button className="start-button" onClick={startModel} disabled={modelBusy}><Power size={15} />{modelBusy ? '处理中…' : '保存并启动'}</button>
          </footer>
        </section>
      </div>}
    </main>
  )
}
