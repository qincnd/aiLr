local LrApplication = import "LrApplication"
local LrApplicationView = import "LrApplicationView"
local LrDevelopController = import "LrDevelopController"
local LrDialogs = import "LrDialogs"
local LrExportSession = import "LrExportSession"
local LrFileUtils = import "LrFileUtils"
local LrHttp = import "LrHttp"
local LrPathUtils = import "LrPathUtils"
local LrTasks = import "LrTasks"
local LrPrefs = import "LrPrefs"

local API = "http://127.0.0.1:8000/api/lightroom"
local prefs = LrPrefs.prefsForPlugin()

-- Bump this string whenever Bridge.lua changes. Every dialog and every failure
-- reported to the web page carries it, so it is obvious whether Lightroom is
-- still executing a cached older copy of this file. After editing Bridge.lua the
-- plug-in must be reloaded (File > Plug-in Manager > Reload) or Lightroom restarted.
local BRIDGE_VERSION = "2026.09.28.1"

-- Bridge work has to suspend the task: LrHttp.get/post, LrTasks.sleep,
-- LrApplicationView.switchToModule and the LrExportSession renditions all yield.
-- Standard Lua pcall runs the callee across a C-call boundary, where yielding is
-- forbidden, so Lightroom fails with "Yielding is not allowed within a C or
-- metamethod call". LrTasks.pcall is the SDK protected call that keeps yielding allowed.
local function protected_call(target, ...)
    if LrTasks.pcall then
        return LrTasks.pcall(target, ...)
    end
    -- Hosts without LrTasks.pcall: forward the callee's yields to the task coroutine.
    local unpackValues = table.unpack or unpack
    local thread = coroutine.create(target)
    local arguments = { ... }
    while true do
        local ok, result = coroutine.resume(thread, unpackValues(arguments))
        if not ok then
            return false, result
        end
        if coroutine.status(thread) == "dead" then
            return true, result
        end
        arguments = { coroutine.yield(result) }
    end
end

-- Every Lightroom Classic develop parameter aiLr can drive. Keys are the crs/XMP
-- setting names used on the wire; CONTROLLER_ALIASES holds the few parameters
-- whose LrDevelopController name differs. Keep in sync with
-- backend/app/settings.py (develop_controls_payload()).
local SETTING_KEYS = {
    "Exposure2012", "Contrast2012", "Highlights2012", "Shadows2012",
    "Whites2012", "Blacks2012", "Texture", "Clarity2012",
    "Dehaze", "Vibrance", "Saturation", "ConvertToGrayscale",
    "Temperature", "Tint", "WhiteBalance", "ToneCurveName",
    "ToneCurvePV2012", "ToneCurvePV2012Red", "ToneCurvePV2012Green", "ToneCurvePV2012Blue",
    "ParametricShadows", "ParametricDarks", "ParametricLights", "ParametricHighlights",
    "ParametricShadowSplit", "ParametricMidtoneSplit", "ParametricHighlightSplit", "HueAdjustmentRed",
    "HueAdjustmentOrange", "HueAdjustmentYellow", "HueAdjustmentGreen", "HueAdjustmentAqua",
    "HueAdjustmentBlue", "HueAdjustmentPurple", "HueAdjustmentMagenta", "SaturationAdjustmentRed",
    "SaturationAdjustmentOrange", "SaturationAdjustmentYellow", "SaturationAdjustmentGreen", "SaturationAdjustmentAqua",
    "SaturationAdjustmentBlue", "SaturationAdjustmentPurple", "SaturationAdjustmentMagenta", "LuminanceAdjustmentRed",
    "LuminanceAdjustmentOrange", "LuminanceAdjustmentYellow", "LuminanceAdjustmentGreen", "LuminanceAdjustmentAqua",
    "LuminanceAdjustmentBlue", "LuminanceAdjustmentPurple", "LuminanceAdjustmentMagenta", "ColorGradeGlobalHue",
    "ColorGradeGlobalSat", "ColorGradeGlobalLum", "ColorGradeShadowHue", "ColorGradeShadowSat",
    "ColorGradeShadowLum", "ColorGradeMidtoneHue", "ColorGradeMidtoneSat", "ColorGradeMidtoneLum",
    "ColorGradeHighlightHue", "ColorGradeHighlightSat", "ColorGradeHighlightLum", "ColorGradeBlending",
    "SplitToningShadowHue", "SplitToningShadowSaturation", "SplitToningHighlightHue", "SplitToningHighlightSaturation",
    "SplitToningBalance", "Sharpness", "SharpenRadius", "SharpenDetail",
    "SharpenEdgeMasking", "LuminanceSmoothing", "LuminanceDetail", "LuminanceContrast",
    "ColorNoiseReduction", "ColorNoiseReductionDetail", "ColorNoiseReductionSmoothness", "PostCropVignetteAmount",
    "PostCropVignetteMidpoint", "PostCropVignetteFeather", "PostCropVignetteRoundness", "PostCropVignetteStyle",
    "GrainAmount", "GrainSize", "GrainFrequency", "AutoLateralCA",
    "LensProfileEnable", "LensManualDistortionAmount", "ChromaticAberrationR", "ChromaticAberrationB",
    "DefringePurpleAmount", "DefringePurpleHueLo", "DefringePurpleHueHi", "DefringeGreenAmount",
    "DefringeGreenHueLo", "DefringeGreenHueHi", "PerspectiveUpright", "PerspectiveVertical",
    "PerspectiveHorizontal", "PerspectiveRotate", "PerspectiveScale", "PerspectiveX",
    "PerspectiveY", "PerspectiveAspect", "AutoTone",
}

-- crs key -> LrDevelopController parameter name, only where the two differ.
local CONTROLLER_ALIASES = {
    Exposure2012 = "Exposure",
    Contrast2012 = "Contrast",
    Highlights2012 = "Highlights",
    Shadows2012 = "Shadows",
    Whites2012 = "Whites",
    Blacks2012 = "Blacks",
    Clarity2012 = "Clarity",
}

local BOOLEAN_SETTINGS = {
    ConvertToGrayscale = true,
    AutoLateralCA = true,
    LensProfileEnable = true,
    AutoTone = true,
}

-- Enum values travel as text (for example ToneCurveName = "Medium Contrast").
local ENUM_SETTINGS = {
    WhiteBalance = true,
    ToneCurveName = true,
    PostCropVignetteStyle = true,
    PerspectiveUpright = true,
}

-- Point curves carry a table of {x, y} pairs.
local CURVE_SETTINGS = {
    ToneCurvePV2012 = true,
    ToneCurvePV2012Red = true,
    ToneCurvePV2012Green = true,
    ToneCurvePV2012Blue = true,
}

local SETTING_KINDS = {}
for _, key in ipairs(SETTING_KEYS) do
    SETTING_KINDS[key] = "number"
end
for key in pairs(BOOLEAN_SETTINGS) do
    SETTING_KINDS[key] = "bool"
end
for key in pairs(ENUM_SETTINGS) do
    SETTING_KINDS[key] = "string"
end
for key in pairs(CURVE_SETTINGS) do
    SETTING_KINDS[key] = "curve"
end

local function controller_name(key)
    return CONTROLLER_ALIASES[key] or key
end

local MIME_TYPES = {
    JPEG = "image/jpeg",
    PNG = "image/png",
    TIFF = "image/tiff",
}

local function response_status(headers)
    if type(headers) ~= "table" then
        return nil
    end
    return tonumber(headers.status)
end

local function url_encode(value)
    return (string.gsub(value, "([^%w%-_%.~])", function(character)
        return string.format("%%%02X", string.byte(character))
    end))
end

local function url_decode(value)
    local text = (value or ""):gsub("%+", " ")
    return (string.gsub(text, "%%(%x%x)", function(hex)
        return string.char(tonumber(hex, 16))
    end))
end

-- Point curves arrive percent-encoded as "x,y;x,y".
local function parse_curve(value)
    local points = {}
    for pair in string.gmatch(url_decode(value), "[^;]+") do
        local x, y = string.match(pair, "^%s*(%-?[%d%.]+)%s*,%s*(%-?[%d%.]+)%s*$")
        if x and y then
            points[#points + 1] = { tonumber(x), tonumber(y) }
        end
    end
    if #points < 2 then
        return nil
    end
    return points
end

local function post_text(url, body)
    local _, headers = LrHttp.post(
        url,
        body or "",
        {{ field = "Content-Type", value = "text/plain; charset=utf-8" }},
        "POST",
        10
    )
    return response_status(headers)
end

local function selected_photo_name()
    local catalog = LrApplication.activeCatalog()
    local photo = catalog:getTargetPhoto()
    if not photo then
        return ""
    end
    local path = photo:getRawMetadata("path") or ""
    return LrPathUtils.leafName(path)
end

local function decode_job(body)
    local lines = {}
    for line in string.gmatch((body or "") .. "\n", "([^\n]*)\n") do
        lines[#lines + 1] = string.gsub(line, "\r", "")
    end
    if #lines < 5 or lines[1] == "" then
        return nil
    end

    local job = {
        id = lines[1],
        action = lines[2],
        format = lines[3],
        quality = tonumber(lines[4]) or 90,
        maxDimension = tonumber(lines[5]) or 2560,
        settings = {},
        unsupported = {},
    }
    for index = 6, #lines do
        local key, value = string.match(lines[index], "^([^=]+)=([^=]+)$")
        if key then
            local kind = SETTING_KINDS[key]
            if kind == "number" then
                local number = tonumber(value)
                if number then
                    job.settings[key] = number
                end
            elseif kind == "bool" then
                job.settings[key] = (url_decode(value) == "true")
            elseif kind == "string" then
                local text = url_decode(value)
                -- Numeric enums (PostCropVignetteStyle, PerspectiveUpright) must reach
                -- the host as numbers; text enums (WhiteBalance, ToneCurveName) as text.
                job.settings[key] = tonumber(text) or text
            elseif kind == "curve" then
                local points = parse_curve(value)
                if points then
                    job.settings[key] = points
                else
                    job.unsupported[#job.unsupported + 1] = key
                end
            else
                job.unsupported[#job.unsupported + 1] = key
            end
        end
    end
    return job
end

local function post_report(job, report)
    -- Reports travel on their own endpoint so a render that only lost a few
    -- parameters can still finish and still tell the web page what happened.
    post_text(API .. "/jobs/" .. job.id .. "/report", report:sub(1, 4000))
end

local function apply_settings(job)
    local keys = {}
    for key in pairs(job.settings) do
        keys[#keys + 1] = key
    end
    table.sort(keys)
    if #keys == 0 then
        error("The Lightroom job contains no develop settings")
    end

    -- Every parameter is applied on its own: a control that this Lightroom
    -- version does not know (or that the photo's process version refuses) is
    -- reported instead of aborting the whole render.
    local applied = 0
    local rejectedKeys, rejectedDetails = {}, {}
    for _, key in ipairs(job.unsupported) do
        rejectedKeys[#rejectedKeys + 1] = key
        rejectedDetails[#rejectedDetails + 1] = key .. ": not in this aiLr Bridge build"
    end
    for _, key in ipairs(keys) do
        local value = job.settings[key]
        local ok, message = protected_call(function()
            LrDevelopController.setValue(controller_name(key), value)
        end)
        if ok then
            applied = applied + 1
        else
            local detail = tostring(message):gsub("%s+", " ")
            rejectedKeys[#rejectedKeys + 1] = key
            rejectedDetails[#rejectedDetails + 1] = key .. ": " .. detail
        end
    end

    if applied == 0 then
        error("Lightroom rejected every develop setting (" .. table.concat(rejectedDetails, "; ") .. ")")
    end
    if #rejectedKeys > 0 then
        post_report(
            job,
            "APPLIED=" .. applied
                .. "\nREJECTED=" .. table.concat(rejectedKeys, ",")
                .. "\n" .. table.concat(rejectedDetails, "\n")
        )
    end
end

local function render_photo(photo, job)
    local outputRoot = LrPathUtils.child(LrPathUtils.getStandardFilePath("temp"), "aiLr")
    local outputDirectory = LrPathUtils.child(outputRoot, job.id)
    LrFileUtils.createAllDirectories(outputDirectory)

    local exportSettings = {
        LR_export_destinationType = "specificFolder",
        LR_export_destinationPathPrefix = outputDirectory,
        LR_export_useSubfolder = false,
        LR_collisionHandling = "overwrite",
        LR_format = job.format,
        LR_jpeg_quality = math.max(1, math.min(100, job.quality)) / 100,
        LR_size_doConstrain = true,
        LR_size_resizeType = "longEdge",
        LR_size_maxWidth = job.maxDimension,
        LR_size_maxHeight = job.maxDimension,
        LR_size_units = "pixels",
        LR_export_colorSpace = "sRGB",
        LR_reimportExportedPhoto = false,
        LR_renamingTokensOn = false,
    }
    local session = LrExportSession({
        photosToExport = { photo },
        exportSettings = exportSettings,
    })

    local renderedPath
    for _, rendition in session:renditions({ stopIfCanceled = false }) do
        local succeeded, result = rendition:waitForRender()
        if not succeeded then
            error(result or "Lightroom could not render the selected photo")
        end
        renderedPath = result
    end
    if not renderedPath then
        error("Lightroom did not return a rendered image")
    end

    local file = io.open(renderedPath, "rb")
    if not file then
        error("Cannot read Lightroom render output")
    end
    local image = file:read("*a")
    file:close()
    if not image then
        error("Cannot read Lightroom render output")
    end
    -- The Lightroom Lua sandbox strips the file-system functions of the standard
    -- libraries (os.remove, os.rename, os.tmpname, os.execute, io.popen ...),
    -- so os.remove is nil inside a plug-in. Use the SDK file API instead, and keep
    -- the cleanup best effort: a finished render must not fail because the
    -- temporary file could not be removed.
    protected_call(function()
        if LrFileUtils.exists(renderedPath) then
            LrFileUtils.delete(renderedPath)
        end
    end)
    return image, LrPathUtils.leafName(renderedPath)
end

local function post_job_failure(job, message)
    -- The version prefix is how the web page shows which Bridge.lua build failed.
    local text = "aiLr Bridge " .. BRIDGE_VERSION .. ": " .. tostring(message)
    post_text(API .. "/jobs/" .. job.id .. "/failed", text:sub(1, 1000))
end

local function probe_settings(job)
    -- Read-only capability check: getValue() fails for a parameter the installed
    -- Lightroom version does not expose, which is exactly what the web page wants
    -- to know before it offers that control for manual editing.
    local supported, unsupported, details = {}, {}, {}
    for _, key in ipairs(SETTING_KEYS) do
        local ok, result = protected_call(function()
            return LrDevelopController.getValue(controller_name(key))
        end)
        -- Only a raised error means "this build does not expose the parameter".
        -- A nil value is legal (for example a photo without channel curves), so it
        -- still counts as supported and only earns an explanatory note.
        if ok then
            supported[#supported + 1] = key
            if result == nil then
                details[#details + 1] = key .. ": no value for the selected photo"
            end
        else
            unsupported[#unsupported + 1] = key
            details[#details + 1] = key .. ": " .. tostring(result):gsub("%s+", " ")
        end
    end

    post_report(
        job,
        "SUPPORTED=" .. table.concat(supported, ",")
            .. "\nUNSUPPORTED=" .. table.concat(unsupported, ",")
            .. "\n" .. table.concat(details, "\n")
    )
end

local function run_job(job)
    if job.action == "probe" then
        -- Probing only reads values, so it never touches the photo or the module.
        if not LrApplication.activeCatalog():getTargetPhoto() then
            error("Select a photo in Lightroom Classic before probing develop parameters")
        end
        probe_settings(job)
        return
    end

    local catalog = LrApplication.activeCatalog()
    local photo = catalog:getTargetPhoto()
    if not photo then
        error("Select a photo in Lightroom Classic before requesting a render")
    end
    local selectedPhotoId = photo.localIdentifier

    LrApplicationView.switchToModule("develop")
    LrTasks.sleep(0.5)
    local activePhoto = catalog:getTargetPhoto()
    if not activePhoto or activePhoto.localIdentifier ~= selectedPhotoId then
        error("The selected Lightroom photo changed before the render started")
    end
    photo = activePhoto
    apply_settings(job)

    local image, filename = render_photo(photo, job)
    local mimeType = MIME_TYPES[job.format]
    if not mimeType then
        error("Unsupported export format: " .. tostring(job.format))
    end
    local _, headers = LrHttp.post(
        API .. "/jobs/" .. job.id .. "/result",
        image,
        {
            { field = "Content-Type", value = mimeType },
            { field = "X-aiLr-Filename", value = url_encode(filename) },
        },
        "POST",
        120,
        #image
    )
    if response_status(headers) ~= 200 then
        error("The aiLr server did not accept the Lightroom render")
    end
end

local function poll_bridge()
    while prefs.aiLrBridgeRunning do
        local selectedName = selected_photo_name()
        post_text(API .. "/heartbeat", selectedName)

        local body, headers = LrHttp.get(API .. "/jobs/next", nil, 10)
        if response_status(headers) == 200 and body and body ~= "" then
            local job = decode_job(body)
            if job then
                local ok, message = protected_call(run_job, job)
                if not ok then
                    post_job_failure(job, message)
                end
            end
        end
        LrTasks.sleep(1.5)
    end
end

if prefs.aiLrBridgeRunning then
    prefs.aiLrBridgeRunning = false
    LrDialogs.message("aiLr Bridge", "Bridge stopped (" .. BRIDGE_VERSION .. ").", "info")
else
    prefs.aiLrBridgeRunning = true
    LrTasks.startAsyncTask(function()
        local ok, message = protected_call(poll_bridge)
        prefs.aiLrBridgeRunning = false
        if not ok then
            LrDialogs.message(
                "aiLr Bridge",
                "Bridge " .. BRIDGE_VERSION .. " stopped with an error:\n\n" .. tostring(message),
                "warning"
            )
        end
    end)
    LrDialogs.message(
        "aiLr Bridge",
        "Bridge started (" .. BRIDGE_VERSION .. ") with " .. #SETTING_KEYS
            .. " develop parameters. Keep Lightroom Classic open and select the photo you want to render.",
        "info"
    )
end
