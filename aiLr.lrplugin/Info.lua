-- aiLr Lightroom Classic plug-in manifest.
-- The folder that contains this file must have a name ending in ".lrplugin".
return {
    LrSdkVersion = 6.0,
    LrSdkMinimumVersion = 3.0,
    LrToolkitIdentifier = "com.ailr.lightroom.bridge",
    LrPluginName = "aiLr Lightroom Bridge",
    LrLibraryMenuItems = {
        {
            title = "aiLr: Start/Stop Web Bridge",
            file = "Bridge.lua",
        },
    },
}