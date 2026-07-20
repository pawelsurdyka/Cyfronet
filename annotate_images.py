#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, BinaryIO

import requests
from dotenv import load_dotenv
from PIL import Image, UnidentifiedImageError


@dataclass(frozen=True)
class RemoteFile:
    """Represents a regular file stored in Onedata."""

    file_id: str
    path: PurePosixPath


class OnedataClient:
    """
    Provides methods for browsing files, downloading content,
    and managing JSON metadata through the Oneprovider API.
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

    @staticmethod
    def _response_details(response: requests.Response) -> Any:
        """ Returns the API response as JSON or plain text. """
        try:
            return response.json()
        except ValueError:
            return response.text

    def _expect_status(
        self,
        response: requests.Response,
        expected_statuses: set[int],
        action: str,
    ) -> None:
        if response.status_code not in expected_statuses:
            raise RuntimeError(
                f"{action}. HTTP {response.status_code}: "
                f"{self._response_details(response)}"
            )

    def iter_children(
        self,
        directory_id: str,
    ) -> Iterator[dict[str, str]]:
        """
        Yields all items located directly in the specified directory.
        Handles paginated API responses.
        """

        url = (
            f"{self.base_url}/data/"
            f"{directory_id}/children"
        )

        next_page_token: str | None = None

        while True:
            request_body: dict[str, Any] = {
                "attributes": [
                    "fileId",
                    "name",
                    "type",
                ],
                "limit": 100,
            }

            if next_page_token is not None:
                request_body["token"] = next_page_token

            response = self.session.get(
                url,
                json=request_body,
                timeout=(10, 60),
            )

            self._expect_status(
                response,
                {200},
                (
                    "Failed to read directory "
                    f"{directory_id}"
                ),
            )

            try:
                payload = response.json()
            except ValueError as error:
                raise RuntimeError(
                    "The API returned a response that is not valid JSON."
                ) from error

            children = payload.get("children")

            if not isinstance(children, list):
                raise RuntimeError(
                    "The API returned an invalid list "
                    "of directory entries."
                )

            for child in children:
                if not isinstance(child, dict):
                    raise RuntimeError(
                        "The API returned an invalid "
                        "directory entry."
                    )

                file_id = child.get("fileId")
                name = child.get("name")
                file_type = child.get("type")

                values = (file_id, name, file_type)

                if not all(
                    isinstance(value, str)
                    for value in values
                ):
                    raise RuntimeError(
                        "A directory entry does not contain "
                        "a valid fileId, name, or type."
                    )

                yield {
                    "fileId": file_id,
                    "name": name,
                    "type": file_type,
                }

            if payload.get("isLast") is True:
                return

            next_page_token = payload.get(
                "nextPageToken"
            )

            if (
                not isinstance(next_page_token, str)
                or not next_page_token
            ):
                raise RuntimeError(
                    "The API indicated another page but did "
                    "not return a valid nextPageToken."
                )

    @contextmanager
    def download_file(
        self,
        file_id: str,
    ) -> Iterator[BinaryIO]:
        """
        Downloads a file into a temporary buffer.
        Small files remain in memory, while larger files are
        automatically written to a temporary file.
        """
        url = (
            f"{self.base_url}/data/"
            f"{file_id}/content"
        )

        temporary_file = tempfile.SpooledTemporaryFile(
            max_size=16 * 1024 * 1024,
            mode="w+b",
        )

        try:
            with self.session.get(
                url,
                stream=True,
                timeout=(10, 300),
            ) as response:
                self._expect_status(
                    response,
                    {200},
                    (
                        "Failed to download file "
                        f"{file_id}"
                    ),
                )

                for chunk in response.iter_content(
                    chunk_size=1024 * 1024
                ):
                    if chunk:
                        temporary_file.write(chunk)

            temporary_file.seek(0)
            yield temporary_file

        finally:
            temporary_file.close()

    def set_json_metadata(
        self,
        file_id: str,
        metadata: dict[str, int],
    ) -> None:
        """Sets JSON metadata for the specified file."""

        url = (
            f"{self.base_url}/data/"
            f"{file_id}/metadata/json"
        )

        response = self.session.put(
            url,
            json=metadata,
            timeout=(10, 60),
        )

        self._expect_status(
            response,
            {204},
            (
                "Failed to set metadata for"
                f"file {file_id}"
            ),
        )


def required_environment_variable(name: str) -> str:
    """ Returns a required environment variable or raises an error. """
    value = os.getenv(name)

    if not value:
        raise RuntimeError(
            "Required environment variable is missing: "
            f"{name}"
        )

    return value


def iter_space_files(
    client: OnedataClient,
    space_dir_id: str,
    space_name: str,
) -> Iterator[RemoteFile]:
    """
    Traverses all directory levels in the Onedata space.
    Using a stack instead of recursive function calls prevents
    exceeding Python's recursion limit.
    """
    directories: list[
        tuple[str, PurePosixPath]
    ] = [
        (
            space_dir_id,
            PurePosixPath(space_name),
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
            client.iter_children(directory_id),
            key=lambda child: child["name"].casefold(),
        )

        for child in children:
            child_path = (
                directory_path / child["name"]
            )
            child_type = child["type"]

            if child_type == "DIR":
                child_directories.append(
                    (
                        child["fileId"],
                        child_path,
                    )
                )

            elif child_type == "REG":
                yield RemoteFile(
                    file_id=child["fileId"],
                    path=child_path,
                )

            else:
                print(
                    f"[SKIP] {child_path} — "
                    f"unsupported type: {child_type}"
                )

        # Reversing the list ensures that directories
        # are visited in alphabetical order.
        directories.extend(
            reversed(child_directories)
        )


def get_image_dimensions(
    client: OnedataClient,
    remote_file: RemoteFile,
) -> tuple[int, int] | None:
    """
    Returns the image dimensions or None when Pillow does not
    recognize the file as an image.
    """
    with client.download_file(
        remote_file.file_id
    ) as downloaded_file:
        try:
            with Image.open(downloaded_file) as image:
                width, height = image.size

                # Verifies that the image format and content are valid.
                image.verify()

                return int(width), int(height)

        except (
            UnidentifiedImageError,
            OSError,
            ValueError,
        ):
            return None

        except Image.DecompressionBombError as error:
            raise RuntimeError(
                "The image is too large to process safely: "
                f"{error}"
            ) from error


def main() -> int:
    """
    Loads the configuration and processes every file in the space.
    """
    load_dotenv()

    try:
        provider_host = (
            required_environment_variable(
                "ONEDATA_PROVIDER_HOST"
            )
        )
        token = required_environment_variable(
            "ONEDATA_TOKEN"
        )
        space_dir_id = (
            required_environment_variable(
                "ONEDATA_SPACE_DIR_ID"
            )
        )

        space_name = os.getenv(
            "ONEDATA_SPACE_NAME",
            "Space",
        )

        client = OnedataClient(
            provider_host=provider_host,
            token=token,
        )

        scanned_files = 0
        annotated_images = 0
        skipped_files = 0
        errors = 0

        for remote_file in iter_space_files(
            client=client,
            space_dir_id=space_dir_id,
            space_name=space_name,
        ):
            scanned_files += 1

            try:
                dimensions = get_image_dimensions(
                    client,
                    remote_file,
                )

                if dimensions is None:
                    skipped_files += 1

                    print(
                        f"[SKIP] {remote_file.path} — "
                        "not an image"
                    )
                    continue

                width, height = dimensions

                metadata = {
                    "width": width,
                    "height": height,
                }

                client.set_json_metadata(
                    file_id=remote_file.file_id,
                    metadata=metadata,
                )

                annotated_images += 1

                print(
                    f"[ OK ] {remote_file.path} — "
                    f"{width}x{height}px"
                )

            except (
                requests.RequestException,
                RuntimeError,
            ) as error:
                errors += 1

                print(
                    f"[ERROR] {remote_file.path} — "
                    f"{error}",
                    file=sys.stderr,
                )

    except (
        requests.RequestException,
        RuntimeError,
    ) as error:
        print(
            f"Error: {error}",
            file=sys.stderr,
        )
        return 1

    print()
    print("Summary:")
    print(
        f"  scanned files:       "
        f"{scanned_files}"
    )
    print(
        f"  annotated images:         "
        f"{annotated_images}"
    )
    print(
        f"  skipped non-image files:    "
        f"{skipped_files}"
    )
    print(
        f"  errors:                  "
        f"{errors}"
    )

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
