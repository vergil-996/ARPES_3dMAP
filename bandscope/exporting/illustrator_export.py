from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


class IllustratorError(RuntimeError):
    pass


def _executable_from_command(command):
    match = re.search(r'"([^"]+\.exe)"|([A-Za-z]:\\.*?\.exe)', str(command), re.I)
    return match.group(1) or match.group(2) if match else None


def illustrator_available():
    if os.name != "nt":
        return False
    import winreg

    keys = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Illustrator.exe"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Illustrator.exe"),
    )
    try:
        clsid = winreg.QueryValue(winreg.HKEY_CLASSES_ROOT, r"Illustrator.Application\CLSID")
        command = winreg.QueryValue(winreg.HKEY_CLASSES_ROOT, rf"CLSID\{clsid}\LocalServer32")
        executable = _executable_from_command(command)
        if executable and Path(executable).is_file():
            return True
    except OSError:
        pass
    for root, key in keys:
        try:
            executable = winreg.QueryValue(root, key)
        except OSError:
            continue
        if Path(executable).is_file():
            return True
    return False


def _conversion_script():
    return r'''$ErrorActionPreference = "Stop"
$source = $args[0].Replace('\', '/')
$target = $args[1].Replace('\', '/')
$jsx = '(function(){var doc=null;var level=app.userInteractionLevel;try{' +
  'app.userInteractionLevel=UserInteractionLevel.DONTDISPLAYALERTS;' +
  'doc=app.open(new File("' + $source + '"));' +
  'var options=new IllustratorSaveOptions();' +
  'options.pdfCompatible=true;options.compressed=true;' +
  'doc.saveAs(new File("' + $target + '"),options);' +
  'doc.close(SaveOptions.DONOTSAVECHANGES);doc=null;' +
  '}catch(e){if(doc!==null){doc.close(SaveOptions.DONOTSAVECHANGES);}throw e;}' +
  'finally{app.userInteractionLevel=level;}})();'
$app = New-Object -ComObject Illustrator.Application
try {
  $result = $app.DoJavaScript($jsx)
  if (-not (Test-Path -LiteralPath $args[1])) {
    throw "Illustrator did not create the AI file. Result: $result"
  }
} finally {
  [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($app)
}
'''


def convert_pdf_to_ai(pdf_path, ai_path, timeout=180):
    if not illustrator_available():
        raise IllustratorError("未检测到 Adobe Illustrator；请改用 PDF 或 SVG，或安装 Illustrator 后重试。")
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell is None:
        raise IllustratorError("未找到 Windows PowerShell，无法调用 Illustrator 转换 .ai。")
    source = str(Path(pdf_path).resolve())
    target = str(Path(ai_path).resolve())
    if Path(target).exists():
        raise IllustratorError("Illustrator 转换需要使用新的临时输出路径。")
    with tempfile.TemporaryDirectory(prefix="bandscope-ai-") as directory:
        script = Path(directory) / "convert.ps1"
        script.write_text(_conversion_script(), encoding="ascii", newline="\n")
        command = [
            powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(script), source, target,
        ]
        kwargs = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {}
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True,
                encoding="mbcs" if os.name == "nt" else None, errors="replace",
                timeout=int(timeout), check=False, **kwargs
            )
        except subprocess.TimeoutExpired as exc:
            raise IllustratorError("Illustrator 转换超时；请改用 PDF 导出后手动另存为 .ai。") from exc
        except OSError as exc:
            raise IllustratorError(f"无法启动 Illustrator 转换：{exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "未知错误").strip()
        Path(target).unlink(missing_ok=True)
        raise IllustratorError(f"Illustrator 转换失败：{detail}")
    if not Path(target).is_file() or Path(target).stat().st_size <= 0:
        Path(target).unlink(missing_ok=True)
        raise IllustratorError("Illustrator 未生成有效的 .ai 文件；请改用 PDF 导出后手动另存。")
