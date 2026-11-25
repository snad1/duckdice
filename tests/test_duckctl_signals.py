"""duckctl pause / resume / reload: USR1, USR2 or HUP to one lane or to every running lane.

Each lane daemon is stood in for by a real child process that records the signals it gets.
/proc is faked through DUCKCTL_PROC_DIR and HOME is a temp dir, so no real daemon is touched.
Reference: wolfbet/tests/test_phase11i_wolfctl_verbs.py.
"""

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from time import sleep as _sleep   # conftest stubs time.sleep; these waits must be real

import pytest

_CTL = Path(__file__).resolve().parent.parent / "duckctl"

_RECORDER = textwrap.dedent("""
    import signal, sys, time
    out = sys.argv[1]
    def note(sig, frame):
        with open(out, "a") as f:
            f.write(signal.Signals(sig).name + "\\n")
    for s in (signal.SIGUSR1, signal.SIGUSR2, signal.SIGHUP):
        signal.signal(s, note)
    open(out + ".ready", "w").close()
    while True:
        time.sleep(0.02)
""")

_BOT_CMDLINE = "python3\0/root/duckdice-bot/duckdice.py\0--daemon\0"


@pytest.fixture
def host(tmp_path):
    home, proc = tmp_path / "home", tmp_path / "proc"
    home.mkdir()
    proc.mkdir()
    children = []
    yield {"home": home, "proc": proc, "tmp": tmp_path, "children": children}
    for child in children:
        child.kill()
        child.wait()


def _ctl(host, *args):
    env = {**os.environ, "HOME": str(host["home"]), "DUCKCTL_PROC_DIR": str(host["proc"])}
    return subprocess.run(["bash", str(_CTL), *args], capture_output=True, text=True,
                          timeout=20, env=env, cwd=host["tmp"])


def _pid_file(host, k: int) -> Path:
    return host["home"] / (".duckdice_autobot.pid" if k == 1 else f".duckdice_autobot.lane{k}.pid")


def _lane(host, k: int, cmdline: str = _BOT_CMDLINE):
    """Start a recorder for lane k, write its pid file and its fake /proc cmdline."""
    out = host["tmp"] / f"signals{k}.txt"
    child = subprocess.Popen([sys.executable, "-c", _RECORDER, str(out)])
    host["children"].append(child)
    for _ in range(200):
        if Path(f"{out}.ready").exists():
            break
        _sleep(0.01)
    _pid_file(host, k).write_text(f"{child.pid}\n")
    (host["proc"] / str(child.pid)).mkdir()
    (host["proc"] / str(child.pid) / "cmdline").write_text(cmdline)
    return child.pid, out


def _received(path: Path, expect: int = 1) -> list:
    for _ in range(200):
        if path.exists():
            got = path.read_text().split()
            if len(got) >= expect:
                return got
        _sleep(0.01)
    return []


@pytest.mark.parametrize("verb,signame", [("pause", "SIGUSR1"), ("resume", "SIGUSR2"),
                                          ("reload", "SIGHUP")])
def test_verb_sends_its_signal_to_the_named_lane_only(host, verb, signame):
    _, out1 = _lane(host, 1)
    pid2, out2 = _lane(host, 2)
    r = _ctl(host, verb, "2")
    assert r.returncode == 0, r.stderr
    assert f"lane 2: {verb} sent (pid {pid2})" in r.stdout
    assert _received(out2) == [signame]
    _sleep(0.1)
    assert not out1.exists()


def test_default_and_all_reach_every_running_lane(host):
    _, out1 = _lane(host, 1)
    _, out3 = _lane(host, 3)
    _pid_file(host, 2).write_text("999999\n")   # stale
    r = _ctl(host, "pause")
    assert r.returncode == 0, r.stderr
    assert "lane 2: not running (stale pid 999999)" in r.stderr
    r = _ctl(host, "resume", "all")
    assert r.returncode == 0
    assert _received(out1, 2) == ["SIGUSR1", "SIGUSR2"]
    assert _received(out3, 2) == ["SIGUSR1", "SIGUSR2"]


def test_a_pid_that_is_not_the_bot_is_refused(host):
    pid, out = _lane(host, 1, cmdline="/usr/sbin/sshd\0-D\0")
    r = _ctl(host, "pause", "1")
    assert r.returncode == 1
    assert f"lane 1: pid {pid} is not duckdice.py, refused" in r.stderr
    assert "No running lane matched." in r.stderr
    _sleep(0.1)
    assert not out.exists()


@pytest.mark.parametrize("target", ["0", "100", "x", "1;id", "-1"])
def test_a_bad_lane_number_exits_2(host, target):
    r = _ctl(host, "reload", target)
    assert r.returncode == 2 and "Usage: duckctl reload [K|all]" in r.stderr


def test_no_pid_files_exits_1(host):
    r = _ctl(host, "reload")
    assert r.returncode == 1 and "No running lane matched." in r.stderr


def test_a_garbage_pid_file_is_skipped(host):
    _pid_file(host, 1).write_text("12; rm -rf /\n")
    r = _ctl(host, "pause", "1")
    assert r.returncode == 1 and "lane 1: pid file holds no pid, skipped" in r.stderr


def test_usage_lists_the_verbs(host):
    assert "duckctl pause|resume|reload [K|all]" in _ctl(host, "help").stdout
