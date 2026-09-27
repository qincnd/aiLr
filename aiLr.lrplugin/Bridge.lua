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

local SETTING_NAMES = {
    Exposure2012 = "Exposure",
    Contrast2012 = "Contrast",
    Highlights2012 = "Highlights",
    Shadows2012 = "Shadows",
    Whites2012 = "Whites",
    Blacks2012 = "Blacks",
    Temperature = "Temperature",
    Tint = "Tint",
    Vibrance = "Vibrance",
    Saturation = "Saturation",
    Texture = "Texture",
    Clarity2012 = "Clarity",
    Dehaze = "Dehaze",
}

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
    }
    for index = 6, #lines do
        local key, value = string.match(lines[index], "^([^=]+)=([^=]+)$")
        if key and SETTING_NAMES[key] then
            local number = tonumber(value)
            if number then
                job.settings[key] = number
            end
        end
    end
    return job
end

local function apply_settings(job)
    if next(job.settings) == nil then
        error("The Lightroom job contains no develop settings")
    end
    for key, value in pairs(job.settings) do
        local controllerName = SETTING_NAMES[key]
        if not controllerName then
            error("Unsupported Lightroom develop setting: " .. tostring(key))
        end
        LrDevelopController.setValue(controllerName, value)
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
    os.remove(renderedPath)
    return image, LrPathUtils.leafName(renderedPath)
end

local function post_job_failure(job, message)
    post_text(API .. "/jobs/" .. job.id .. "/failed", tostring(message):sub(1, 1000))
end

local function run_job(job)
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
    LrDialogs.message("aiLr Bridge", "Bridge stopped.", "info")
else
    prefs.aiLrBridgeRunning = true
    LrTasks.startAsyncTask(function()
        local ok, message = protected_call(poll_bridge)
        prefs.aiLrBridgeRunning = false
        if not ok then
            LrDialogs.message("aiLr Bridge", tostring(message), "warning")
        end
    end)
    LrDialogs.message("aiLr Bridge", "Bridge started. Keep Lightroom Classic open and select the photo you want to render.", "info")
end
