"""Local auth regression tests: never authenticate or upload to Azure."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import target_blob_storage as target

SP_CONFIG = {
    "auth_method": "service_principal",
    "account_url": "https://example.blob.core.windows.net",
    "azure_tenant_id": "directory-id",
    "azure_client_id": "application-id",
    "azure_client_secret": "synthetic-secret",
}


@pytest.fixture
def clients(monkeypatch):
    credential = Mock()
    client = Mock()
    factory = Mock(return_value=client)
    factory.from_connection_string.return_value = client
    monkeypatch.setattr(target, "BlobServiceClient", factory)
    secret_factory = Mock(return_value=credential)
    monkeypatch.setattr(target, "ClientSecretCredential", secret_factory, raising=False)
    sas = Mock(return_value="synthetic-sas")
    monkeypatch.setattr(target, "generate_account_sas", sas)
    return SimpleNamespace(credential=credential, factory=factory, client=client,
                           secret_factory=secret_factory, sas=sas)


def upload(tmp_path, config):
    return target.upload(SimpleNamespace(config={
        "container": "partner-output", "path_prefix": "history/connection/job",
        "input_path": str(tmp_path), **config,
    }))


def test_service_principal_uploads_exact_file_bytes_and_preserves_output_path(tmp_path, clients):
    content = b'{"transactionId":"a","payments":[]}\n'
    (tmp_path / "transactions.jsonl").write_bytes(content)
    captured = []
    clients.client.get_blob_client.return_value.upload_blob.side_effect = (
        lambda data, **kwargs: captured.append((data.read(), kwargs))
    )

    upload(tmp_path, {**SP_CONFIG, "connect_string": "ignored-legacy-secret", "account_key": "ignored-key"})

    clients.secret_factory.assert_called_once_with(
        tenant_id="directory-id", client_id="application-id", client_secret="synthetic-secret",
    )
    clients.factory.assert_called_once_with(
        account_url=SP_CONFIG["account_url"], credential=clients.credential,
        max_single_put_size=4 * 1024 * 1024, max_block_size=4 * 1024 * 1024,
    )
    clients.client.get_blob_client.assert_called_once_with(
        container="partner-output", blob="history/connection/job/transactions.jsonl",
    )
    assert captured == [(content, {"overwrite": False})]
    clients.factory.from_connection_string.assert_not_called()
    clients.sas.assert_not_called()
    clients.client.close.assert_called_once()
    clients.credential.close.assert_called_once()


@pytest.mark.parametrize("field", ["azure_tenant_id", "azure_client_id", "azure_client_secret", "account_url"])
def test_missing_sp_field_fails_without_falling_back(tmp_path, clients, field):
    config = {key: value for key, value in SP_CONFIG.items() if key != field}
    with pytest.raises(ValueError, match=field) as error:
        upload(tmp_path, {**config, "connect_string": "old-connection-string"})
    assert "synthetic-secret" not in str(error.value)
    clients.factory.from_connection_string.assert_not_called()
    clients.sas.assert_not_called()


def test_existing_connection_string_still_works(tmp_path, clients):
    upload(tmp_path, {"connect_string": "existing-connection-string"})
    clients.factory.from_connection_string.assert_called_once_with("existing-connection-string")
    clients.secret_factory.assert_not_called()
    clients.sas.assert_not_called()


def test_existing_account_key_still_works(tmp_path, clients):
    upload(tmp_path, {"account_name": "example", "account_key": "synthetic-key"})
    assert clients.sas.call_args.kwargs["account_key"] == "synthetic-key"
    clients.factory.assert_called_once_with(
        account_url=SP_CONFIG["account_url"], credential="synthetic-sas",
    )
    clients.secret_factory.assert_not_called()


def test_unknown_auth_mode_does_not_fall_back(tmp_path, clients):
    with pytest.raises(ValueError, match="auth_method"):
        upload(tmp_path, {"auth_method": "typo", "connect_string": "old"})
    clients.factory.from_connection_string.assert_not_called()


def test_upload_failure_propagates_and_releases_resources(tmp_path, clients):
    (tmp_path / "transactions.jsonl").write_text("{}\n")
    clients.client.get_blob_client.return_value.upload_blob.side_effect = RuntimeError("upload failed")
    with pytest.raises(RuntimeError, match="upload failed"):
        upload(tmp_path, SP_CONFIG)
    clients.client.close.assert_called_once()
    clients.credential.close.assert_called_once()


def test_nested_export_keeps_forward_slashes_and_explicit_overwrite(tmp_path, clients):
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "transactions.jsonl").write_bytes(b"{}\n")
    upload(tmp_path, {**SP_CONFIG, "path_prefix": "history/job/", "overwrite": True})
    clients.client.get_blob_client.assert_called_once_with(
        container="partner-output", blob="history/job/nested/transactions.jsonl",
    )
    assert clients.client.get_blob_client.return_value.upload_blob.call_args.kwargs == {"overwrite": True}


def test_client_creation_failure_closes_credential_without_fallback(tmp_path, clients):
    clients.factory.side_effect = RuntimeError("client creation failed")
    with pytest.raises(RuntimeError, match="client creation failed"):
        upload(tmp_path, {**SP_CONFIG, "connect_string": "ignored-legacy-secret"})
    clients.credential.close.assert_called_once()
    clients.factory.from_connection_string.assert_not_called()
