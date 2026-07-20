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
import urllib3
from urllib3.exceptions import InsecureRequestWarning


import requests
from dotenv import load_dotenv
from PIL import Image, UnidentifiedImageError

def environment_flag(name: str, default: bool = True) -> bool:
    value = os.getenv(name)

    if value is None:
        return default

    normalized = value.strip().lower()

    if normalized in {"1", "true", "yes", "on"}:
        return True

    if normalized in {"0", "false", "no", "off"}:
        return False

    raise RuntimeError(
        f"Niepoprawna wartość {name}: {value!r}. "
        "Użyj true albo false."
    )

@dataclass(frozen=True)
class RemoteFile:
    """Reprezentuje zwykły plik znajdujący się w Onedata."""

    file_id: str
    path: PurePosixPath


class OnedataClient:
    """Prosty klient operacji potrzebnych w zadaniu."""

    def __init__(
        self,
        provider_host: str,
        token: str,
        verify_ssl: bool = True,
    ) -> None:
        self.base_url = (
            f"https://{provider_host}/api/v3/oneprovider"
        )

        self.session = requests.Session()
        self.session.verify = verify_ssl
        if not verify_ssl:
            urllib3.disable_warnings(InsecureRequestWarning)

        self.session.headers.update({
            "X-Auth-Token": token,
        })

    @staticmethod
    def _response_details(response: requests.Response) -> Any:
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
        Zwraca wszystkie elementy bezpośrednio znajdujące się
        w podanym katalogu.

        Obsługuje stronicowanie odpowiedzi API.
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

            # Oneprovider API 25 przyjmuje opcje listowania
            # jako JSON w ciele zapytania GET.
            response = self.session.get(
                url,
                json=request_body,
                timeout=(10, 60),
            )

            self._expect_status(
                response,
                {200},
                (
                    "Nie udało się odczytać katalogu "
                    f"{directory_id}"
                ),
            )

            try:
                payload = response.json()
            except ValueError as error:
                raise RuntimeError(
                    "API zwróciło odpowiedź, która nie jest JSON."
                ) from error

            children = payload.get("children")

            if not isinstance(children, list):
                raise RuntimeError(
                    "API zwróciło niepoprawną listę "
                    "elementów katalogu."
                )

            for child in children:
                if not isinstance(child, dict):
                    raise RuntimeError(
                        "API zwróciło niepoprawny element "
                        "katalogu."
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
                        "Element katalogu nie zawiera "
                        "fileId, name lub type."
                    )

                yield {
                    "fileId": file_id,
                    "name": name,
                    "type": file_type,
                }

            # Pole isLast jest wiarygodnym wskaźnikiem,
            # czy pobraliśmy wszystkie elementy.
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
                    "API wskazało kolejną stronę, ale "
                    "nie zwróciło nextPageToken."
                )

    @contextmanager
    def download_file(
        self,
        file_id: str,
    ) -> Iterator[BinaryIO]:
        """
        Pobiera plik do tymczasowego bufora.

        Małe pliki pozostają w pamięci, a większe są
        automatycznie zapisywane do pliku tymczasowego.
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
                        "Nie udało się pobrać pliku "
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
        """Ustawia metadane JSON dla podanego pliku."""

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
                "Nie udało się ustawić metadanych "
                f"pliku {file_id}"
            ),
        )


def required_environment_variable(name: str) -> str:
    value = os.getenv(name)

    if not value:
        raise RuntimeError(
            "Brak wymaganej zmiennej środowiskowej: "
            f"{name}"
        )

    return value


def iter_space_files(
    client: OnedataClient,
    space_dir_id: str,
    space_name: str,
) -> Iterator[RemoteFile]:
    """
    Przechodzi przez wszystkie zagłębienia Space.

    Użycie stosu zamiast rekurencyjnego wywoływania funkcji
    chroni przed przekroczeniem limitu rekurencji Pythona.
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
                    f"nieobsługiwany typ: {child_type}"
                )

        # reversed sprawia, że katalogi są odwiedzane
        # w kolejności alfabetycznej.
        directories.extend(
            reversed(child_directories)
        )


def get_image_dimensions(
    client: OnedataClient,
    remote_file: RemoteFile,
) -> tuple[int, int] | None:
    """
    Zwraca wymiary obrazu lub None, jeśli plik nie jest
    rozpoznawany przez Pillow jako obraz.
    """
    with client.download_file(
        remote_file.file_id
    ) as downloaded_file:
        try:
            with Image.open(downloaded_file) as image:
                width, height = image.size

                # Sprawdzenie, czy format obrazu jest poprawny.
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
                "Obraz jest zbyt duży do bezpiecznego "
                f"przetworzenia: {error}"
            ) from error


def main() -> int:
    load_dotenv(override=False)

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

        verify_ssl = environment_flag(
            "ONEDATA_VERIFY_SSL",
            default=True,
        )
        client = OnedataClient(
            provider_host=provider_host,
            token=token,
            verify_ssl=verify_ssl,
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
                        "to nie jest obraz"
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
            f"Błąd: {error}",
            file=sys.stderr,
        )
        return 1

    print()
    print("Podsumowanie:")
    print(
        f"  sprawdzone pliki:       "
        f"{scanned_files}"
    )
    print(
        f"  opisane obrazy:         "
        f"{annotated_images}"
    )
    print(
        f"  pominięte nieobrazy:    "
        f"{skipped_files}"
    )
    print(
        f"  błędy:                  "
        f"{errors}"
    )

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
