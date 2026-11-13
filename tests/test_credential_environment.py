"""The shared service principal may come from environment variables instead of each tenant's settings."""

import pytest

from tests.test_authentication import SP_CONFIG, clients, upload  # noqa: F401  (fixture re-export)

VARIABLES = {
    "AZURE_TENANT_ID": "directory-from-env",
    "AZURE_CLIENT_ID": "application-from-env",
    "AZURE_CLIENT_SECRET": "secret-from-env",
}
SETTINGS = ("azure_tenant_id", "azure_client_id", "azure_client_secret")
WITHOUT_CREDENTIALS = {key: value for key, value in SP_CONFIG.items() if key not in SETTINGS}


@pytest.fixture(autouse=True)
def no_credential_environment(monkeypatch):
    for variable in VARIABLES:
        monkeypatch.delenv(variable, raising=False)


def test_service_principal_from_environment_when_settings_have_none(tmp_path, clients, monkeypatch):  # noqa: F811
    for variable, value in VARIABLES.items():
        monkeypatch.setenv(variable, value)
    upload(tmp_path, WITHOUT_CREDENTIALS)
    clients.secret_factory.assert_called_once_with(
        tenant_id="directory-from-env", client_id="application-from-env", client_secret="secret-from-env",
    )


def test_settings_win_over_environment(tmp_path, clients, monkeypatch):  # noqa: F811
    for variable, value in VARIABLES.items():
        monkeypatch.setenv(variable, value)
    upload(tmp_path, SP_CONFIG)
    clients.secret_factory.assert_called_once_with(
        tenant_id="directory-id", client_id="application-id", client_secret="synthetic-secret",
    )


def test_environment_fills_only_the_missing_setting(tmp_path, clients, monkeypatch):  # noqa: F811
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "secret-from-env")
    config = {key: value for key, value in SP_CONFIG.items() if key != "azure_client_secret"}
    upload(tmp_path, config)
    clients.secret_factory.assert_called_once_with(
        tenant_id="directory-id", client_id="application-id", client_secret="secret-from-env",
    )


@pytest.mark.parametrize("missing", ["AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"])
def test_missing_credential_names_the_setting_and_the_variable(missing, tmp_path, clients, monkeypatch):  # noqa: F811
    for variable, value in VARIABLES.items():
        if variable != missing:
            monkeypatch.setenv(variable, value)
    with pytest.raises(ValueError, match=missing) as error:
        upload(tmp_path, WITHOUT_CREDENTIALS)
    assert missing.lower() in str(error.value)
    assert "secret-from-env" not in str(error.value)
    clients.factory.from_connection_string.assert_not_called()
    clients.sas.assert_not_called()


def test_account_url_still_has_to_be_configured(tmp_path, clients, monkeypatch):  # noqa: F811
    for variable, value in VARIABLES.items():
        monkeypatch.setenv(variable, value)
    with pytest.raises(ValueError, match="account_url"):
        upload(tmp_path, {key: value for key, value in WITHOUT_CREDENTIALS.items() if key != "account_url"})
