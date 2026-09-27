# aiLr Lightroom Classic Bridge

这是通过 Lightroom Classic Lua SDK 调用 LrC 实际 Develop/Export 引擎的插件。网页不能直接访问 LrC 目录；插件以当前选中的目录照片为目标，轮询 FastAPI 本地任务队列。

## 目录结构

- `Info.lua`：插件清单。`LrSdkVersion = 6.0`、`LrToolkitIdentifier = "com.ailr.lightroom.bridge"`，并把 `LrLibraryMenuItems` 的菜单项绑定到 `Bridge.lua`。
- `Bridge.lua`：菜单回调。该文件在**点击菜单项时**执行顶层代码，与 Adobe 官方样例（`helloworld.lrdevplugin`、`custommetadatasample.lrdevplugin`）一致，不需要 `return function`。

## 安装与启动

1. 保持 aiLr FastAPI 后端运行在 `http://127.0.0.1:8000`。
2. Lightroom Classic 打开“文件 > 增效工具管理器（插件管理器）”，点击“添加”，在项目根目录选中 `aiLr.lrplugin` 这个**文件夹本身**（不要进入文件夹选文件，也不要选项目根目录）。
3. 添加成功后列表里会出现 “aiLr Lightroom Bridge”，状态为已启用。
4. 切换到“图库”模块，在菜单栏最右侧点击 `aiLr: Start/Stop Web Bridge` 启动轮询，再回到网页。LrC 8 之后该菜单项直接位于“图库”模块菜单栏，不再放在“增效工具额外功能”子菜单里。
5. 在网页和 Lightroom 中选中同名照片。网页会检查插件上报的当前文件名，不匹配时拒绝创建渲染任务。

再次点击同一菜单项可停止轮询。停止或关闭 Lightroom 后，网页状态会在心跳超时后变为离线。

## 渲染行为

- “应用到 LrC 并预览”：插件切换到 Develop 模块，通过 `LrDevelopController.setValue` 应用 AI 参数，再用 `LrExportSession` 生成 2560px JPEG 回传网页。
- “LrC 渲染并下载”：按网页选择的 JPEG/PNG/TIFF 格式和最长边由 LrC 渲染；JPEG 可调质量，成品由浏览器下载。
- 两种操作都会修改当前选中照片的 Develop 状态并创建 Lightroom 可撤销的历史记录；它不是临时预览。请使用副本或虚拟副本验证。
- 预览/导出结果来自 Lightroom Classic，不是网页滤镜或 Python 图像模拟。

## 本地协议

插件每 1.5 秒向 `/api/lightroom/heartbeat` 上报选中照片文件名，并从 `/api/lightroom/jobs/next` 领取任务。输出二进制图像回传到 `/api/lightroom/jobs/{id}/result`；网页轮询任务状态后显示预览或下载文件。

任务参数在 FastAPI 端按 `backend/app/settings.py` 的范围再次校验。插件只接受白名单内的 Develop 参数和 JPEG、PNG、TIFF 格式。桥接 API 仅供本机工作流使用；不要将 FastAPI 绑定到公网。

## 验收边界

当前开发环境没有安装 Lightroom Classic，因此 Lua 已做静态语法检查，后端任务协议已用模拟插件完成往返测试，但尚未在 Adobe 宿主中验证各版本的 Develop 参数名、导出设置和插件菜单加载。首次实机请在虚拟副本上测试，尤其检查撤销历史、白平衡数值和 PNG/TIFF 导出。

故障记录（2026-09-27）：首次实机安装时插件目录被命名为 `lrc-plugin.Irplugin`（后缀为大写 `I`），LrC 直接拒绝添加文件夹；该副本内的 `Info.lua` 还是旧的 `LrSdkVersion = 11.0` 且缺少 `LrToolkitIdentifier`。现仓库内只保留根目录下的 `aiLr.lrplugin` 一份插件，请不要再手工复制改名副本，否则两份清单会不同步。

故障记录（2026-09-27，第 2 条）：实机点击 `aiLr: Start/Stop Web Bridge` 后 LrC 弹出警告「Yielding is not allowed within a C or metamethod call」。原因是 `poll_bridge` / `run_job` 原来用标准 Lua `pcall` 包裹，而 `LrHttp.get/post`、`LrTasks.sleep`、`LrApplicationView.switchToModule` 与 `LrExportSession` 的 renditions 迭代都必须让出（yield）当前任务，`pcall` 属于 C 调用边界，其中禁止 yield。现已改为 `LrTasks.pcall`（SDK 提供的允许 yield 的保护调用，见 `Bridge.lua` 中的 `protected_call`）。若再次出现同类提示，先排查是否新引入了裸 `pcall` / `xpcall`，或在 `LrView` 绑定、菜单回调等非任务（non-task）上下文里直接调用了会 yield 的函数。修改 Lua 后需在 LrC「增效工具管理器」里重新加载插件（或重启 LrC），否则运行的仍是旧代码。
