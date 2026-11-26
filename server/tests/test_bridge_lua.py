# SPDX-License-Identifier: GPL-3.0-or-later
"""The darktable plug-in (agent/darktable/lpl_bridge.lua) shows the agent's preview rendering as a darktable background
job: run under real Lua with a stand-in darktable module (tests/lua/bridge_render_check.lua). Skipped without lua5.4."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LUA = shutil.which("lua5.4") or shutil.which("lua")


@pytest.mark.skipif(not LUA, reason="no Lua interpreter")
def test_render_progress_in_darktable(tmp_path):
    r = subprocess.run([LUA, str(Path(__file__).parent / "lua" / "bridge_render_check.lua"),
                        str(ROOT / "agent" / "darktable" / "lpl_bridge.lua"), str(tmp_path)],
                       capture_output=True, text=True, timeout=60, env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stdout + r.stderr


@pytest.mark.skipif(not shutil.which("luac5.4"), reason="no luac")
def test_bridge_syntax():
    r = subprocess.run(["luac5.4", "-p", str(ROOT / "agent" / "darktable" / "lpl_bridge.lua")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
