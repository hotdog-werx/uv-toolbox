local cmd = require("cmd")

local function shell_quote(value)
    return "'" .. value:gsub("'", "'\"'\"'") .. "'"
end

function PLUGIN:MisePath(ctx)
    local args = "uv-toolbox shim --list-paths"
    if ctx.options.config then
        args = "uv-toolbox --config " .. shell_quote(ctx.options.config) .. " shim --list-paths"
    end

    local ok, result = pcall(cmd.exec, args)
    if not ok then
        return {}
    end

    local paths = {}
    for path in result:gmatch("[^\r\n]+") do
        if path ~= "" then
            table.insert(paths, path)
        end
    end
    return paths
end
