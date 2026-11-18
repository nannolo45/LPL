--[[
  lpl_bridge.lua — connects darktable to the LightPhotoLibrary PC agent.

  Install: copy this file to <darktable config dir>/lua/ and add the line
      require "lpl_bridge"
  to <darktable config dir>/luarc   (the agent's --install-bridge does both).
    Windows config dir: %LOCALAPPDATA%\darktable
    Linux config dir:   ~/.config/darktable   (flatpak: ~/.var/app/org.darktable.Darktable/config/darktable)

  How it talks to the agent (plain files, no network):
    <bridge>/request.txt   written by the agent, one command per line, tab separated:
                             open    <image path>    (import if needed and show it in darkroom; first one is shown)
                             import  <image path>    (import only, so it is in the filmstrip)
                             reload  <image path>    (re-read its XMP, after LPL changed rating/labels/tags)
                             forget  <image path>    (remove it from the darktable library)
    <bridge>/alive         timestamp written every few seconds, so the agent knows the bridge runs
    <bridge>/outbox/<name>.jpg + <name>.done
                           written when you leave an image in darkroom: a rendered preview for LPL
                           and a marker telling the agent to sync the XMP back to the NAS now.
    <bridge> = %LOCALAPPDATA%\LightPhotoLibrary\bridge  (Windows)
               ~/.local/share/lightphotolibrary/bridge  (Linux)
]]

local dt = require "darktable"

local MODULE = "lpl_bridge"
local sep = package.config:sub(1, 1)

local function bridge_dir()
  if dt.configuration.running_os == "windows" then
    return (os.getenv("LOCALAPPDATA") or "C:") .. "\\LightPhotoLibrary\\bridge"
  end
  local base = os.getenv("XDG_DATA_HOME")
  if not base or base == "" then base = (os.getenv("HOME") or "") .. "/.local/share" end
  return base .. "/lightphotolibrary/bridge"
end

local BRIDGE = bridge_dir()
local OUTBOX = BRIDGE .. sep .. "outbox"
local REQUEST = BRIDGE .. sep .. "request.txt"
local ALIVE = BRIDGE .. sep .. "alive"

local function log(msg)
  dt.print_log("[lpl] " .. tostring(msg))
end

local function file_exists(p)
  local f = io.open(p, "rb")
  if f then f:close() return true end
  return false
end

local function write_file(p, text)
  local f = io.open(p, "wb")
  if not f then return false end
  f:write(text)
  f:close()
  return true
end

local function norm(p)
  if not p then return "" end
  p = p:gsub("\\", "/")
  if dt.configuration.running_os == "windows" then p = p:lower() end
  return p
end

local function image_path(img)
  return img.path .. sep .. img.filename
end

local function in_working_set(img)
  if not img then return false end
  local wd = norm(BRIDGE):gsub("/bridge$", "/work")
  return norm(img.path):sub(1, #wd) == wd
end

local function find_image(path)
  local target = norm(path)
  for _, img in ipairs(dt.database) do
    if norm(image_path(img)) == target then return img end
  end
  return nil
end

local function import(path)
  local img = find_image(path)
  if img then return img, false end
  local ok, res = pcall(dt.database.import, path)
  if ok then return res, true end
  log("import failed: " .. tostring(res))
  return nil, false
end

local function reload_sidecar(img)
  local xmp = image_path(img) .. ".xmp"
  if file_exists(xmp) then
    local ok, err = pcall(function() img:apply_sidecar(xmp) end)
    if not ok then log("apply_sidecar failed: " .. tostring(err)) end
  end
end

local function show(img)
  local ok, err = pcall(function()
    if dt.gui.current_view() ~= dt.gui.views.darkroom then
      dt.gui.current_view(dt.gui.views.darkroom)
    end
    dt.gui.views.darkroom.display_image(img)
  end)
  if not ok then log("display failed: " .. tostring(err)) end
end

local function forget(path)
  local img = find_image(path)
  if not img then return end
  local ok = pcall(function() dt.database.remove(img) end)
  if not ok then pcall(function() dt.database.delete(img) end) end
end

-- export a rendered preview of an image we were editing and tell the agent it is done
local jpeg = nil
local function export_done(img)
  if not in_working_set(img) then return end
  local name = img.filename
  local jpg = OUTBOX .. sep .. name .. ".jpg"
  local ok, err = pcall(function()
    if not jpeg then jpeg = dt.new_format("jpeg") end
    jpeg.max_width = 1920
    jpeg.max_height = 1920
    pcall(function() jpeg.quality = 88 end)
    jpeg:write_image(img, jpg)
  end)
  if not ok then log("preview export failed: " .. tostring(err)) end
  write_file(OUTBOX .. sep .. name .. ".done", image_path(img))
end

local function handle_requests()
  if not file_exists(REQUEST) then return end
  local f = io.open(REQUEST, "rb")
  if not f then return end
  local text = f:read("*a") or ""
  f:close()
  os.remove(REQUEST)
  local first = nil
  for line in text:gmatch("[^\r\n]+") do
    local cmd, path = line:match("^(%w+)\t(.+)$")
    if cmd == "open" or cmd == "import" then
      local img, fresh = import(path)
      if img then
        if not fresh then reload_sidecar(img) end
        if cmd == "open" and not first then first = img end
      end
    elseif cmd == "reload" then
      local img = find_image(path)
      if img then reload_sidecar(img) end
    elseif cmd == "forget" then
      forget(path)
    end
  end
  if first then show(first) end
end

-- status.txt: what the bridge did at startup, shown on LightPhotoLibrary's Working set page
local STATUS = BRIDGE .. sep .. "status.txt"
local status_lines = {}
local function status(msg)
  table.insert(status_lines, os.date("%H:%M:%S") .. " " .. tostring(msg))
  write_file(STATUS, table.concat(status_lines, "\n") .. "\n")
  log(msg)
end

local function tick()
  write_file(ALIVE, tostring(os.time()))
  local ok, err = pcall(handle_requests)
  if not ok then status("request failed: " .. tostring(err)) end
end

status("loaded in darktable " .. tostring(dt.configuration.version) .. ", Lua API " .. tostring(dt.configuration.api_version_string))
write_file(ALIVE, tostring(os.time()))

-- leaving an image in darkroom (switching image or view) = finished editing it for now
local current = nil
local ok_ev, err_ev = pcall(function()
  dt.register_event(MODULE .. "_loaded", "darkroom-image-loaded", function(event, ...)
    local args = { ... }
    local img = args[#args]
    if current and img and current ~= img then export_done(current) end
    current = img
    tick()
  end)
  dt.register_event(MODULE .. "_view", "view-changed", function(event, old_view, new_view)
    if old_view == dt.gui.views.darkroom and current then
      export_done(current)
      current = nil
    end
    tick()
  end)
end)
if not ok_ev then status("could not register darkroom events: " .. tostring(err_ev)) end

-- main loop: check for requests every second in the background
local ok_bg, err_bg = pcall(function()
  dt.control.dispatch(function()
    local ok_loop, err_loop = pcall(function()
      while not dt.control.ending do
        tick()
        dt.control.sleep(1000)
      end
    end)
    if not ok_loop then status("background loop stopped: " .. tostring(err_loop)) end
  end)
end)
if ok_bg then
  status("background loop started")
else
  -- older darktable without dispatch: react to activity instead of a timer
  status("no background loop (" .. tostring(err_bg) .. "), falling back to checking on mouse/selection activity")
  for _, ev in ipairs({ "mouse-over-image-changed", "selection-changed", "collection-changed" }) do
    pcall(dt.register_event, MODULE .. "_" .. ev, ev, function() tick() end)
  end
end
