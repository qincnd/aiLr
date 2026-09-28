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

- “应用到 LrC 并预览”：插件切换到 Develop 模块，通过 `LrDevelopController.setValue` 逐项应用网页参数（最多 107 项，见下节），再用 `LrExportSession` 生成 2560px JPEG 回传网页。
- “LrC 渲染并下载”：按网页选择的 JPEG/PNG/TIFF 格式和最长边由 LrC 渲染；JPEG 可调质量，成品由浏览器下载。
- 两种操作都会修改当前选中照片的 Develop 状态并创建 Lightroom 可撤销的历史记录；它不是临时预览。请使用副本或虚拟副本验证。
- 预览/导出结果来自 Lightroom Classic，不是网页滤镜或 Python 图像模拟。
- 网页「全部 LrC 调色参数」面板支持不经过 AI 直接手工挑选参数；「检测 LrC 支持」按钮会创建一个只读的 `probe` 任务，不修改照片。

## 本地协议

插件每 1.5 秒向 `/api/lightroom/heartbeat` 上报选中照片文件名，并从 `/api/lightroom/jobs/next` 领取任务。任务前 5 行固定为 `任务号 / 动作 / 格式 / 质量 / 最长边`，其后每行一个 `键=值`：数值保持原样（`Temperature=5600`、`Exposure2012=0.35`），布尔为 `true`/`false`，枚举与点曲线用百分号编码（`ToneCurveName=Medium%20Contrast`、`ToneCurvePV2012=0%2C0%3B255%2C255`）。输出二进制图像回传到 `/api/lightroom/jobs/{id}/result`，插件报告回传到 `/api/lightroom/jobs/{id}/report`；网页轮询任务状态后显示预览或下载文件。

任务参数在 FastAPI 端按 `backend/app/settings.py` 的注册表再次校验（键名白名单 + 数值范围 + 枚举取值 + 曲线控制点）。插件只接受白名单内的 Develop 参数和 JPEG、PNG、TIFF 格式。桥接 API 仅供本机工作流使用；不要将 FastAPI 绑定到公网。

## 支持的 Develop 参数

- `Bridge.lua` 顶部的 `SETTING_KEYS` 列出接入的 107 个 LrC Develop 参数，键名与 `crs`（XMP）设置名一致；`CONTROLLER_ALIASES` 只记录键名与 `LrDevelopController` 参数名不同的 7 项（`Exposure2012 -> Exposure`、`Contrast2012 -> Contrast`、`Highlights2012 -> Highlights`、`Shadows2012 -> Shadows`、`Whites2012 -> Whites`、`Blacks2012 -> Blacks`、`Clarity2012 -> Clarity`），其余参数两者同名。
- 值分四类：`number`（数值）、`bool`（`AutoTone`、`AutoLateralCA`、`LensProfileEnable`、`ConvertToGrayscale`）、`string`（枚举：`WhiteBalance`、`ToneCurveName`、`PostCropVignetteStyle`、`PerspectiveUpright`）、`curve`（`ToneCurvePV2012` 及红/绿/蓝三个通道曲线，格式 `x,y;x,y`，插件还原为 `{x, y}` 点表后交给宿主）。
- 分组覆盖：基本（含黑白转换）、白平衡、色调曲线（点曲线 + 参数曲线）、混色器 HSL（8 色 × 色相/饱和度/明亮度）、颜色分级、分离色调、细节（锐化与减少杂色）、效果（裁剪后暗角、颗粒）、镜头校正、变换、其它。
- 镜头校正与变换共 19 项与 LrC 版本和镜头配置文件强相关，注册表里标记为 `experimental`：默认不允许大模型调整（不会进入当次请求的 JSON Schema），需要时可在网页面板里手动打开开关为该次请求解锁，或用行末的 `＋` 直接把参数加入本次渲染；被拒时只跳过该项。
- 参数表在 `backend/app/settings.py` 生成（`develop_controls_payload()` 同时驱动网页与 MCP）：`MODEL_SETTING_KEYS` 是默认允许模型调整的集合（除 `experimental` 外的全部 88 项，数值 / 枚举 / 开关 / 点曲线都在内），网页可用 `POST /api/suggestions` 的 `allowed_keys` 字段按次裁剪；改动后需要同步 `Bridge.lua` 的 `SETTING_KEYS`，两端键名不一致时，插件会把未知键记入报告而不是直接报错。

## 参数被宿主拒绝时

- 每个参数单独用 `protected_call` 调用 `LrDevelopController.setValue`：某一项被当前 LrC 版本或照片的 process version 拒绝时只记录该项，其余参数照常应用，渲染继续。
- 只有全部参数都被拒绝才算渲染失败，失败信息会带上逐项原因。
- 部分被拒绝时，插件先回传 `APPLIED=n` / `REJECTED=键,键` 与逐项原因到 `POST /api/lightroom/jobs/{id}/report`，网页在 Lightroom Classic 区块显示黄色提示；渲染结果仍然正常返回。
- `probe` 动作（网页「检测 LrC 支持」）用只读的 `LrDevelopController.getValue` 逐项自检，回传 `SUPPORTED=...` / `UNSUPPORTED=...`，网页把当前宿主不支持的参数置灰。该动作不写入照片、不切换模块，但需要一个选中的照片作为读取目标。

## 验收边界

当前开发环境没有安装 Lightroom Classic，因此 Lua 已做静态语法检查，后端任务协议已用模拟插件完成往返测试，但尚未在 Adobe 宿主中验证各版本的 Develop 参数名、导出设置和插件菜单加载。首次实机请在虚拟副本上测试，尤其检查撤销历史、白平衡数值和 PNG/TIFF 导出；接入后建议先点一次网页上的「检测 LrC 支持」，用插件回传的 `SUPPORTED` / `UNSUPPORTED` 清单核对当前 LrC 版本实际接受的参数，再决定是否启用镜头校正与变换这两组实验参数。

故障记录由deepseek生成

故障记录（2026-09-27）：首次实机安装时插件目录被命名为 `lrc-plugin.Irplugin`（后缀为大写 `I`），LrC 直接拒绝添加文件夹；该副本内的 `Info.lua` 还是旧的 `LrSdkVersion = 11.0` 且缺少 `LrToolkitIdentifier`。现仓库内只保留根目录下的 `aiLr.lrplugin` 一份插件，请不要再手工复制改名副本，否则两份清单会不同步。

故障记录（2026-09-27，第 2 条）「Yielding is not allowed within a C or metamethod call」：已修复。标准 Lua 的 `pcall`/`xpcall` 是 C 调用边界，而 `LrHttp.get/post`、`LrTasks.sleep`、`LrApplicationView.switchToModule`、`LrExportSession:renditions()` 的迭代都必须 yield，所以旧代码第一圈心跳就报错。现改用 `protected_call`（优先 `LrTasks.pcall`，无此 API 时用 `coroutine` 转发 yield）。新代码不要把会 yield 的函数放进裸 `pcall`/`xpcall`。

故障记录（2026-09-27，第 3 条）`Bridge.lua:182: attempt to call field 'remove' (a nil value)`：已修复。Lightroom Classic 的 Lua 沙箱**删掉了标准库里所有会改动文件系统的函数**（`os.remove`、`os.rename`、`os.tmpname`、`os.execute`、`io.popen` 等），因此在插件里 `os.remove` 的值就是 `nil`，这不是拼写错误，也不是 `os` 表缺失。修法：清理临时渲染文件改用 SDK 的 `LrFileUtils.exists` + `LrFileUtils.delete`，并用 `protected_call` 包成尽力而为（渲染已成功就不该因为删不掉临时文件而判失败）。后续插件里凡是移动、复制、删除文件，一律用 `LrFileUtils.move/copy/delete`，不要再写 `os.*`。

故障记录（2026-09-27，第 4 条）**如何判断 LrC 到底跑的哪一版 `Bridge.lua`**：`Bridge.lua` 现在有 `BRIDGE_VERSION`（当前 `2026.09.28.1`），启动/停止弹窗和回传网页的失败信息都会带版本号（网页红条形如 `aiLr Bridge 2026.09.27.3: ...`）。如果网页红条是老行号（例如 `[string "Bridge.lua"]:182: attempt to call field 'remove'`）且**不带**版本前缀，那就是 LrC 还在执行缓存旧代码：必须「文件 > 增效工具管理器」→ 选中 `aiLr Lightroom Bridge` → 重新加载，或直接重启 LrC。判定依据很硬：第 3 条改完后第 182 行已经是 `if not image then`，`if` 不是函数调用，新代码里不可能再出现这个行号的 `attempt to call`。