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
  Plus,
  Power,
  RefreshCw,
  Save,
  Settings2,
  ShieldCheck,
  Sparkles,
  Upload,
  X,
} from 'lucide-react'

type Suggestion = { summary: string; settings: Record<string, SettingValue> }
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
  description: string
}
type DevelopGroup = { id: string; label: string; controls: DevelopControl[] }
type DevelopRegistry = { groups: DevelopGroup[]; model_keys: string[]; core_keys: string[]; total: number }

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

export default function App() {
  const [photo, setPhoto] = useState<File | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string>()
  const [previewLoading, setPreviewLoading] = useState(false)
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
  // 允许大模型调整的参数集合：默认除实验参数外全部允许，面板开关只控制这一范围。
  const [allowedKeys, setAllowedKeys] = useState<Record<string, boolean>>({})
  const [settings, setSettings] = useState<Record<string, SettingValue>>({})
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({ basic: true })
  const [curveDrafts, setCurveDrafts] = useState<Record<string, string>>({})
  const [probeBusy, setProbeBusy] = useState(false)
  const [probeResult, setProbeResult] = useState<{ supported: string[]; unsupported: string[]; detail: string }>()
  const [lightroomWarning, setLightroomWarning] = useState('')

  const controlIndex = useMemo(() => {
    const index: Record<string, DevelopControl> = {}
    registry?.groups.forEach((group) => group.controls.forEach((control) => { index[control.key] = control }))
    return index
  }, [registry])

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

  function selectPhoto(file: File | undefined) {
    setPhoto(file ?? null)
    setSuggestion(undefined)
    setLightroomPreviewUrl(undefined)
    setPreviewMode('original')
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
              .map((control) => [control.key, !control.experimental]),
          ),
        )
      })
      .catch(() => { if (!cancelled) setRegistry(undefined) })
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
  }

  function toggleAllowed(control: DevelopControl) {
    const allowed = allowedKeys[control.key] ?? !control.experimental
    setAllowedKeys((current) => ({ ...current, [control.key]: !allowed }))
    // 不再允许模型调整的参数也退出本次渲染，避免开关与写入值不一致。
    if (allowed && control.key in settings) removeSetting(control.key)
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
              <Sparkles size={16} className="sparkle-icon" />
            </div>
            <div className="prompt-block">
              <label htmlFor="creative-brief">告诉 AI 你想要的感觉</label>
              <textarea id="creative-brief" value={prompt} onChange={(event) => setPrompt(event.target.value)} maxLength={500} placeholder="例如：胶片感的暖调人像，肤色自然……" />
              <div className="prompt-meta"><span>建议具体描述氛围、主体和需要保留的细节</span><span>{prompt.length}/500</span></div>
            </div>
            <button className="generate-button" onClick={generate} disabled={busy || previewLoading || !health?.model_active}>
              {busy || previewLoading ? <span className="spinner" /> : <Sparkles size={17} />}
              {previewLoading ? '正在准备 RAW…' : busy ? '正在分析照片…' : health?.model_active ? '生成调色方案' : '先启动模型'}
              {!busy && !previewLoading && <span className="button-shortcut">↵</span>}
            </button>
            {error && <div className="error-message" role="alert">{error}</div>}
            <div className="controls-divider"><span>参数微调</span><span>{hasSettings ? `${Object.keys(settings).length} 项待应用` : '等待 AI 建议或手动添加'}</span></div>
            {suggestion && <p className="suggestion-summary">{suggestion.summary}</p>}
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
                const allowedCount = group.controls.filter((control) => allowedKeys[control.key] ?? !control.experimental).length
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
                        const allowed = allowedKeys[control.key] ?? !control.experimental
                        const rejected = probeResult?.unsupported.includes(control.key) ?? false
                        return (
                          <div className={`develop-row ${active ? 'is-active' : ''} ${rejected ? 'is-rejected' : ''} ${allowed ? '' : 'is-disallowed'}`} key={control.key}>
                            <label className="develop-row-toggle" title={`${allowed ? '已允许' : '未允许'}大模型调整 · ${control.key} → LrC ${control.controller}${control.description ? ` · ${control.description}` : ''}`}>
                              <input type="checkbox" checked={allowed} disabled={rejected} onChange={() => toggleAllowed(control)} />
                              <span className="develop-row-label">
                                {control.label}
                                {control.experimental && <em className="develop-tag">实验</em>}
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
