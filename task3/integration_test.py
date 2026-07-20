#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import os
import secrets
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

import time
import requests
import urllib3
from PIL import Image, UnidentifiedImageError
from urllib3.exceptions import InsecureRequestWarning


ONEZONE_IMAGE = "onedata/onezone:25.1"
ONEPROVIDER_IMAGE = "onedata/oneprovider:25.1"

ONEZONE_STARTUP_TIMEOUT_SECONDS = 20 * 60
ONEPROVIDER_STARTUP_TIMEOUT_SECONDS = 20 * 60

@dataclass(frozen=True)
class RemoteFile:
    """
    Represents a regular file stored in Onedata together with its
    JSON metadata information.
    """

    file_id: str
    path: PurePosixPath
    has_json_metadata: bool
    json_metadata: Any


class OnedataClient:
    """
    Provides REST API operations used to verify the integration test.
    """

    def __init__(self, provider_host: str, token: str) -> None:
        """ Configures the API address and authenticated HTTP session. """
        self.base_url = (
            f"https://{provider_host}/api/v3/oneprovider"
        )

        self.session = requests.Session()
        self.session.headers.update({
            "X-Auth-Token": token,
        })

        # Disabling certificate verification for the local
        # Demo Mode which uses self-signed certificates.
        self.session.verify = False

    def get_spaces(self) -> list[dict[str, Any]]:
        """
        Retrieves the list of spaces available to the current user.
        """
        response = self.session.get(
            f"{self.base_url}/spaces",
            timeout=(10, 60),
        )
        response.raise_for_status()

        payload = response.json()

        if not isinstance(payload, list):
            raise RuntimeError(
                "The /spaces endpoint returned an invalid response."
            )

        return payload

    def iter_children(
        self,
        directory_id: str,
    ) -> Iterator[dict[str, Any]]:
        """ Iterates over all direct children of a remote directory. """
        url = (
            f"{self.base_url}/data/"
            f"{directory_id}/children"
        )

        page_token: str | None = None

        while True:
            request_body: dict[str, Any] = {
                "attributes": [
                    "fileId",
                    "name",
                    "type",
                    "hasJsonMetadata",
                    "jsonMetadata",
                ],
                "limit": 100,
            }

            if page_token is not None:
                request_body["token"] = page_token

            response = self.session.get(
                url,
                json=request_body,
                timeout=(10, 60),
            )
            response.raise_for_status()

            payload = response.json()
            children = payload.get("children")

            if not isinstance(children, list):
                raise RuntimeError(
                    "The API returned an invalid list "
                    "of directory entries."
                )

            yield from children

            if payload.get("isLast") is True:
                return

            page_token = payload.get("nextPageToken")

            if (
                not isinstance(page_token, str)
                or not page_token
            ):
                raise RuntimeError(
                    "The API did not return nextPageToken "
                    "even though listing was not complete."
                )

    def iter_files(
        self,
        root_directory_id: str,
        root_name: str,
    ) -> Iterator[RemoteFile]:
        """ Traverses the directory tree and yields all regular files. """
        directories: list[
            tuple[str, PurePosixPath]
        ] = [
            (
                root_directory_id,
                PurePosixPath(root_name),
            )
        ]

        while directories:
            directory_id, directory_path = (
                directories.pop()
            )

            child_directories: list[
                tuple[str, PurePosixPath]
            ] = []

            children = sorted(
                self.iter_children(directory_id),
                key=lambda item: str(
                    item.get("name", "")
                ).casefold(),
            )

            for child in children:
                file_id = child.get("fileId")
                name = child.get("name")
                file_type = child.get("type")

                if not all(
                    isinstance(value, str)
                    for value in (
                        file_id,
                        name,
                        file_type,
                    )
                ):
                    raise RuntimeError(
                        "A directory entry does not contain "
                        "valid fileId, name, and type fields."
                    )

                path = directory_path / name

                if file_type == "DIR":
                    child_directories.append(
                        (file_id, path)
                    )

                elif file_type == "REG":
                    yield RemoteFile(
                        file_id=file_id,
                        path=path,
                        has_json_metadata=(
                            child.get("hasJsonMetadata")
                            is True
                        ),
                        json_metadata=child.get(
                            "jsonMetadata"
                        ),
                    )

                else:
                    print(
                        f"[ERROR] {path} — "
                        f"unsupported type: {file_type}"
                    )

            directories.extend(
                reversed(child_directories)
            )

    def download_file(self, file_id: str) -> bytes:
        """ Downloads the complete contents of a remote file."""
        response = self.session.get(
            (
                f"{self.base_url}/data/"
                f"{file_id}/content"
            ),
            timeout=(10, 300),
        )
        response.raise_for_status()

        return response.content


def parse_arguments() -> argparse.Namespace:
    """ Parses command-line arguments """
    parser = argparse.ArgumentParser(
        description=(
            "Runs an integration test for the upload_files.py"
            "and annotate_images.py scripts."
        )
    )

    parser.add_argument(
        "--source-dir",
        required=True,
        type=Path,
        help="Local parent directory containing the test files.",
    )

    return parser.parse_args()


def run_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    capture_output: bool = False,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    """ Executes a command and displays it before execution. """
    print(f"$ {shlex.join(command)}")

    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=env,
            check=True,
            text=True,
            capture_output=capture_output,
            timeout=timeout,
        )

    except subprocess.CalledProcessError as error:
        if error.stdout:
            print(
                f"\nCOMMAND STDOUT:\n{error.stdout}",
                file=sys.stderr,
            )

        if error.stderr:
            print(
                f"\nCOMMAND STDERR:\n{error.stderr}",
                file=sys.stderr,
            )

        raise


def get_container_ip(container_name: str) -> str:
    """ Retrieves the IP address assigned to a Docker container. """
    result = run_command(
        [
            "docker",
            "inspect",
            "--format",
            (
                "{{range .NetworkSettings.Networks}}"
                "{{.IPAddress}}"
                "{{end}}"
            ),
            container_name,
        ],
        capture_output=True,
    )

    ip_address = result.stdout.strip()

    if not ip_address:
        raise RuntimeError(
            "Failed to retrieve the IP address of "
            f"container {container_name}."
        )

    return ip_address


def remove_container(container_name: str) -> None:
    """ Forcefully removes a Docker container without raising errors. """
    subprocess.run(
        [
            "docker",
            "rm",
            "-f",
            container_name,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def print_container_logs(container_name: str) -> None:
    """ Prints the most recent logs produced by a Docker container. """
    result = subprocess.run(
        [
            "docker",
            "logs",
            "--tail",
            "150",
            container_name,
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    if not result.stdout and not result.stderr:
        return

    print(
        f"\n--- RECENT LOGS: "
        f"{container_name} ---"
    )

    if result.stdout:
        print(result.stdout)

    if result.stderr:
        print(result.stderr)


def content_is_image(content: bytes) -> bool:
    """
    Identifies an image based on its contents rather than its
    filename extension.
    """
    # Attempts to open and validate the supplied binary content.
    try:
        with Image.open(
            io.BytesIO(content)
        ) as image:
            image.verify()

        return True

    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
    ):
        return False


def find_demo_space(
    spaces: list[dict[str, Any]],
) -> dict[str, Any]:
    """ Finds and returns the space named demo-space. """
    for space in spaces:
        if space.get("name") == "demo-space":
            return space

    raise RuntimeError(
        "Oneprovider did not return a space "
        "named demo-space."
    )


def verify_space_files(
    client: OnedataClient,
    space_directory_id: str,
    space_name: str,
) -> tuple[bool, int]:
    """ Verifies JSON metadata for every file in the selected space. """
    all_files_ok = True
    checked_files = 0

    for remote_file in client.iter_files(
        root_directory_id=space_directory_id,
        root_name=space_name,
    ):
        checked_files += 1

        try:
            content = client.download_file(
                remote_file.file_id
            )

            if content_is_image(content):
                metadata = remote_file.json_metadata

                metadata_ok = (
                    remote_file.has_json_metadata
                    and isinstance(metadata, dict)
                    and "width" in metadata
                    and "height" in metadata
                )

                if metadata_ok:
                    print(
                        f"[OK] {remote_file.path} — "
                        "image has width and height metadata"
                    )
                else:
                    all_files_ok = False

                    print(
                        f"[ERROR] {remote_file.path} — "
                        "image does not have the required "
                        "JSON metadata"
                    )

            elif remote_file.has_json_metadata:
                all_files_ok = False

                print(
                    f"[ERROR] {remote_file.path} — "
                    "non-image file has JSON metadata "
                )

            else:
                print(
                    f"[OK] {remote_file.path} — "
                    "non-image file has no JSON metadata "
                )

        except (
            requests.RequestException,
            RuntimeError,
        ) as error:
            all_files_ok = False

            print(
                f"[ERROR] {remote_file.path} — "
                f"{error}"
            )

    if checked_files == 0:
        print(
            "[ERROR] No files were found in the space. "
        )
        return False, 0

    return all_files_ok, checked_files


def ensure_container_is_running(container_name: str) -> None:
    """ Verifies that a Docker container is currently running. """
    result = run_command(
        [
            "docker",
            "inspect",
            "--format",
            (
                "{{.State.Status}}|"
                "{{.State.ExitCode}}|"
                "{{.State.OOMKilled}}|"
                "{{.State.Error}}"
            ),
            container_name,
        ],
        capture_output=True,
    )

    status, exit_code, oom_killed, error = (
        result.stdout.strip().split("|", maxsplit=3)
    )

    if status != "running":
        print_container_logs(container_name)

        raise RuntimeError(
            f"Container {container_name} is not running: "
            f"status={status}, "
            f"exit_code={exit_code}, "
            f"oom_killed={oom_killed}, "
            f"error={error or 'brak'}"
        )


def main() -> int:
    """ Sets up the test environment and runs the integration test. """
    args = parse_arguments()

    source_dir = (
        args.source_dir
        .expanduser()
        .resolve()
    )

    project_dir = (
        Path(__file__)
        .resolve()
        .parent
    )

    upload_script = (
        project_dir / "upload_files.py"
    )

    annotate_script = (
        project_dir / "annotate_images.py"
    )

    if not source_dir.is_dir():
        print(
            f"Error: directory does not exist: "
            f"{source_dir}",
            file=sys.stderr,
        )
        return 2

    for script in (
        upload_script,
        annotate_script,
    ):
        if not script.is_file():
            print(
                f"Error: required script is missing: "
                f"{script}",
                file=sys.stderr,
            )
            return 2

    unique_suffix = secrets.token_hex(4)

    zone_container = (
        f"onedata-integration-oz-"
        f"{unique_suffix}"
    )

    provider_container = (
        f"onedata-integration-op-"
        f"{unique_suffix}"
    )

    test_failed_with_exception = False

    urllib3.disable_warnings(
        InsecureRequestWarning
    )

    try:
        run_command(
            ["docker", "info"],
            capture_output=True,
            timeout=30,
        )

        print("\n=== STARTING ONEZONE ===")

        run_command([
            "docker",
            "run",
            "-d",
            "--name",
            zone_container,
            ONEZONE_IMAGE,
            "demo",
        ])

        ensure_container_is_running(zone_container)

        print(
            "\nWaiting for Onezone to become fully ready..."
        )

        run_command(
            [
                "docker",
                "exec",
                zone_container,
                "await",
            ],
            timeout=ONEZONE_STARTUP_TIMEOUT_SECONDS,
        )

        ensure_container_is_running(zone_container)

        zone_ip = get_container_ip(zone_container)

        print("\n=== STARTING ONEPROVIDER ===")

        run_command([
            "docker",
            "run",
            "-d",
            "--name",
            provider_container,
            ONEPROVIDER_IMAGE,
            "demo",
            zone_ip,
        ])

        ensure_container_is_running(provider_container)

        print(
            "\nWaiting for Oneprovider to become fully ready..."
        )

        run_command(
            [
                "docker",
                "exec",
                provider_container,
                "await-demo",
            ],
            timeout=ONEPROVIDER_STARTUP_TIMEOUT_SECONDS,
        )


        print(
            "The environment reported readiness."
            "Waiting 30 seconds for stabilization..."
        )

        time.sleep(30)

        provider_ip = get_container_ip(
            provider_container
        )

        token_result = run_command(
            [
                "docker",
                "exec",
                provider_container,
                "demo-access-token",
            ],
            capture_output=True,
            timeout=60,
        )

        access_token = (
            token_result.stdout.strip()
        )

        if not access_token:
            raise RuntimeError(
                "The container did not return "
                "an access token."
            )

        client = OnedataClient(
            provider_host=provider_ip,
            token=access_token,
        )

        space = find_demo_space(
            client.get_spaces()
        )

        space_name = space.get("name")
        space_id = space.get("spaceId")
        space_directory_id = space.get("dirId")

        if not all(
            isinstance(value, str)
            for value in (
                space_name,
                space_id,
                space_directory_id,
            )
        ):
            raise RuntimeError(
                "The /spaces response does not contain "
                "valid name, spaceId, or dirId fields. "
            )

        child_environment = os.environ.copy()

        child_environment.update({
            "ONEDATA_PROVIDER_HOST": provider_ip,
            "ONEDATA_TOKEN": access_token,
            "ONEDATA_SPACE_ID": space_id,
            "ONEDATA_SPACE_DIR_ID": (
                space_directory_id
            ),
            "ONEDATA_SPACE_NAME": space_name,
            "ONEDATA_VERIFY_SSL": "false",
        })

        print("\n=== RUNNING FILE UPLOAD ===")

        run_command(
            [
                sys.executable,
                str(upload_script),
                "--source-dir",
                str(source_dir),
            ],
            cwd=project_dir,
            env=child_environment,
        )

        print(
            "\n=== RUNNING IMAGE ANNOTATION ==="
        )

        run_command(
            [
                sys.executable,
                str(annotate_script),
            ],
            cwd=project_dir,
            env=child_environment,
        )

        print("\n=== VERIFYING FILE ===")

        test_success, checked_files = (
            verify_space_files(
                client=client,
                space_directory_id=(
                    space_directory_id
                ),
                space_name=space_name,
            )
        )

        print()

        if test_success:
            print(
                "TEST PASSED —"
                f"verified {checked_files} "
                "files."
            )
            return 0

        print(
            "TEST FAILED — "
            f"verified {checked_files} "
            "files."
        )
        return 1

    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        requests.RequestException,
        RuntimeError,
        KeyError,
    ) as error:
        test_failed_with_exception = True

        print(
            "\nIntegration test error: "
            f"{error}",
            file=sys.stderr,
        )

        return 1

    except KeyboardInterrupt:
        test_failed_with_exception = True

        print(
            "\nThe test was interrupted by the user.",
            file=sys.stderr,
        )

        return 130

    finally:
        if test_failed_with_exception:
            print_container_logs(
                provider_container
            )
            print_container_logs(
                zone_container
            )

        print("\nRemoving test containers...")

        remove_container(
            provider_container
        )
        remove_container(
            zone_container
        )


if __name__ == "__main__":
    raise SystemExit(main())
