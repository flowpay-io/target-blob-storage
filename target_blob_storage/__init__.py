#!/usr/bin/env python3
import os
import json
import argparse
import logging
import posixpath

from contextlib import contextmanager, ExitStack
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from azure.identity import ClientSecretCredential
from azure.storage.blob import BlobServiceClient, generate_account_sas, ResourceTypes, AccountSasPermissions

logger = logging.getLogger("target-blob-storage")
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

def load_json(path):
    with open(path) as f:
        return json.load(f)


def parse_args():
    '''Parse standard command-line args.
    Parses the command-line arguments mentioned in the SPEC and the
    BEST_PRACTICES documents:
    -c,--config     Config file
    -s,--state      State file
    -d,--discover   Run in discover mode
    -p,--properties Properties file: DEPRECATED, please use --catalog instead
    --catalog       Catalog file
    Returns the parsed args object from argparse. For each argument that
    point to JSON files (config, state, properties), we will automatically
    load and parse the JSON file.
    '''
    parser = argparse.ArgumentParser()

    parser.add_argument(
        '-c', '--config',
        help='Config file',
        required=True)

    args = parser.parse_args()
    if args.config:
        setattr(args, 'config_path', args.config)
        args.config = load_json(args.config)

    return args


@contextmanager
def storage_client(config):
    """Select authentication explicitly and close clients even when an upload fails."""
    with ExitStack() as resources:
        method = config.get("auth_method", "legacy")
        if method == "service_principal":
            for field in ("account_url", "azure_tenant_id", "azure_client_id", "azure_client_secret"):
                if not isinstance(config.get(field), str) or not config[field].strip():
                    raise ValueError(f"{field} is required for service_principal authentication")
            endpoint = urlsplit(config["account_url"])
            if (endpoint.scheme != "https" or not endpoint.hostname or endpoint.username or endpoint.password
                    or endpoint.query or endpoint.fragment or endpoint.path not in ("", "/")):
                raise ValueError("account_url must be an HTTPS account endpoint without credentials, query or path")
            credential = ClientSecretCredential(
                tenant_id=config["azure_tenant_id"],
                client_id=config["azure_client_id"],
                client_secret=config["azure_client_secret"],
            )
            resources.callback(credential.close)
            # Bound request bodies so a full history does not require one long SSL write.
            client = BlobServiceClient(
                account_url=config["account_url"], credential=credential,
                max_single_put_size=4 * 1024 * 1024, max_block_size=4 * 1024 * 1024,
            )
        elif method == "legacy":
            if config.get("connect_string"):
                client = BlobServiceClient.from_connection_string(config["connect_string"])
            else:
                account_name = config.get("account_name")
                sas_token = generate_account_sas(
                    account_name=account_name,
                    account_key=config.get("account_key"),
                    resource_types=ResourceTypes(object=True),
                    permission=AccountSasPermissions(read=True, add=True, create=True, write=True, delete=True, list=True),
                    expiry=datetime.utcnow() + timedelta(hours=1),
                )
                client = BlobServiceClient(
                    account_url=f"https://{account_name}.blob.core.windows.net", credential=sas_token,
                )
        else:
            raise ValueError("auth_method must be service_principal or legacy")
        resources.callback(client.close)
        yield client


def upload(args):
    logger.info("Starting data export...")
    config = args.config

    container_name = config.get('container')
    target_path = config.get('path_prefix')
    local_path = config.get('input_path')
    overwrite = config.get("overwrite", False)

    try:
        with storage_client(config) as blob_service_client:
            for root, dirs, files in os.walk(local_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    relative_path = os.path.relpath(file_path, local_path).replace(os.sep, "/")
                    remote_file_path = posixpath.join(target_path.rstrip("/"), relative_path)
                    blob_client = blob_service_client.get_blob_client(container=container_name, blob=remote_file_path)

                    try:
                        with open(file_path, "rb") as data:
                            logger.info(f"Uploading: {container_name}:{remote_file_path}")
                            blob_client.upload_blob(data, overwrite=overwrite)
                    except Exception as e:
                        logger.exception(f"Failed to upload {file_path} to {remote_file_path}: {e}")
                        if hasattr(e, 'reason'):
                            # Credential errors have no HTTP response, so reason can be None.
                            raise Exception(e.reason or str(e))
                        else:
                            raise e

    except Exception as e:
        logger.exception(f"An error occurred during the export process: {e}")
        raise e

    logger.info("Data export completed.")

def main():
    # Parse command line arguments
    args = parse_args()

    # Upload the data
    upload(args)


if __name__ == "__main__":
    main()
