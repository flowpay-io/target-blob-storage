"""Large files must survive a transport that cannot send one large request body."""

import hashlib
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree

from azure.core.credentials import AccessToken
from azure.core.exceptions import ServiceRequestError
from azure.core.pipeline.transport import HttpResponse, HttpTransport
from azure.storage.blob import BlobServiceClient

import target_blob_storage as target

LIMIT = 4 * 1024 * 1024


class Credential:
    def get_token(self, *scopes, **kwargs):
        return AccessToken("synthetic-token", int(time.time()) + 3600)

    def close(self):
        pass


class UploadResponse(HttpResponse):
    def __init__(self, request):
        super().__init__(request, None)
        self.status_code = 201
        self.reason = "Created"
        self.headers = {"etag": '"synthetic-etag"', "content-length": "0"}

    def body(self):
        return b""


class LimitedTransport(HttpTransport):
    def __init__(self):
        self.blocks = {}
        self.result = None
        self.body_sizes = []

    def open(self):
        pass

    def close(self):
        pass

    def __exit__(self, *args):
        self.close()

    def send(self, request, **kwargs):
        body = request.body.read() if hasattr(request.body, "read") else request.body
        if isinstance(body, str):
            body = body.encode()
        body = body or b""
        self.body_sizes.append(len(body))
        if len(body) > LIMIT:
            raise ServiceRequestError("Simulated SSL write timeout for an oversized request")
        assert request.headers["Authorization"] == "Bearer synthetic-token"
        query = parse_qs(urlsplit(request.url).query)
        if query.get("comp") == ["block"]:
            self.blocks[query["blockid"][0]] = body
        elif query.get("comp") == ["blocklist"]:
            block_ids = [node.text for node in ElementTree.fromstring(body)]
            self.result = b"".join(self.blocks[block_id] for block_id in block_ids)
        else:
            self.result = body
        return UploadResponse(request)


def test_large_export_is_uploaded_in_bounded_blocks_without_changing_bytes(tmp_path, monkeypatch):
    content = b"0123456789abcdef" * (6 * 1024 * 1024 // 16)
    (tmp_path / "transactions.jsonl").write_bytes(content)
    transport = LimitedTransport()
    monkeypatch.setattr(target, "ClientSecretCredential", lambda **kwargs: Credential())
    monkeypatch.setattr(target, "BlobServiceClient", lambda **kwargs: BlobServiceClient(
        **kwargs, transport=transport, retry_total=0,
    ))
    target.upload(SimpleNamespace(config={
        "auth_method": "service_principal", "account_url": "https://example.blob.core.windows.net",
        "azure_tenant_id": "directory", "azure_client_id": "application", "azure_client_secret": "synthetic-secret",
        "container": "partner-output", "path_prefix": "history/test/job", "input_path": str(tmp_path),
    }))
    assert len(transport.blocks) >= 2
    assert all(size <= LIMIT for size in transport.body_sizes)
    assert hashlib.sha256(transport.result).digest() == hashlib.sha256(content).digest()
