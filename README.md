# Azure Blob Storage target with service-principal authentication

Adds an opt-in authentication mode to the existing Azure output target.

```json
{
  "auth_method": "service_principal",
  "account_url": "https://example.blob.core.windows.net",
  "azure_tenant_id": "<directory-id>",
  "azure_client_id": "<application-id>",
  "azure_client_secret": "<protected-secret>",
  "container": "partner-output",
  "path_prefix": "history/connection-1/gokasa/202611/job-1",
  "input_path": "<local-export-directory>",
  "overwrite": false
}
```

The Azure SDK obtains tokens using `ClientSecretCredential`. This mode ignores old connection
strings/account keys and never falls back if authentication fails. The processor identity must
have write access to the output container. Keep credentials in protected target configuration.
Register `azure_client_secret` as a secret in the host's connector configuration before rollout.

Omitting `auth_method` (or using `legacy`) preserves the existing connection-string/account-key
behavior. This patch does not change any shared target settings. Clients and credentials close
after both successful and failed exports. Blob paths use `/` on Windows and Linux; nested paths,
file contents and the overwrite option are preserved.

Service-principal uploads use requests of at most 4 MiB for file data. Larger histories are
staged as blocks and committed once complete, avoiding a single long SSL write for the entire file.

Run local tests with `python -m pytest tests`. They mock Azure clients and never upload to Azure.
Before deployment, choose a tested/pinned package source and verify one isolated export with
real role assignments. No Azure resource deployment is needed to review or run these tests.
