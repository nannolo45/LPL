-- Loads agent/darktable/lpl_bridge.lua with a stand-in darktable module and drives its background loop, to check the
-- rendering job it shows in darktable (render.txt -> background job, percent, Cancel -> render-stop.txt, end message).
-- Run by tests/test_bridge_lua.py: lua5.4 bridge_render_check.lua <bridge.lua> <home>
local script, home = arg[1], arg[2]
local jobs, prints, queued = {}, {}, {}
package.preload["darktable"] = function()
  return {
    configuration = { running_os = "linux", version = "5.2.1", api_version_string = "9.4.0" },
    print_log = function() end,
    print = function(m) prints[#prints + 1] = m end,
    gui = { views = {}, create_job = function(text, bar, cancel)
      local j = { text = text, bar = bar, cancel = cancel, percent = 0, valid = true }
      jobs[#jobs + 1] = j
      return j
    end },
    control = { ending = false, sleep = function() end, dispatch = function(f, ...) queued[#queued + 1] = { f, { ... } } end },
    register_event = function() end,
    destroy_event = function() end,
    database = {},
  }
end
local bridge = home .. "/.local/share/lightphotolibrary/bridge"
os.execute("mkdir -p '" .. bridge .. "'")
local function write(name, text) local f = assert(io.open(bridge .. "/" .. name, "wb")); f:write(text); f:close() end
local function read(name) local f = io.open(bridge .. "/" .. name, "rb"); if not f then return nil end; local t = f:read("*a"); f:close(); return t end
local function run_loop() local q = queued; queued = {}; for _, x in ipairs(q) do x[1](table.unpack(x[2])) end end
local function check(cond, what) if not cond then io.stderr:write("FAILED: " .. what .. "\n"); os.exit(1) end end

write("render.txt", "6\t40\t0\t40\t1\t0\t\n")  -- an old, ended job: nothing shown, no message at start
dofile(script)
run_loop()
check(#jobs == 0 and #prints == 0, "an ended job from before is not shown")

write("render.txt", "7\t3\t1\t20\t0\t0\tDSCF5845.RAF +5\n")
run_loop()
check(#jobs == 1 and jobs[1].bar == true and jobs[1].text:find("20 previews"), "a running job is shown with a bar")
check(math.abs(jobs[1].percent - 0.2) < 1e-9, "percent is (done + failed) / total")
write("render.txt", "7\t10\t1\t20\t0\t0\tx\n")
run_loop()
check(#jobs == 1 and math.abs(jobs[1].percent - 0.55) < 1e-9, "the same job is updated, not shown twice")
jobs[1].cancel(jobs[1])
check(read("render-stop.txt") == "7", "Cancel asks the agent to stop job 7")
write("render.txt", "7\t11\t1\t20\t1\t1\t\n")
run_loop()
check(jobs[1].valid == false, "the job goes away when it ended")
local last = prints[#prints]
check(last and last:find("stopped") and last:find("11 rendered") and last:find("1 failed"), "end message: " .. tostring(last))
local n = #prints
run_loop()
check(#prints == n, "the end message is said once")
write("render.txt", "8\t0\t0\t5\t0\t0\t\n")
run_loop()
check(#jobs == 2 and jobs[2].valid, "a new job gets a new bar")
print("ok")
