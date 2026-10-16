import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from bandscope.app_metadata import installer_asset_name
from bandscope.updates.matelab_source import (
    MatelabAuthError,
    MatelabCredentials,
    MatelabReleaseClient,
)
from bandscope.updates.net import UntrustedHostError
from bandscope.updates.update_service import (
    ReleaseAssetNotFound,
    ReleaseFormatError,
    UpdateError,
)


class FakeResponse(io.BytesIO):
    def __init__(self, payload, url):
        super().__init__(payload)
        self._url = url

    def geturl(self):
        return self._url


CREDENTIALS = MatelabCredentials(username="zhangsan", password="secret")

ACCESS_TOKEN = "access-token-1"
REFRESH_TOKEN = "refresh-token-1"
ACCESS_TOKEN_2 = "access-token-2"

NOTEBOOK = {
    "id": 4955,
    "showtext": "BandScope发布",
    "server": "localhost",
    "owner": True,
    "email": "zhangsan@iphy.ac.cn",
    "userid": 8633,
}

INSTALLER_CPU = b"cpu installer bytes"
INSTALLER_CPU_DIGEST = hashlib.sha256(INSTALLER_CPU).hexdigest()


def _release_record(record_id, sn, version, *, files=None, notes=""):
    asset_name = installer_asset_name(version, "CPU")
    file_fields = files if files is not None else [
        {
            "hash": INSTALLER_CPU_DIGEST,
            "filename": asset_name,
            "size": len(INSTALLER_CPU),
        }
    ]
    return {
        "eln_name": "BandScope发布",
        "id": record_id,
        "title": f"BandScope v{version}",
        "uid": sn,
        "data": [
            {
                "uid": "m1",
                "name": "manifest",
                "type": "richtext",
                "data": f"<p>{{\"version\": \"{version}\", \"notes\": \"{notes}\"}}</p>",
            },
            {
                "uid": "f1",
                "name": "安装包",
                "type": "files",
                "data": file_fields,
            },
        ],
    }


class FakeMatelabServer:
    """按 URL 路径分发的假 MatElab 服务，记录调用历史供断言。"""

    def __init__(self):
        self.calls = []
        self.password = "secret"
        self.records = [
            _release_record(101, "SN111", "1.2.0", notes="旧版"),
            _release_record(102, "SN222", "1.3.0", notes="新版说明"),
        ]
        self.items = [
            {
                "id": record["id"],
                "title": record["title"],
                "sn": record["uid"],
                "keywords": ["bandscope-release"],
                "datetime_modify": "2026-10-01 10:00",
            }
            for record in self.records
        ]
        self.require_refresh = False
        self.download_content = INSTALLER_CPU

    def opener(self, request, timeout):
        del timeout
        url = request.full_url
        path = urlparse(url).path
        body = (request.data or b"").decode("utf-8")
        self.calls.append((path, dict(parse_qs(body)) if body else {}, dict(request.header_items())))
        if path == "/tokens":
            form = dict(parse_qs(body))
            if form.get("password", [""])[0] != self.password:
                return FakeResponse(json.dumps({"code": 1, "msg": "用户名或密码错误"}).encode(), url)
            return FakeResponse(
                json.dumps(
                    {
                        "code": 0,
                        "access": {"token": ACCESS_TOKEN, "expiredAt": 0},
                        "refresh": {"token": REFRESH_TOKEN, "expiredAt": 0},
                    }
                ).encode(),
                url,
            )
        if path == "/tokens/tokens_refresh":
            return FakeResponse(
                json.dumps(
                    {
                        "code": 0,
                        "access": {"token": ACCESS_TOKEN_2, "expiredAt": 0},
                        "refresh": {"token": "refresh-token-2", "expiredAt": 0},
                    }
                ).encode(),
                url,
            )
        if path == "/eln_api/elns":
            return FakeResponse(json.dumps({"code": 0, "my": [NOTEBOOK], "share": []}).encode(), url)
        if path == "/eln_api/items":
            return FakeResponse(json.dumps({"code": 0, "items": self.items}).encode(), url)
        if path == "/eln_api/export":
            if self.require_refresh and request.headers.get("Authorization") == f"Bearer {ACCESS_TOKEN}":
                self.require_refresh = False
                return FakeResponse(json.dumps({"code": "refresh"}).encode(), url)
            return FakeResponse(json.dumps({"code": 0, "dataset": self.records}).encode(), url)
        if path == "/eln_url/eln":
            return FakeResponse(self.download_content, url)
        raise AssertionError(f"未预期的请求：{url}")


def _client(server, **kwargs):
    return MatelabReleaseClient(lambda: CREDENTIALS, opener=server.opener, **kwargs)


class MatelabAuthTests(unittest.TestCase):
    def test_login_failure_raises_auth_error(self):
        server = FakeMatelabServer()
        client = _client(server)
        client_credentials = MatelabCredentials(username="zhangsan", password="wrong")
        client = MatelabReleaseClient(lambda: client_credentials, opener=server.opener)
        with self.assertRaises(MatelabAuthError):
            client.fetch_latest_release()

    def test_missing_credentials_raise_auth_error(self):
        server = FakeMatelabServer()
        client = MatelabReleaseClient(lambda: None, opener=server.opener)
        with self.assertRaises(MatelabAuthError):
            client.check_for_update("1.0.0", "CPU")
        self.assertEqual(server.calls, [])


class MatelabFetchTests(unittest.TestCase):
    def test_check_picks_highest_version_and_matches_flavor(self):
        server = FakeMatelabServer()
        result = _client(server).check_for_update("1.1.1", "CPU")
        self.assertTrue(result.update_available)
        self.assertEqual(str(result.release.version), "1.3.0")
        self.assertEqual(result.release.notes, "新版说明")
        self.assertEqual(result.installer.name, installer_asset_name("1.3.0", "CPU"))
        self.assertEqual(result.installer.sha256, INSTALLER_CPU_DIGEST)
        self.assertIn("eln_url/eln", result.installer.download_url)
        self.assertIn(f"token={ACCESS_TOKEN}", result.installer.download_url)

    def test_no_new_version_returns_no_installer(self):
        server = FakeMatelabServer()
        result = _client(server).check_for_update("9.9.9", "CPU")
        self.assertFalse(result.update_available)
        self.assertIsNone(result.installer)

    def test_missing_flavor_raises_asset_not_found(self):
        server = FakeMatelabServer()
        with self.assertRaises(ReleaseAssetNotFound):
            _client(server).check_for_update("1.1.1", "NVIDIA")

    def test_token_expiry_triggers_refresh_and_retry(self):
        server = FakeMatelabServer()
        server.require_refresh = True
        result = _client(server).check_for_update("1.1.1", "CPU")
        self.assertTrue(result.update_available)
        paths = [path for path, _form, _headers in server.calls]
        self.assertIn("/tokens/tokens_refresh", paths)
        # 重试后组装出的下载地址应使用刷新后的 token。
        self.assertIn(f"token={ACCESS_TOKEN_2}", result.installer.download_url)

    def test_notebook_not_found_is_user_presentable(self):
        server = FakeMatelabServer()
        client = _client(server, notebook="不存在的记录本")
        with self.assertRaises(UpdateError) as ctx:
            client.fetch_latest_release()
        self.assertIn("不存在的记录本", str(ctx.exception))

    def test_records_without_version_are_rejected(self):
        server = FakeMatelabServer()
        for record in server.records:
            record["data"] = [m for m in record["data"] if m["name"] != "manifest"]
            record["title"] = "无版本记录"
        with self.assertRaises(ReleaseFormatError):
            _client(server).fetch_latest_release()


class MatelabDownloadTests(unittest.TestCase):
    def test_download_verifies_size_and_digest(self):
        server = FakeMatelabServer()
        client = _client(server)
        result = client.check_for_update("1.1.1", "CPU")
        with tempfile.TemporaryDirectory() as directory:
            path, digest = client.download_installer(result.installer, directory)
            self.assertEqual(path.read_bytes(), INSTALLER_CPU)
            self.assertEqual(digest, INSTALLER_CPU_DIGEST)
        download_calls = [c for c in server.calls if c[0] == "/eln_url/eln"]
        self.assertEqual(len(download_calls), 1)

    def test_download_rejects_untrusted_group_server(self):
        server = FakeMatelabServer()
        evil_notebook = dict(NOTEBOOK, server="https://evil.example.com")
        server_records = [_release_record(201, "SN333", "1.4.0")]
        server.records = server_records
        server.items = [
            {
                "id": 201,
                "title": "BandScope v1.4.0",
                "sn": "SN333",
                "keywords": ["bandscope-release"],
            }
        ]

        original_elns = server.opener

        def opener(request, timeout):
            if urlparse(request.full_url).path == "/eln_api/elns":
                return FakeResponse(
                    json.dumps({"code": 0, "my": [evil_notebook], "share": []}).encode(),
                    request.full_url,
                )
            return original_elns(request, timeout)

        client = MatelabReleaseClient(lambda: CREDENTIALS, opener=opener)
        result = client.check_for_update("1.1.1", "CPU")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(UntrustedHostError):
                client.download_installer(result.installer, directory)

    def test_download_allows_configured_group_server(self):
        server = FakeMatelabServer()
        group_notebook = dict(NOTEBOOK, server="https://group.eln.local:9000")
        server.records = [_release_record(201, "SN333", "1.4.0")]
        server.items = [
            {
                "id": 201,
                "title": "BandScope v1.4.0",
                "sn": "SN333",
                "keywords": ["bandscope-release"],
            }
        ]
        original_elns = server.opener

        def opener(request, timeout):
            url = request.full_url
            path = urlparse(url).path
            if path == "/eln_api/elns":
                return FakeResponse(
                    json.dumps({"code": 0, "my": [group_notebook], "share": []}).encode(), url
                )
            if "group.eln.local" in (urlparse(url).hostname or ""):
                return FakeResponse(INSTALLER_CPU, url)
            return original_elns(request, timeout)

        client = MatelabReleaseClient(
            lambda: CREDENTIALS,
            opener=opener,
            extra_download_hosts=("group.eln.local",),
        )
        result = client.check_for_update("1.1.1", "CPU")
        with tempfile.TemporaryDirectory() as directory:
            path, digest = client.download_installer(result.installer, directory)
            self.assertEqual(path.read_bytes(), INSTALLER_CPU)
            self.assertEqual(digest, INSTALLER_CPU_DIGEST)

    def test_download_without_prior_check_is_rejected(self):
        server = FakeMatelabServer()
        client = _client(server)
        result = _client(FakeMatelabServer()).check_for_update("1.1.1", "CPU")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(UpdateError):
                client.download_installer(result.installer, directory)


class MatelabUploadTests(unittest.TestCase):
    def _session(self, server):
        from bandscope.updates.matelab_source import MatelabSession

        return MatelabSession(CREDENTIALS, "https://eln.iphy.ac.cn:61262", timeout=5, opener=server.opener)

    def test_chunked_upload_sends_ordered_slices_with_final_hash(self):
        from bandscope.updates.matelab_source import upload_file

        content = b"0123456789ABCDEF"  # 16 字节
        server = FakeMatelabServer()
        uploads = []

        original_opener = server.opener

        def opener(request, timeout):
            if urlparse(request.full_url).path == "/eln_api/upload":
                uploads.append(request.data)
                return FakeResponse(
                    json.dumps({"code": 0, "id": 1, "size": len(content), "hash": ""}).encode(),
                    request.full_url,
                )
            return original_opener(request, timeout)

        server.opener = opener
        session = self._session(server)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.bin"
            path.write_bytes(content)
            progress = []
            upload_file(session, path, name="bs-test", eln="BandScope发布", chunk_size=6, progress=lambda s, t: progress.append((s, t)))

        self.assertEqual(len(uploads), 3)  # 6+6+4
        uids = {self._field(body, "uid") for body in uploads}
        self.assertEqual(len(uids), 1)  # 同一文件碎片共用一个 uid
        self.assertEqual([self._field(body, "last") for body in uploads], ["0", "0", "1"])
        self.assertEqual(self._field(uploads[2], "hash"), hashlib.sha256(content).hexdigest())
        self.assertEqual(self._field(uploads[0], "eln"), "BandScope发布")
        self.assertEqual(progress[-1], (len(content), len(content)))

    def test_chunked_upload_exact_multiple_returns_payload(self):
        from bandscope.updates.matelab_source import upload_file

        content = b"0123456789AB"  # 12 字节 = 2 个 6 字节整片
        server = FakeMatelabServer()
        uploads = []

        original_opener = server.opener

        def opener(request, timeout):
            if urlparse(request.full_url).path == "/eln_api/upload":
                uploads.append(request.data)
                return FakeResponse(
                    json.dumps({"code": 0, "id": 7, "size": len(content), "hash": "x"}).encode(),
                    request.full_url,
                )
            return original_opener(request, timeout)

        server.opener = opener
        session = self._session(server)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.bin"
            path.write_bytes(content)
            result = upload_file(session, path, name="bs-test", chunk_size=6)

        self.assertIsNotNone(result)  # 整倍数时末片也必须返回服务器反馈
        self.assertEqual(result.get("id"), 7)
        self.assertEqual(len(uploads), 2)
        self.assertEqual([self._field(body, "last") for body in uploads], ["0", "1"])
        self.assertEqual(self._field(uploads[1], "hash"), hashlib.sha256(content).hexdigest())

    def test_upload_retries_chunk_after_token_refresh(self):
        from bandscope.updates.matelab_source import upload_file

        content = b"abc"
        server = FakeMatelabServer()
        state = {"uploads": 0}

        original_opener = server.opener

        def opener(request, timeout):
            if urlparse(request.full_url).path == "/eln_api/upload":
                state["uploads"] += 1
                if state["uploads"] == 1:
                    return FakeResponse(json.dumps({"code": "refresh"}).encode(), request.full_url)
                return FakeResponse(json.dumps({"code": 0, "id": 1}).encode(), request.full_url)
            return original_opener(request, timeout)

        server.opener = opener
        session = self._session(server)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.bin"
            path.write_bytes(content)
            upload_file(session, path, name="bs-test")

        self.assertEqual(state["uploads"], 2)
        paths = [path for path, _form, _headers in server.calls]
        self.assertIn("/tokens/tokens_refresh", paths)

    @staticmethod
    def _field(body: bytes, name: str) -> str:
        import re as _re

        match = _re.search(
            rb'name="' + name.encode() + rb'"\r\n\r\n([^\r]+)', body
        )
        return match.group(1).decode() if match else ""


if __name__ == "__main__":
    unittest.main()
