"""Step-wise driver for MatElab release publishing (one installer per invocation).

发布脚本本体一次上传全部安装包，容易超出单个 shell 窗口的时长；本驱动把
上传拆成每次调用一个文件，结果写入 release/.matelab-publish-<version>.json，
最后一步再创建发布记录。仅用于本机发布，行为与 publish_matelab.py 一致。
"""
import base64
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

VERSION = "1.14.1"
INSTALLERS = {
    "CPU": ROOT / "release" / "BandScope-1.14.1-Windows-x64-CPU-Setup.exe",
    "NVIDIA": ROOT / "release" / "BandScope-1.14.1-Windows-x64-NVIDIA-Setup.exe",
}
NOTES = ROOT / "release" / "release-notes-1.14.1.txt"
STATE = ROOT / "release" / f".matelab-publish-{VERSION}.json"


def _credentials():
    from PyQt5.QtCore import QSettings

    from bandscope.updates.matelab_source import MatelabCredentials

    settings = QSettings("ARPES", "ARPES_3dMAP")
    username = str(settings.value("updates/matelab_username", "") or "")
    encoded = str(settings.value("updates/matelab_password_b64", "") or "")
    password = base64.b64decode(encoded).decode("utf-8") if encoded else ""
    if not username or not password:
        raise SystemExit("本机 QSettings 中没有 MatElab 登录凭证。")
    return MatelabCredentials(username=username, password=password)


def _session():
    from bandscope.updates.matelab_source import MATELAB_API_BASE, MATELAB_RELEASE_NOTEBOOK, MatelabSession

    session = MatelabSession(_credentials(), MATELAB_API_BASE, timeout=60, opener=None)
    session.login()
    print("MatElab 登录成功。", flush=True)
    return session, MATELAB_RELEASE_NOTEBOOK


def _resume_upload(session, notebook, state, *, budget_seconds):
    """跨进程续传 NVIDIA 安装包：同一 uid 逐片追加，每片成功后落盘进度。

    时间预算在「每片开始之前」检查，避免单片传到一半被 shell 超时杀掉
    （那样无法确定服务器是否已收到该片，只能换新 uid 重来）。
    """
    import hashlib
    import json as _json
    import urllib.request

    from bandscope.app_metadata import APP_NAME, APP_VERSION
    from bandscope.updates.matelab_source import UPLOAD_CHUNK_SIZE, _multipart_body
    from bandscope.updates.update_service import ReleaseFormatError, UpdateError

    path = INSTALLERS["NVIDIA"]
    total = path.stat().st_size
    partial = state.get("partial")
    if partial is None:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(UPLOAD_CHUNK_SIZE), b""):
                digest.update(chunk)
        partial = {
            "flavor": "NVIDIA",
            "uid": __import__("uuid").uuid4().hex[:32],
            "name": f"bs-{VERSION.lower()}-nvidia-{os.urandom(4).hex()}",
            "hash": digest.hexdigest(),
            "sent": 0,
        }
        state["partial"] = partial
        STATE.write_text(_json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"续传 NVIDIA：uid={partial['uid']}，已从 {partial['sent'] / (1024 ** 2):.0f}/{total / (1024 ** 2):.0f} MiB 开始", flush=True)

    started = time.time()
    sent = int(partial["sent"])
    with path.open("rb") as stream:
        stream.seek(sent)
        while sent < total:
            if time.time() - started > budget_seconds:
                print(f"\n  本轮时间预算用完，进度 {sent / (1024 ** 2):.0f}/{total / (1024 ** 2):.0f} MiB", flush=True)
                return
            chunk = stream.read(UPLOAD_CHUNK_SIZE)
            last = sent + len(chunk) >= total
            fields = [("uid", partial["uid"]), ("name", partial["name"]), ("last", "1" if last else "0"), ("eln", notebook)]
            if last:
                fields.append(("hash", partial["hash"]))
            for attempt in (0, 1):
                boundary, body = _multipart_body(fields, "file", path.name, chunk)
                request = urllib.request.Request(
                    f"{session.base_url}/eln_api/upload",
                    data=body,
                    headers={
                        "Content-Type": f"multipart/form-data; boundary={boundary}",
                        "Authorization": f"Bearer {session.token()}",
                        "User-Agent": f"{APP_NAME}/{APP_VERSION}",
                    },
                    method="POST",
                )
                with session._open(request) as response:
                    try:
                        payload = _json.load(response)
                    except (TypeError, ValueError, UnicodeDecodeError) as exc:
                        raise ReleaseFormatError("MatElab 上传接口返回了无效的 JSON 数据。") from exc
                code = payload.get("code")
                if code == "refresh" and attempt == 0:
                    session.refresh()
                    continue
                break
            if code != 0:
                raise UpdateError(str(payload.get("errmsg") or payload.get("msg") or f"MatElab 上传失败（code={code}）。"))
            sent += len(chunk)
            partial["sent"] = sent
            STATE.write_text(_json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\r  NVIDIA: {sent / (1024 ** 2):.0f}/{total / (1024 ** 2):.0f} MiB", end="", flush=True)
            if last:
                print(f"\n  上传完成，用时 {time.time() - started:.1f}s", flush=True)
                state["uploaded"].append(
                    {"flavor": "NVIDIA", "name": partial["name"], "filename": path.name,
                     "size": payload.get("size", total), "hash": payload.get("hash", partial["hash"])}
                )
                state.pop("partial")
                STATE.write_text(_json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
                return


def main(step):
    from scripts.release.publish_matelab import _create_release_record, _find_notebook
    from bandscope.updates.matelab_source import upload_file

    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"uploaded": []}
    session, notebook = _session()
    _find_notebook(session, notebook)

    if step in INSTALLERS:
        if any(item["flavor"] == step for item in state["uploaded"]):
            print(f"{step} 已上传，跳过。", flush=True)
            return
        path = INSTALLERS[step]
        size = path.stat().st_size
        name = f"bs-{VERSION.lower()}-{step.lower()}-{os.urandom(4).hex()}"
        print(f"上传 {step} 安装包 {path.name}（{size / (1024 ** 2):.1f} MiB）…", flush=True)

        def _progress(sent, total):
            print(f"\r  {step}: {sent / (1024 ** 2):.0f}/{total / (1024 ** 2):.0f} MiB", end="", flush=True)

        started = time.time()
        result = upload_file(session, path, name=name, eln=notebook, progress=_progress)
        print(f"\n  完成，用时 {time.time() - started:.1f}s", flush=True)
        state["uploaded"].append(
            {"flavor": step, "name": name, "filename": path.name,
             "size": result.get("size", size), "hash": result.get("hash", "")}
        )
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    elif step == "resume-nvidia":
        _resume_upload(session, notebook, state, budget_seconds=255)
    elif step == "record":
        uploaded = state["uploaded"]
        if len(uploaded) != 2:
            raise SystemExit(f"需要 2 个已上传安装包，当前 {len(uploaded)} 个。")
        notes = NOTES.read_text(encoding="utf-8").strip()
        _create_release_record(session, notebook, VERSION, notes, uploaded)
        print(f"BandScope v{VERSION} 已发布到 MatElab 记录本「{notebook}」。", flush=True)
    else:
        raise SystemExit(f"未知步骤 {step!r}（CPU / NVIDIA / record）")


if __name__ == "__main__":
    main(sys.argv[1])
