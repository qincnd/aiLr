# aiLr 智能调色工作台

面向 Lightroom Classic 的 AI 调色工作台：网页负责选图和 AI 调色，Python API 调用本地视觉模型或 OpenAI-compatible 云端视觉模型；通过 Lightroom Classic Lua 插件把参数应用到当前选中的目录照片，由 LrC 实际渲染预览和导出。
插件已接入 LrC 的全部可用 Develop 参数（107 项，覆盖基本、白平衡、色调曲线、混色器 HSL、颜色分级、分离色调、细节、效果、镜头校正、变换与黑白/自动色调），参数表以 `backend/app/settings.py` 为唯一真值源，网页、MCP 与插件共用同一份定义；「全部 LrC 调色参数」面板里的开关表示「是否允许大模型调整该参数」——默认除 19 项实验参数外全部允许，行末的 `＋` 可把参数以当前值手工加入本次渲染，所以整套流程不依赖 AI 建议。
与 LrC 版本或镜头配置文件绑定的少数参数（镜头校正、去边、变换/Upright）标记为「实验」：默认不允许模型调整、也不会随建议写入，需要时可在面板里手动允许或直接用 `＋` 加入本次渲染；宿主拒绝时只跳过该项并在网页给出提示，不会让整次渲染失败。可用网页上的「检测 LrC 支持」让插件在当前 LrC 版本上逐项自检。
目前未完成在前端页面实时预览调色后的图像，请手动打开LrC软件完成调色结果的查看。

## 技术结构

- `frontend/`：Vite、React、TypeScript 调色工作台。
- `backend/app/`：FastAPI、模型适配器与 Lightroom 参数校验。
- `backend/mcp_server.py`：官方 MCP Python SDK v2 stdio server，提供参数范围查询与校验工具。
- `frontend/public/使用指南.html`：静态使用指南页，前端顶栏「使用指南」按钮在新标签打开（`/使用指南.html`），随 Vite 构建复制到 `dist/`。
- `aiLr.lrplugin/`：Lightroom Classic SDK Lua 插件（目录名必须以 `.lrplugin` 结尾才能被 LrC 加载），通过本地队列接收网页预览/导出任务。
- `backend/data/model_config.json`：网页保存的模型连接配置，运行后自动生成且已加入 Git 忽略。

调用链：网页上传照片、调色意图与「允许模型调整的参数集合」-> Ollama/云端视觉模型以资深调色师视角先诊断再输出受约束的 Develop 参数（prompt 固定为「角色 + 工作流：先读图、先修影调与白平衡、再塑形与配色、最后细节与效果 + 输出契约」，并声明本插件只有全局滑块，默认可输出除实验参数外的全部 88 项，含数值、枚举、开关与点曲线）-> 网页微调（也可完全手动挑选参数）-> Lightroom 插件将参数写入当前选中照片并渲染 JPEG 预览 -> 按 JPEG/PNG/TIFF、质量和最长边选项导出并下载。网页原图与 LrC 当前选中照片文件名必须一致。启动 FastAPI 或 Vite 不会加载大模型；模型未显式启动时，建议接口会拒绝推理请求。选择云端 provider 时，照片会发送到配置的云端服务。

## 环境要求

- Windows 10/11
- Python 3.11 或更高版本
- Node.js 20 或更高版本（含 npm）
- 本地运行需要安装 Ollama 并下载视觉模型；或配置兼容视觉输入的云端 API
- RAW 解码使用 LibRaw（通过 `rawpy`），实际机型支持取决于 LibRaw

## 一键脚本（推荐）

根目录提供 PowerShell 脚本和双击入口，行为与下面的手工命令一致：

| 入口 | 用途 |
| --- | --- |
| `scripts\init.ps1`（或双击 `init.bat`） | 创建 `.venv`、安装后端和前端依赖、生成 `.env`、检查 Ollama 模型 |
| `scripts\start.ps1`（或双击 `start.bat`） | 在独立窗口启动 FastAPI 与 Vite，健康检查通过后打开浏览器 |

```powershell
# 初始化：-Force 重建 .venv / 重置 .env，-RunTests 顺带跑单元测试，-SkipModelPull 不下载模型
powershell -ExecutionPolicy Bypass -File .\scripts\init.ps1 -RunTests

# 启动：-BackendOnly / -FrontendOnly / -NoBrowser / -Foreground / -DryRun
powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

脚本自动使用 `py -3`（或 `python`）、`npm` 和 8000/5173 端口：端口已被本机 aiLr 服务占用时直接复用，被其它程序占用时明确报错。`-Foreground` 让后端日志留在当前窗口，便于排查启动失败；`-DryRun` 只检查环境并打印将要执行的命令，不启动任何进程。停止服务的方式是关闭 “aiLr backend” / “aiLr frontend” 窗口，或在窗口内按 Ctrl+C。脚本为幂等设计，重复运行不会破坏已有 `.venv`、`.env` 和 `node_modules`。

## 初始化

在项目根目录 PowerShell 执行（等价于 `scripts\init.ps1`）：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
npm --prefix frontend install
```

如果 PowerShell 不允许激活脚本，也可以不激活环境，直接使用 `\.venv\Scripts\python.exe -m pip ...` 和 `\.venv\Scripts\python.exe -m uvicorn ...`。

## 本地模型启动

确保 Ollama 正在运行后拉取默认视觉模型：

```powershell
ollama pull qwen2.5vl:7b
```

默认模型为 `qwen2.5vl:7b`，地址为 `http://127.0.0.1:11434`。图片推理上下文默认设为 8192 tokens，以容纳视觉输入；相比 Ollama 常见的 4096 默认值会增加内存占用。可提前用 `ollama pull` 下载权重，但 aiLr 不会在后端启动时请求模型推理；只有网页“模型设置”中的“保存并启动”会加载所选模型。模型体积较大，首次下载需要时间和磁盘空间。

## 网页模型设置

点击页面右上角“模型设置”，选择本地 Ollama 或云端模型。打开本地设置时会自动读取 Ollama 已下载模型清单，可选择模型并手动重新检测；检测只读取 `/api/tags`，不会加载模型。填写模型地址后先保存配置再重新检测。云端模式需填写模型 ID、API Base URL 和 API Key。点击“保存配置”只写入设置，不会启动模型；点击“保存并启动”才会加载本地模型或验证云端 API；运行后可以点“停止”卸载本地模型。切换配置后必须再次启动才能生成建议。

模型配置会以 JSON 保存在 `backend/data/model_config.json`。为满足本地持久化，这个文件中的 API Key 是本机明文，只通过写接口接收且不会从读取接口返回；文件已加入 `.gitignore`，不要手动提交或同步到公开位置。要更换密钥时，在网页输入新值并保存。

## RAW 图片

网页文件选择器支持常见的 DNG、CR2/CR3、NEF/NRW、ARW、RAF、ORF、RW2、PEF 等 RAW 扩展名。后端使用 rawpy/LibRaw 按相机白平衡解码，并生成最长边不超过 2048px 的 JPEG 预览供网页显示和模型分析；原始文件不会被改写。普通图片上传上限为 12 MB，RAW 默认上限为 100 MB，可通过 `.env` 中的 `AILR_MAX_IMAGE_MB` 与 `AILR_MAX_RAW_IMAGE_MB` 调整。RAW 是否能解码仍取决于 LibRaw 对具体相机型号和文件版本的支持。

## 启动开发服务

在两个 PowerShell 终端中分别执行（推荐直接运行 `scripts\start.ps1`，见上文“一键脚本”）：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload
```

```powershell
npm --prefix frontend run dev
```

浏览器访问 `http://127.0.0.1:5173`。API 健康状态位于 `http://127.0.0.1:8000/api/health`。

## 使用云端模型

编辑 `.env`，设置：

```dotenv
AILR_MODEL_PROVIDER=openai
AILR_MODEL_NAME=你的视觉模型名称
AILR_OPENAI_BASE_URL=https://api.openai.com/v1
AILR_OPENAI_API_KEY=你的密钥
```

也可将 `AILR_OPENAI_BASE_URL` 指向遵循 Chat Completions、支持图像输入和 JSON 输出的兼容服务。密钥只放在本地 `.env`，不要提交到 Git。

## MCP

VS Code MCP 配置已放在 `.vscode/mcp.json`。创建 `.venv` 并安装依赖后，在 VS Code 的 MCP Servers 面板启动 `ailr-lightroom`，可调用 `list_develop_controls`（分组返回全部 107 个参数及其类型、范围、默认值与 LrC 控制器名）、`list_develop_ranges`（纯数值范围）与 `validate_develop_settings`。独立终端也可运行：

```powershell
.\.venv\Scripts\python.exe backend\mcp_server.py
```

stdio 是机器协议通道，MCP 服务不要向 stdout 输出普通日志。

## Lightroom Classic 预览与导出

1. 在 LrC 的“文件 > 增效工具管理器”中添加 `aiLr.lrplugin` 文件夹（选中该文件夹本身；目录名必须以小写 `.lrplugin` 结尾）。
2. 在 LrC“图库”模块菜单栏右侧点击 `aiLr: Start/Stop Web Bridge` 启动轮询；保持 LrC 和 FastAPI 服务运行，并在 LrC 中选中与网页上传同名的照片。
3. 网页生成建议后点击“应用到 LrC 并预览”，或在「全部 LrC 调色参数」面板手动挑选参数后直接渲染。插件通过 `LrDevelopController` 修改选中照片，再用 `LrExportSession` 渲染 JPEG 回传网页。被当前 LrC 版本拒绝的参数会被跳过并在网页给出提示。
4. 选择 JPEG、PNG 或 TIFF，调整 JPEG 质量/最长边，点击“LrC 渲染并下载”获取 Lightroom 实际渲染的文件。
5. 首次接入后建议点一次「检测 LrC 支持」：插件用只读的 `LrDevelopController.getValue` 逐项自检 107 个参数，网页会标出当前 LrC 版本不接受的项（不会修改照片）。

LrC 的 Develop 参数会写入当前选中照片并产生可撤销的历史记录；预览不是临时模拟。Lightroom 插件通过轮询 `127.0.0.1:8000` 的本地任务 API 工作，未连接插件或文件名不匹配时，网页会阻止渲染。当前执行环境未安装 Lightroom Classic，真实宿主内的插件渲染仍需在安装 LrC 的机器上完成验收。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s backend/tests -v
```

测试不需要启动 Ollama 或 Lightroom Classic。
也不需要连接大模型

## Lightroom Classic 集成边界

请在 Adobe Lightroom Classic 中启用插件，在 Adobe Lightroom Classic 首页 文件 -> 增效工具管理器 内导入 aiLr.lrplugin 插件后才可以与该程序建立连接

## 声明 
本软件仅供学习开发使用