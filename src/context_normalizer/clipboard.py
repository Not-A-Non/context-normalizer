from __future__ import annotations

import shutil
import subprocess
import sys


class ClipboardUnavailable(RuntimeError):
    pass


def _run(command: list[str], *, input_text: str | None = None) -> str:
    completed = subprocess.run(
        command,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"exit code {completed.returncode}"
        raise ClipboardUnavailable(f"clipboard command failed: {detail}")
    return completed.stdout


def read_clipboard() -> str:
    if sys.platform == "win32":
        executable = shutil.which("pwsh") or shutil.which("powershell")
        if not executable:
            raise ClipboardUnavailable("PowerShell is required for clipboard access")
        return _run([executable, "-NoProfile", "-Command", "Get-Clipboard -Raw"])
    if sys.platform == "darwin":
        return _run(["pbpaste"])
    if shutil.which("wl-paste"):
        return _run(["wl-paste", "--no-newline"])
    if shutil.which("xclip"):
        return _run(["xclip", "-selection", "clipboard", "-o"])
    raise ClipboardUnavailable("install wl-clipboard or xclip for clipboard access")


def write_clipboard(value: str) -> None:
    if sys.platform == "win32":
        executable = shutil.which("pwsh") or shutil.which("powershell")
        if not executable:
            raise ClipboardUnavailable("PowerShell is required for clipboard access")
        _run(
            [
                executable,
                "-NoProfile",
                "-Command",
                "Set-Clipboard -Value ([Console]::In.ReadToEnd())",
            ],
            input_text=value,
        )
        return
    if sys.platform == "darwin":
        _run(["pbcopy"], input_text=value)
        return
    if shutil.which("wl-copy"):
        _run(["wl-copy"], input_text=value)
        return
    if shutil.which("xclip"):
        _run(["xclip", "-selection", "clipboard"], input_text=value)
        return
    raise ClipboardUnavailable("install wl-clipboard or xclip for clipboard access")
