local cmd = require("cmd")

local args = "uv-toolbox shim --list-paths"
if ctx.config and ctx.config.config then
    args = args .. " --config " .. ctx.config.config
end

local result, err = cmd.exec(args)
if err ~= nil then
    return {}
end

local paths = {}
for path in result:gmatch("[^\n]+") do
    if path ~= "" then
        table.insert(paths, path)
    end
end
return paths
