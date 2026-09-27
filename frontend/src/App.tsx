import { useEffect, useState } from 'react'
import {
  Activity,
  Aperture,
  ArrowDownToLine,
  Check,
  ImagePlus,
  Sparkles,
  Upload,
} from 'lucide-react'

type Suggestion = { summary: string; settings: Record<string, number> }
type Health = { status: string; provider: string; model: string; lightroom: string }

const API = 'http://127.0.0.1:8000'
const controlLabels: Record<string, string> = {
  Exposure2012: '曝光',
  Contrast2012: '对比度',
  Highlights2012: '高光',
  Shadows2012: '阴影',
  Whites2012: '白色色阶',
  Blacks2012: '黑色色阶',
  Temperature: '色温',
  Tint: '色调',
  Vibrance: '自然饱和度',
  Saturation: '饱和度',
  Texture: '纹理',
  Clarity2012: '清晰度',
  Dehaze: '去朦胧',
}
const limits: Record<string, [number, number]> = {
  Exposure2012: [-5, 5],
  Contrast2012: [-100, 100],
  Highlights2012: [-100, 100],
  Shadows2012: [-100, 100],
  Whites2012: [-100, 100],
  Blacks2012: [-100, 100],
  Temperature: [2000, 50000],
  Tint: [-150, 150],
  Vibrance: [-100, 100],
  Saturation: [-100, 100],
  Texture: [-100, 100],
  Clarity2012: [-100, 100],
  Dehaze: [-100, 100],
}

export default function App() {
  const [photo, setPhoto] = useState<File | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string>()
  const [prompt, setPrompt] = useState('自然通透，保留天空层次，绿色不要过饱和')
  const [suggestion, setSuggestion] = useState<Suggestion>()
  const [health, setHealth] = useState<Health>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!photo) {
      setPreviewUrl(undefined)
      return
    }
    const url = URL.createObjectURL(photo)
    setPreviewUrl(url)
    return () => URL.revokeObjectURL(url)
  }, [photo])

  useEffect(() => {
    fetch(`${API}/api/health`)
      .then((response) => response.json())
      .then((data: Health) => setHealth(data))
      .catch(() => setHealth(undefined))
  }, [])

  async function generate() {
    if (!photo) {
      setError('先选择一张照片，再生成调色建议。')
      return
    }
    setBusy(true)
    setError('')
    const body = new FormData()
    body.append('photo', photo)
    body.append('prompt', prompt)
    try {
      const response = await fetch(`${API}/api/suggestions`, { method: 'POST', body })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || '请求失败，请检查模型服务。')
      setSuggestion(data as Suggestion)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '无法连接调色服务。')
    } finally {
      setBusy(false)
    }
  }

  function updateControl(name: string, value: number) {
    setSuggestion((current) => current ? {
      ...current,
      settings: { ...current.settings, [name]: value },
    } : current)
  }

  function exportSettings() {
    if (!suggestion) return
    const blob = new Blob([JSON.stringify({ format: 'ailr-develop-settings-v1', ...suggestion }, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${photo?.name.replace(/\.[^.]+$/, '') || 'ailr'}-develop.json`
    link.click()
    URL.revokeObjectURL(url)
  }

  const connected = Boolean(health)

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="#workspace" aria-label="aiLr 工作台">
          <span className="brand-mark"><Aperture size={19} strokeWidth={2.2} /></span>
          <span>ai<span className="brand-accent">Lr</span></span>
        </a>
        <div className="topbar-center"><span className="crumb-muted">工作区</span><span className="crumb-divider">/</span><span>智能调色</span></div>
        <div className="topbar-right">
          <span className={`connection ${connected ? 'is-online' : ''}`}><i />{connected ? '本地服务在线' : '等待连接'}</span>
        </div>
      </header>

      <section className="workspace" id="workspace">
        <div className="section-heading">
          <div>
            <div className="eyebrow"><span className="eyebrow-line" />AI COLOR STUDIO <span className="version">BETA 01</span></div>
            <h1>让灵感先行，<em>色彩随后。</em></h1>
          </div>
          <div className="model-pill"><span className="model-dot" /><span>{health ? `${health.provider === 'ollama' ? 'Ollama 本地' : '云端兼容'} · ${health.model}` : '模型服务未连接'}</span></div>
        </div>

        <div className="editor-grid">
          <section className="photo-panel" aria-label="照片预览">
            <div className="panel-toolbar">
              <div className="panel-title"><span className="step-number">01</span><span>照片画布</span></div>
              <span className="toolbar-note">原图预览</span>
            </div>
            <div className={`photo-stage ${previewUrl ? 'has-photo' : ''}`}>
              {previewUrl ? (
                <>
                  <img className="photo-preview" src={previewUrl} alt="待调色照片预览" />
                  <div className="image-badge"><Activity size={13} />{photo?.name}</div>
                  <label className="replace-photo" title="替换照片"><Upload size={15} /><input type="file" accept="image/*" onChange={(event) => setPhoto(event.target.files?.[0] ?? null)} /></label>
                </>
              ) : (
                <label className="upload-prompt">
                  <input type="file" accept="image/*" onChange={(event) => setPhoto(event.target.files?.[0] ?? null)} />
                  <span className="upload-icon"><ImagePlus size={22} /></span>
                  <strong>把照片放进画布</strong>
                  <span>选择本地图片开始调色</span>
                  <small>JPG · PNG · WEBP，最大 12 MB</small>
                </label>
              )}
              <div className="stage-corner corner-tl" /><div className="stage-corner corner-br" />
            </div>
            <div className="photo-footer"><span>{photo ? `${(photo.size / 1024 / 1024).toFixed(1)} MB` : '尚未载入照片'}</span><span className="footer-separator" /><span>Lightroom 参数格式</span></div>
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
            <button className="generate-button" onClick={generate} disabled={busy}>
              {busy ? <span className="spinner" /> : <Sparkles size={17} />}
              {busy ? '正在分析照片…' : '生成调色方案'}
              {!busy && <span className="button-shortcut">↵</span>}
            </button>
            {error && <div className="error-message" role="alert">{error}</div>}
            <div className="controls-divider"><span>参数微调</span><span>{suggestion ? `${Object.keys(suggestion.settings).length} 项已生成` : '等待 AI 建议'}</span></div>
            {suggestion ? (
              <>
                <p className="suggestion-summary">{suggestion.summary}</p>
                <div className="slider-list">
                  {Object.entries(suggestion.settings).map(([name, value]) => {
                    const [min, max] = limits[name] ?? [-100, 100]
                    return <label className="slider-row" key={name}>
                      <span className="slider-label">{controlLabels[name] ?? name}</span>
                      <input type="range" min={min} max={max} step={name === 'Exposure2012' ? 0.1 : name === 'Temperature' ? 50 : 1} value={value} onChange={(event) => updateControl(name, Number(event.target.value))} />
                      <span className="slider-value">{Number.isInteger(value) ? value : value.toFixed(1)}</span>
                    </label>
                  })}
                </div>
                <button className="export-button" onClick={exportSettings}><ArrowDownToLine size={16} />导出调色参数<span>.JSON</span></button>
              </>
            ) : (
              <div className="empty-controls"><span className="empty-mark"><Sparkles size={17} /></span><span>生成后，参数会在这里逐项呈现<br />你可以检查并手动微调</span></div>
            )}
          </section>
        </div>

        <footer className="workspace-footer">
          <span><span className="footer-live" />{health?.provider === 'openai' ? '照片将发送至所选云端模型' : '当前会话仅在本机工作区'}</span>
          <span className="lrc-state"><Check size={13} />参数可导出 · Lightroom 插件桥接待配置</span>
        </footer>
      </section>
    </main>
  )
}
