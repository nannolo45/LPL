--[[
  lpl_bridge.lua — connects darktable to the LightPhotoLibrary PC agent.

  Install: the agent's --install-bridge copies this file to <darktable config dir>/lua/lightphotolibrary/
  and adds  require "lightphotolibrary/lpl_bridge"  to <darktable config dir>/luarc.
  With darktable's script manager: lighttable > "scripts" module > folder "lightphotolibrary" > start lpl_bridge.
    Windows config dir: %LOCALAPPDATA%\darktable
    Linux config dir:   ~/.config/darktable   (flatpak: ~/.var/app/org.darktable.Darktable/config/darktable)

  How it talks to the agent (plain files, no network):
    <bridge>/request.txt   written by the agent (and request-local.txt by the PC's local library),
                           one command per line, tab separated:
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
local stopping = false
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

local status  -- defined further down (writes status.txt); declared here so earlier functions can use it

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

-- images LightPhotoLibrary cares about: the agent's working set, plus the PC's local library folders
-- (listed by the local library in roots-local.txt)
local function watched_roots()
  local roots = { (norm(BRIDGE):gsub("/bridge$", "/work")) }
  local f = io.open(BRIDGE .. sep .. "roots-local.txt", "rb")
  if f then
    for line in f:lines() do
      line = line:gsub("[\r\n]", "")
      if line ~= "" then table.insert(roots, (norm(line):gsub("/$", ""))) end
    end
    f:close()
  end
  return roots
end

local function in_working_set(img)
  if not img then return false end
  local p = norm(img.path)
  for _, root in ipairs(watched_roots()) do
    if p == root or p:sub(1, #root + 1) == root .. "/" then return true end
  end
  return false
end

-- Photos this script has handled, by path. darktable's import returns the existing image when the file
-- is already in the library, so there is no need to walk the whole library (with tens of thousands of
-- images that took minutes and froze darktable).
local known = {}

local function find_image(path)
  return known[norm(path)]
end

local function import(path)
  local img = known[norm(path)]
  if img then return img, false end
  local ok, res = pcall(dt.database.import, path)
  if ok and res then
    known[norm(path)] = res
    return res, true
  end
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
  -- darktable enters the darkroom on the selected (or hovered) image and refuses, without an error, when there
  -- is none: with the mouse somewhere else nothing happened. So select the photo first.
  local ok, err = pcall(function()
    pcall(function() dt.gui.selection({ img }) end)
    if dt.gui.current_view() ~= dt.gui.views.darkroom then
      dt.gui.current_view(dt.gui.views.darkroom)
    end
    dt.gui.views.darkroom.display_image(img)
  end)
  if not ok then status("display failed: " .. tostring(err)) end
  local okv, view = pcall(function() return dt.gui.current_view() end)
  if okv and view ~= dt.gui.views.darkroom then
    status("darktable stayed in " .. tostring(view and view.id or "another view") .. ": it did not enter the darkroom")
    return false
  end
  return ok
end

local function forget(path)
  local img = find_image(path)
  if not img then
    local f = io.open(path, "rb")  -- only look it up if the file is still there
    if not f then return end
    f:close()
    img = import(path)
    if not img then return end
  end
  known[norm(path)] = nil
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

-- status.txt: what the bridge did, shown on LightPhotoLibrary's Working set page (last 25 lines)
local STATUS = BRIDGE .. sep .. "status.txt"
local status_lines = {}
status = function(msg)
  table.insert(status_lines, os.date("%H:%M:%S") .. " " .. tostring(msg))
  while #status_lines > 25 do table.remove(status_lines, 1) end
  write_file(STATUS, table.concat(status_lines, "\n") .. "\n")
  log(msg)
end

local function short(path)
  return (tostring(path):match("[^/\\]+$")) or tostring(path)
end

-- Requests are read by the background loop (plain file reading only) and carried out on darktable's
-- main thread, from darktable's own events. Calling darktable (import, switching to darkroom) from the
-- background thread can deadlock darktable on Windows.
local pending = {}
local announced = false

local function read_requests(request_file)
  if not file_exists(request_file) then return end
  local f = io.open(request_file, "rb")
  if not f then return end
  local text = f:read("*a") or ""
  f:close()
  os.remove(request_file)
  for line in text:gmatch("[^\r\n]+") do
    local cmd, path = line:match("^(%w+)\t(.+)$")
    if cmd then
      table.insert(pending, { cmd = cmd, path = path })
      status("request: " .. cmd .. " " .. short(path))
    end
  end
end

local function collect()
  read_requests(REQUEST)
  read_requests(BRIDGE .. sep .. "request-local.txt")
end

local function carry_out()
  if #pending == 0 then return end
  local todo = pending
  pending = {}
  announced = false
  local first = nil
  for _, r in ipairs(todo) do
    if r.cmd == "open" or r.cmd == "import" then
      local img = import(r.path)
      if img then
        status("imported " .. short(r.path))
        reload_sidecar(img)
        if r.cmd == "open" then first = img end  -- the latest open wins
      else
        status("could not import " .. short(r.path) .. " (see darktable log)")
      end
    elseif r.cmd == "reload" then
      local img = import(r.path)
      if img then reload_sidecar(img) end
    elseif r.cmd == "forget" then
      forget(r.path)
    end
  end
  if first then
    status("opening in darkroom: " .. short(first.filename))
    if show(first) then status("opened " .. short(first.filename)) end
  end
end

local busy = false
local last_tick = 0
local rearm = nil  -- set by the loop below

-- background loop: alive + read requests only
local function tick_bg()
  write_file(ALIVE, tostring(os.time()))
  local ok, err = pcall(collect)
  if not ok then status("reading requests failed: " .. tostring(err)) end
  if #pending > 0 and not announced then
    announced = true
    status("waiting for darktable: move the mouse over darktable's window to open it")
  end
end

-- darktable's events (main thread): carry out what's waiting
local function tick(from_activity)
  local now = os.time()
  if from_activity and rearm then pcall(rearm) end
  if from_activity and now - last_tick < 1 and #pending == 0 then return end
  if busy then return end
  busy = true
  last_tick = now
  write_file(ALIVE, tostring(now))
  local ok, err = pcall(function() collect(); carry_out() end)
  if not ok then status("request failed: " .. tostring(err)) end
  busy = false
end

-- Linux: the agent calls this over D-Bus (org.darktable.service.Remote.Lua) right after it has written a request,
-- so a photo opens at once instead of waiting for the mouse to move over darktable's window
_G.lpl_bridge_poke = function()
  status("woken by the agent")
  tick(true)
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
    tick(true)
  end)
  dt.register_event(MODULE .. "_view", "view-changed", function(event, old_view, new_view)
    if old_view == dt.gui.views.darkroom and current then
      export_done(current)
      current = nil
    end
    tick(true)
  end)
end)
if not ok_ev then status("could not register darkroom events: " .. tostring(err_ev)) end

-- main loop: check for requests every second in the background.
-- A chain of short background jobs (each one checks, sleeps, and schedules the next). If the chain stalls,
-- any activity in darktable starts a new one (see rearm below); an older chain stops when a newer one exists.
local rounds = 0
local chain = 0
local last_round = 0
local function round(my_chain)
  if dt.control.ending or stopping or my_chain ~= chain then return end
  last_round = os.time()
  rounds = rounds + 1
  if rounds <= 2 then status("background loop running (round " .. rounds .. ")") end
  local ok, err = pcall(function()
    tick_bg()
    dt.control.sleep(1000)
  end)
  if not ok then status("background loop error: " .. tostring(err)) end
  if not dt.control.ending and not stopping and my_chain == chain then
    local ok2, err2 = pcall(dt.control.dispatch, round, my_chain)
    if not ok2 then status("background loop stopped: " .. tostring(err2)) end
  end
end

local last_rearm = 0
local function start_loop(why)
  chain = chain + 1
  last_round = os.time()
  last_rearm = os.time()
  local ok, err = pcall(dt.control.dispatch, round, chain)
  if ok then status("background loop scheduled" .. (why and (" (" .. why .. ")") or ""))
  else status("no background loop (" .. tostring(err) .. ")") end
end

-- called on darktable activity: restart the loop if it hasn't run for a while
rearm = function()
  local now = os.time()
  if now - last_round > 5 and now - last_rearm > 10 then
    start_loop("restarted: it had stopped")
  end
end

start_loop()

-- backup: also check whenever you use darktable (hover, select, change collection), in case the loop stalls
for _, ev in ipairs({ "mouse-over-image-changed", "selection-changed", "collection-changed", "pixelpipe-processing-complete" }) do
  pcall(dt.register_event, MODULE .. "_" .. ev, ev, function() tick(true) end)
end

-- what darktable's script manager needs to list, start and stop this script
local script_data = {}
script_data.metadata = {
  name = "lpl_bridge",
  purpose = "connects darktable to LightPhotoLibrary: opens photos sent from the library and reports when you are done",
  author = "LightPhotoLibrary",
  help = "https://github.com/nannolo45/LPL",
}
script_data.destroy = function()
  stopping = true
  for _, ev in ipairs({ "darkroom-image-loaded", "view-changed", "mouse-over-image-changed", "selection-changed", "collection-changed", "pixelpipe-processing-complete" }) do
    local name = (ev == "darkroom-image-loaded") and (MODULE .. "_loaded") or (ev == "view-changed") and (MODULE .. "_view") or (MODULE .. "_" .. ev)
    pcall(dt.destroy_event, name, ev)
  end
  status("stopped by the script manager")
end
script_data.destroy_method = "clean"
script_data.restart = nil
script_data.show = nil
return script_data
