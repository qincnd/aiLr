# aiLr 智能调色工作台

面向 Lightroom Classic 的 AI 调色项目起步骨架：网页负责选图和交互，Python API 调用本地视觉模型或 OpenAI-compatible 云端视觉模型，模型只返回受白名单与数值范围约束的 Develop 参数。当前可以生成、手动微调并导出 JSON；Lightroom Classic 写回插件尚未实现。

## 技术结构

- `frontend/`：Vite、React、TypeScript 调色工作台。
- `backend/app/`：FastAPI、模型适配器与 Lightroom 参数校验。
- `backend/mcp_server.py`：官方 MCP Python SDK v2 stdio server，提供参数范围查询与校验工具。
- `lrc-plugin/`：Lightroom Classic 插件桥接设计边界与后续实现说明。

调用链：浏览器上传照片和调色意图 -> FastAPI -> Ollama 或 OpenAI-compatible 视觉模型 -> 校验 Develop 参数 -> 页面手动微调 -> 导出 JSON。选择云端 provider 时，照片会发送到配置的云端服务。

## 环境要求

- Windows 10/11
- Python 3.11 或更高版本
- Node.js 20 或更高版本（含 npm）
- 本地运行需要安装 Ollama 并下载视觉模型；或配置兼容视觉输入的云端 API

## 初始化

在项目根目录 PowerShell 执行：

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

默认 `.env` 配置使用 `AILR_MODEL_PROVIDER=ollama`，地址为 `http://127.0.0.1:11434`。模型体积较大，首次下载需要时间和磁盘空间。

## 启动开发服务

在两个 PowerShell 终端中分别执行：

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

VS Code MCP 配置已放在 `.vscode/mcp.json`。创建 `.venv` 并安装依赖后，在 VS Code 的 MCP Servers 面板启动 `ailr-lightroom`，可调用 `list_develop_controls` 与 `validate_develop_settings`。独立终端也可运行：

```powershell
.\.venv\Scripts\python.exe backend\mcp_server.py
```

stdio 是机器协议通道，MCP 服务不要向 stdout 输出普通日志。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s backend/tests -v
```

测试不需要启动 Ollama 或 Lightroom Classic。

## Lightroom Classic 集成状态

目前导出的是经过校验的参数 JSON，不会直接修改 Lightroom 目录或照片。Lightroom Classic 插件需要在已安装 Adobe Lightroom Classic SDK 的环境中实现：选择当前照片、读取导出的参数、在目录写锁中调用 `LrPhoto:applyDevelopSettings`，再由用户检查和撤销。细节见 `lrc-plugin/README.md`。在插件完成并实机验证前，不要把 JSON 导出称为已写回。
