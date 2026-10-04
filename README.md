# aiLr 智能调色工作台

面向 Lightroom Classic 的 AI 调色工作台：网页负责选图和 AI 调色，Python API 调用本地视觉模型或 OpenAI-compatible 云端视觉模型；通过 Lightroom Classic Lua 插件把参数应用到当前选中的目录照片，由 LrC 实际渲染预览和导出。
插件已接入 LrC 的全部可用 Develop 参数（107 项，覆盖基本、白平衡、色调曲线、混色器 HSL、颜色分级、分离色调、细节、效果、镜头校正、变换与黑白/自动色调），参数表以 `backend/app/settings.py` 为唯一真值源，网页、MCP 与插件共用同一份定义；「全部 LrC 调色参数」面板里的开关表示「是否允许大模型调整该参数」——默认除 19 项实验参数外全部允许，行末的 `＋` 可把参数以当前值手工加入本次渲染，所以整套流程不依赖 AI 建议。
与 LrC 版本或镜头配置文件绑定的少数参数（镜头校正、去边、变换/Upright）标记为「实验」：默认不允许模型调整、也不会随建议写入，需要时可在面板里手动允许或直接用 `＋` 加入本次渲染；宿主拒绝时只跳过该项并在网页给出提示，不会让整次渲染失败。可用网页上的「检测 LrC 支持」让插件在当前 LrC 版本上逐项自检。
目前未完成在前端页面实时预览调色后的图像，请手动打开LrC软件完成调色结果的查看。
开启服务后，可以打开网页右上角的使用指南查看详细的使用教程与预设参数规范等信息。

## 许可证

本项目采用 [MIT License](LICENSE)。

## 技术结构

- `frontend/`：Vite、React、TypeScript 调色工作台。
- `backend/app/`：FastAPI、模型适配器与 Lightroom 参数校验。
- `backend/mcp_server.py`：官方 MCP Python SDK v2 stdio server，提供参数范围查询与校验工具。
- `frontend/public/使用指南.html`：静态使用指南页，前端顶栏「使用指南」按钮在新标签打开（`/使用指南.html`），随 Vite 构建复制到 `dist/`。
- `aiLr.lrplugin/`：Lightroom Classic SDK Lua 插件（目录名必须以 `.lrplugin` 结尾才能被 LrC 加载），通过本地队列接收网页预览/导出任务。
- `backend/data/model_config.json`：网页保存的模型连接配置，运行后自动生成且已加入 Git 忽略。
- `color_presets.json`：项目根目录的调色预设（创作说明模板），网页抽屉的读取、新建与删除都落在这个文件里。

调用链：网页上传照片、调色意图与「允许模型调整的参数集合」-> Ollama/云端视觉模型以资深调色师视角先诊断再输出受约束的 Develop 参数（prompt 固定为「角色 + 工作流：先读图、先修影调与白平衡、再塑形与配色、最后细节与效果 + 输出契约」，并声明本插件只有全局滑块，默认可输出除实验参数与蒙版（效果面板的裁剪后暗角五项，默认关闭）外的全部 83 项，含数值、枚举、开关与点曲线）-> 网页微调（也可完全手动挑选参数）-> Lightroom 插件将参数写入当前选中照片并渲染 JPEG 预览 -> 按 JPEG/PNG/TIFF、质量和最长边选项导出并下载。网页原图与 LrC 当前选中照片文件名必须一致。启动 FastAPI 或 Vite 不会加载大模型；模型未显式启动时，建议接口会拒绝推理请求。选择云端 provider 时，照片会发送到配置的云端服务。

## 环境要求

- Windows 10/11
- Python 3.11 或更高版本
- Node.js 20 或更高版本（含 npm）
- 本地运行需要安装 Ollama 并下载视觉模型；或配置兼容视觉输入的云端 API
- RAW 解码使用 LibRaw（通过 `rawpy`），实际机型支持取决于 LibRaw(一键式脚本会自动哦欸之该环境)

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

## 参数越界时的自动修正

模型给出的 Develop 参数都会按 `backend/app/settings.py` 的注册表逐项校验（键名白名单、数值范围、枚举取值、曲线控制点）。越界不会被静默接受，也不会让整次请求直接失败，而是走一条有限重试链：

- 校验失败时，后端把溢出原因（哪个参数、给了什么值、允许的范围或取值）连同模型刚给出的原答案一起回传，要求它在范围内重新作答；轮数由 `backend/app/services/llm.py` 的 `MAX_PARAMETER_REPAIR_ROUNDS` 控制（默认 2 轮），单次回传的清单最多 `MAX_REPAIR_ISSUES` 条。
- 修好的参数正常进入方案；重试后仍然越界的参数会被丢弃，不会写入 LrC。
- 网页在建议说明下方显示提示条，写明修正了几项、丢弃了哪些参数，并可展开查看逐条越界原因；若所有参数都越界，接口返回错误并提示重新生成。
- Ollama 分支还会把范围与枚举写进结构化输出的 JSON Schema，让越界在生成阶段就被约束；`SYSTEM_PROMPT_CONTRACT` 也声明越界答案会被打回重做。
- 走 MCP 或插件桥接的参数仍用严格校验：`validate_develop_settings()` 遇到第一个非法值就抛错。网页建议链路用 `review_develop_settings()` 取回「可用值 + 越界原因」，两条路径共用同一份注册表。

### 蒙版（裁剪后暗角）：默认关闭 + 伪蒙版护栏

效果组里的裁剪后暗角是唯一能在画面上画出一个可见形状的参数：`PostCropVignetteRoundness` 为正时，暗角会从贴合画幅的椭圆变成画面里的圆，横构图左右两侧会露出一圈弧线；`PostCropVignetteFeather` 接近 0 会留下硬边；数量拉满则变成一圈明显的光晕或黑圈。因此这五项被当作「蒙版」单独管理，默认**关闭**，另外在范围校验之外还有一层护栏：

- **默认关闭**：`MODEL_SETTING_KEYS`（默认允许集合）不含 `PostCropVignette*`；`GET /api/develop/controls` 为这五项返回 `model_default: false`，并额外给出 `mask_keys` 供网页组装开关条。
- **开关在参数区最上方**：「参数微调」标题下方就是「蒙版」开关条：左侧总开关（默认关闭，标签写明 `已开启 / 默认关闭`），右侧是 `关闭 / 轻 / 中 / 强` 强度快捷按钮（数量 -15 / -30 / -50，同时写入羽化 60、圆度 0、中点 50、样式 1）。关闭时既不允许模型给出暗角参数，也会把已加入本次渲染的暗角值一并移除，开关状态与实际写入始终一致；打开后模型才会收到这几个键（`llm.py` 里的暗角窗口规则也只在解锁时追加进提示词）。
- **效果组里标记**：「全部 LrC 调色参数」的效果分区里，这五项带 `蒙版` 标签，与开关条共享同一个允许状态，也可逐项手动开关。
- **安全窗口**：数量 -60 ~ 60、中点 20 ~ 80、羽化 ≥ 25、圆度 ≤ 25，样式只用 1（高光优先）或 2（颜色优先）；`PostCropVignetteStyle = 3`（绘制叠加）会把暗角铺成一层纯色，模型不允许使用。
- **越出窗口的暗角值会和越界参数一样被打回模型重答**（`review_develop_settings(..., guard_vignette=True)`）；模型坚持不改时，`tame_vignette_artifacts()` 把它们收敛到窗口边界，每条改动都写进网页提示条的「已按防伪蒙版规则收敛」明细里，不会静默改值。
- 数量本身就是不可见的归一化小值（例如 `0.2`）时护栏不介入，因为此时其余暗角参数画不出任何形状，不值得多花一轮模型调用。
- 网页手工拖动、插件桥接与 MCP 仍按原值下发（`validate_develop_settings()` 不开护栏，强度快捷键也只是写值），所以想刻意做圆形暗角时不会被拦住。

## 预设调色方向

「调色方向」面板右上角的“调色预设”按钮会从右侧滑出一个抽屉，内置 12 个中文调色方向：胶片暖调人像、自然通透风光、日系清新、电影感青橙、黑白纪实、复古暖褐、冷调高级灰、暗调情绪、霓虹夜景、美食暖调、宠物柔亮、街头硬调。每个方向都写成可执行的调色步骤，并带上必须守住的约束与建议参数区间。

点击任意一张卡片，它的步骤、约束与参考参数会合成一段创作说明填入“告诉 AI 你想要的感觉”输入框并同步字数计数，抽屉随即关闭，内容仍可自由改写；当前输入与某个预设完全一致时，该卡片显示“已填入”，面板上的按钮也会直接显示这个预设名。抽屉支持点击遮罩、右上角按钮和 Esc 键关闭，底部“清空创作说明”用于清空输入框；**不选预设时输入框就是一段自由文本**，和以前一样可以直接写自己的调色方向，两种方式共用同一个输入框。

预设只影响创作说明，不直接写入 LrC 参数——模型连接、参数授权与渲染流程仍按面板当前设置执行。参考参数（`params_hint` 与 `default_intensity`）只是建议：后端会在本次请求的系统提示里说明它不是强制规范，AI 会按画面实际情况判断，但也不能整段忽略。

### 预设文件：项目根目录 `color_presets.json`

预设文案独立存放在项目根目录的 `color_presets.json`，前端不再硬编码方向；后端 `backend/app/presets.py` 是唯一读写入口（路径由 `Path(__file__).resolve().parents[2]` 推导，与 uvicorn 的启动目录无关），文件结构如下：

```json
{
  "version": "2.0",
  "presets": [
    {
      "id": "film-portrait",
      "name": "胶片暖调人像",
      "tags": ["人像", "婚礼", "日常"],
      "default_intensity": 0.8,
      "summary": "暖调肤色、微微褪色的阴影与轻颗粒，适合日常与婚礼照片。",
      "prompt": {
        "steps": ["高光加暖，阴影稍微抬起并带轻微褪色感", "整体饱和度降低，保留衣服和背景色彩层次"],
        "constraints": ["肤色干净通透带奶油感，避免发黄发橙"],
        "params_hint": { "highlights": "+5~+10", "grain": "10~15" }
      }
    }
  ]
}
```

`prompt` 两种写法都支持：上面的对象写法（`steps` 或 `text` 二选一，`constraints` 与 `params_hint` 可省略）与一整段字符串（表单没填参数参考时写的就是这种，与旧结构一致）。格式限制：`steps` 最多 8 条、每条 ≤80 字，`text` 与纯文本写法共用 500 字上限，`constraints` 最多 6 条，`params_hint` 最多 16 项（键名 ≤24 字、取值 ≤32 字）。

每条预设读出来都会多带一句合成好的 `instruction`（创作说明）：正文是步骤与约束，末尾是“参考参数（建议，非强制）”一行。`params_hint` 的短名会补上注册表里的中文标签与 Lightroom 键名（`highlights` → `高光(Highlights2012)`、`blue_sat` → `饱和度 蓝(SaturationAdjustmentBlue)`，写中文短名或原生键名也认，认不出的原样保留），`default_intensity` 会写成“整体强度 0.8”（1 表示完整力度，省略则这一句不出现）。抽屉把 `instruction` 填进输入框，用户改完再由 `POST /api/suggestions` 原样发给模型。

`tags` 是分类标签数组（最多 4 个，每个 ≤8 字），旧写法 `tag: "人像"` 仍然接受并读成 `tags: ["人像"]`；`id` 可以省略（按名称生成，ASCII 名称会得到 `teal-film` 这类连字符 id，重名时自动加 `-2` 后缀），`created_at` 由网页新建时自动补上。整份文件写回时统一标成 `version: "2.0"`，纯文本预设仍按字符串写回、带 `text` 的对象仍按对象写回，不会被来回改写形态。这个文件既能交给 git 管理，也可以直接手工增删条目，改完刷新网页即可生效，不需要重新构建前端；写回时采用“临时文件 + 替换”并保持 UTF-8 无 BOM 与 CRLF，与仓库其它文本文件一致。

### 在网页里新建与删除

抽屉右上角的“新建预设”展开内联表单：名称（≤24 字）、分类（≤8 字，留空记作“自建”）、一句话描述（≤60 字，留空则取创作说明前 48 字）、提示词（≤500 字，打开表单时默认用当前创作说明预填，也可以直接粘贴一段新的方向）和**参数参考（可选）**——一行一个参数，左边参数名（输入框带 `datalist`，候选来自后端的 `params_hint_options()`，即 `HINT_ALIASES` 的短名加上注册表的全部键名）、右边建议区间，最多 16 行，两格都填才会写进文件；表单下方实时预览合成后的“参考参数（建议，非强制）：…”一行，另有一个链接跳到 `frontend/public/使用指南.html#preset-params-hint`（参数名对照表与填写说明）。表单只填提示词时按字符串形态写进 `color_presets.json`，填了参数参考就写成 `{ "text": …, "params_hint": { … } }` 对象（需要分步骤 `steps` 或 `constraints` 时直接编辑文件）。保存后立刻出现在列表末尾；每张卡片底部显示来源（`内置方向` 或 `自建 · 日期`），右下角的“删除”需要两步确认（先点“删除”，再点“确认删除”）。

| 接口 | 说明 |
| --- | --- |
| `GET /api/presets` | 返回 `{ "version": "2.0", "presets": [...], "params_hint_options": [...], "file": "color_presets.json", "path": "..." }`，每条预设都带 `tags`、`prompt`、`instruction` 与可选的 `default_intensity`；`params_hint_options` 是新建表单的候选参数名（`{ "name": "highlights", "label": "高光(Highlights2012)" }`），供前端渲染 `datalist` 与合成预览 |
| `POST /api/presets` | 请求体 `{ name, tag, summary, prompt, params_hint }`（`tags` 数组可选，`prompt` 为一段纯文本，`params_hint` 可省略或为空对象），写入成功返回 `201` 与 `{ "preset": {...}, "presets": [...] }` |
| `DELETE /api/presets/{id}` | 按 id 删除并返回 `{ "presets": [...] }`；id 不存在返回 `404` |

名称、分类、描述、提示词与结构化字段的长度限制（纯文本提示词与合成后的 `instruction` 共用 `PRESET_PROMPT_MAX_LENGTH`，与前端 `PROMPT_MAX_LENGTH` 一致；`params_hint` 键名 ≤24 字、取值 ≤32 字、最多 16 项，与前端表单的 `PRESET_HINT_*` 一致）、同名检查（重名返回 `400`）、最多 50 条的上限都在 `presets.py` 里校验，网页表单的 `maxLength` 只是同一套限制的即时反馈（参数参考的成对填写与合成长度也在提交前先检查一次），出错时抽屉里直接显示后端返回的中文说明。预设文件缺失、不是合法 JSON 或缺少 `presets` 数组时接口返回 `500` 并指出出问题的行或第几项，这类问题只影响预设抽屉，不会影响调色建议与 LrC 渲染。

## RAW 图片

网页文件选择器支持常见的 DNG、CR2/CR3、NEF/NRW、ARW、RAF、ORF、RW2、PEF 等 RAW 扩展名。后端使用 rawpy/LibRaw 按相机白平衡解码，并生成最长边不超过 2048px 的 JPEG 预览供网页显示和模型分析；原始文件不会被改写。普通图片上传上限为 12 MB，RAW 默认上限为 100 MB，可通过 `.env` 中的 `AILR_MAX_IMAGE_MB` 与 `AILR_MAX_RAW_IMAGE_MB` 调整。RAW 是否能解码仍取决于 LibRaw 对具体相机型号和文件版本的支持。
例如NIKON 索尼 哈苏等格式均支持，小米旗舰手机和iPhone等手机厂商拍摄的raw格式的图片也支持(未测试)

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

也可以使用前端页面完成对云端大模型的配置，最后会将配置文件保存在backend/data/model_config.json 中
如果需要对该项目修改，注意不要将api直接上传



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

渲染任务带超时与回收：插件领取任务后 900 秒内没有回传结果（`backend/app/services/lightroom_bridge.py` 的 `JOB_PROCESSING_TIMEOUT_SECONDS`），后端会把该任务标成失败并写明原因，网页立刻看到失败而不是继续等待自己的轮询超时；已完成或已失败的任务连同渲染结果在 900 秒后从任务表回收（`JOB_RETENTION_SECONDS`），队列上限按「排队中 + 执行中」合计 `MAX_ACTIVE_JOBS`（20）计算。网页上传提示里的「JPG / PNG ≤12 MB · RAW ≤100 MB」不再写死，而是读 `GET /api/health` 的 `max_image_mb` / `max_raw_image_mb`（即 `.env` 的 `AILR_MAX_IMAGE_MB` / `AILR_MAX_RAW_IMAGE_MB`）。

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