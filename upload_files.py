#!/usr/bin/env python3

import argparse
import os
import sys
import requests
from pathlib import Path
from typing import Any
from dotenv import load_dotenv


class OnedataClient:
    """
    Provides methods for communicating with the Onedata API,
    including remote directory creation and file uploads.
    """
    def __init__(self, provider_host: str, token: str) -> None:
        """ Configures the API address and an authenticated HTTP session. """
        self.base_url = (
            f"https://{provider_host}/api/v3/oneprovider"
        )

        self.session = requests.Session()
        self.session.headers.update({
            "X-Auth-Token": token,
        })

    @staticmethod
    def _response_details(response: requests.Response) -> Any:
        """ Returns the API response as JSON or plain text. """
        try:
            return response.json()
        except ValueError:
            return response.text

    def create_directory(self, parent_id: str, name: str) -> str:
        """ Creates a remote directory and returns its identifier. """
        url = f"{self.base_url}/data/{parent_id}/children"

        response = self.session.post(
            url,
            params={
                "name": name,
                "type": "DIR",
            },
            timeout=(10, 60),
        )

        if response.status_code != 201:
            raise RuntimeError(
                f"Failed to create directory {name!r}. "
                f"HTTP {response.status_code}: "
                f"{self._response_details(response)}"
            )

        file_id = response.json().get("fileId")
        if not file_id:
            raise RuntimeError(
                f"The API did not return a fileId for directory {name!r}"
            )

        return file_id

    def upload_file(self, parent_id: str, file_path: Path) -> str:
        """ Uploads a file to the specified directory and returns its identifier. """
        url = f"{self.base_url}/data/{parent_id}/children"

        with file_path.open("rb") as file_handle:
            response = self.session.post(
                url,
                params={"name": file_path.name},
                headers={"Content-Type": "application/octet-stream"},
                data=file_handle,
                timeout=(10, 300),
            )

        if response.status_code != 201:
            raise RuntimeError(
                f"Failed to upload file {file_path}. "
                f"HTTP {response.status_code}: "
                f"{self._response_details(response)}"
            )

        file_id = response.json().get("fileId")
        if not file_id:
            raise RuntimeError(
                f"The API did not return a fileId for file {file_path}"
            )

        return file_id


def parse_arguments() -> argparse.Namespace:
    """ Parses command-line arguments. """
    parser = argparse.ArgumentParser(
        description="Uploads a local directory to Onedata."
    )
    parser.add_argument(
        "--source-dir",
        required=True,
        type=Path,
        help="Path to the local source directory.",
    )
    return parser.parse_args()


def required_environment_variable(name: str) -> str:
    """ Returns a required environment variable or raises an error. """
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Required environment variable is missing: {name}"
        )
    return value


def upload_directory_tree(
    client: OnedataClient,
    source_dir: Path,
    space_dir_id: str,
) -> tuple[int, int]:
    """
    Recreates the local directory tree in the Onedata space.
    """
    print(f"Creating the root directory: {source_dir.name}/")

    remote_root_id = client.create_directory(
        space_dir_id,
        source_dir.name,
    )

    remote_directory_ids: dict[Path, str] = {
        source_dir: remote_root_id,
    }

    uploaded_directories = 1
    uploaded_files = 0

    for current_path_string, directory_names, file_names in os.walk(
        source_dir
    ):
        directory_names.sort()
        file_names.sort()

        current_path = Path(current_path_string)
        current_remote_id = remote_directory_ids[current_path]

        for directory_name in directory_names:
            local_directory = current_path / directory_name

            if local_directory.is_symlink():
                raise RuntimeError(
                    f"Symbolic links are not supported: "
                    f"{local_directory}"
                )

            relative_path = local_directory.relative_to(source_dir)
            print(f"[DIR ] {relative_path}/")

            remote_directory_id = client.create_directory(
                current_remote_id,
                directory_name,
            )

            remote_directory_ids[local_directory] = remote_directory_id
            uploaded_directories += 1

        for file_name in file_names:
            local_file = current_path / file_name

            if local_file.is_symlink():
                raise RuntimeError(
                    f"Symbolic links are not supported: "
                    f"{local_file}"
                )

            relative_path = local_file.relative_to(source_dir)
            print(f"[FILE] {relative_path}")

            client.upload_file(current_remote_id, local_file)
            uploaded_files += 1

    return uploaded_directories, uploaded_files


def main() -> int:
    """
    Loads the configuration, validates input, and runs the upload process.
    """
    load_dotenv()

    args = parse_arguments()
    source_dir = args.source_dir.expanduser().resolve()

    if not source_dir.exists():
        print(
            f"Error: directory does not exist: {source_dir}",
            file=sys.stderr,
        )
        return 1

    if not source_dir.is_dir():
        print(
            f"Error: the specified path is not a directory: {source_dir}",
            file=sys.stderr,
        )
        return 1

    try:
        provider_host = required_environment_variable(
            "ONEDATA_PROVIDER_HOST"
        )
        token = required_environment_variable("ONEDATA_TOKEN")
        space_dir_id = required_environment_variable(
            "ONEDATA_SPACE_DIR_ID"
        )

        client = OnedataClient(provider_host, token)

        directories_count, files_count = upload_directory_tree(
            client,
            source_dir,
            space_dir_id,
        )

    except requests.RequestException as error:
        print(
            f"Failed to connect to Onedata: {error}",
            file=sys.stderr,
        )
        return 1
    except RuntimeError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print()
    print("Upload completed successfully.")
    print(f"Directories created: {directories_count}")
    print(f"Files uploaded: {files_count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
