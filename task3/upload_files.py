#!/usr/bin/env python3

import argparse
import os
import sys
from pathlib import Path
from typing import Any
import urllib3
from urllib3.exceptions import InsecureRequestWarning

import requests
from dotenv import load_dotenv


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

class OnedataClient:
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

    def create_directory(self, parent_id: str, name: str) -> str:
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
                f"Nie udało się utworzyć katalogu {name!r}. "
                f"HTTP {response.status_code}: "
                f"{self._response_details(response)}"
            )

        file_id = response.json().get("fileId")
        if not file_id:
            raise RuntimeError(
                f"API nie zwróciło fileId dla katalogu {name!r}"
            )

        return file_id

    def upload_file(self, parent_id: str, file_path: Path) -> str:
        url = f"{self.base_url}/data/{parent_id}/children"

        with file_path.open("rb") as file_handle:
            response = self.session.post(
                url,
                params={"name": file_path.name},
                headers={"Content-Type": "application/octet-stream"},
                data=file_handle,
                timeout=(30, 600),
            )

        if response.status_code != 201:
            raise RuntimeError(
                f"Nie udało się przesłać pliku {file_path}. "
                f"HTTP {response.status_code}: "
                f"{self._response_details(response)}"
            )

        file_id = response.json().get("fileId")
        if not file_id:
            raise RuntimeError(
                f"API nie zwróciło fileId dla pliku {file_path}"
            )

        return file_id


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Wgrywa lokalny katalog do Onedata."
    )
    parser.add_argument(
        "--source-dir",
        required=True,
        type=Path,
        help="Ścieżka do lokalnego katalogu nadrzędnego.",
    )
    return parser.parse_args()


def required_environment_variable(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Brak wymaganej zmiennej środowiskowej: {name}"
        )
    return value


def upload_directory_tree(
    client: OnedataClient,
    source_dir: Path,
    space_dir_id: str,
) -> tuple[int, int]:
    """
    Tworzy w Space katalog o nazwie source_dir, a następnie
    odwzorowuje całą jego zawartość.
    """
    print(f"Tworzenie katalogu głównego: {source_dir.name}/")

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
                    f"Dowiązania symboliczne nie są obsługiwane: "
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
                    f"Dowiązania symboliczne nie są obsługiwane: "
                    f"{local_file}"
                )

            relative_path = local_file.relative_to(source_dir)
            size_mib = local_file.stat().st_size / (1024 * 1024)

            print(
                f"[FILE] {relative_path} "
                f"({size_mib:.2f} MiB) ... ",
                end="",
                flush=True,
            )

            client.upload_file(current_remote_id, local_file)

            print("OK")
            uploaded_files += 1

    return uploaded_directories, uploaded_files


def main() -> int:
    load_dotenv(override=False)

    args = parse_arguments()
    source_dir = args.source_dir.expanduser().resolve()

    if not source_dir.exists():
        print(
            f"Błąd: katalog nie istnieje: {source_dir}",
            file=sys.stderr,
        )
        return 1

    if not source_dir.is_dir():
        print(
            f"Błąd: podana ścieżka nie jest katalogiem: {source_dir}",
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

        verify_ssl = environment_flag(
            "ONEDATA_VERIFY_SSL",
            default=True,
        )
        client = OnedataClient(
            provider_host=provider_host,
            token=token,
            verify_ssl=verify_ssl,
        )

        directories_count, files_count = upload_directory_tree(
            client,
            source_dir,
            space_dir_id,
        )

    except requests.RequestException as error:
        print(
            f"Błąd połączenia z Onedata: {error}",
            file=sys.stderr,
        )
        return 1
    except RuntimeError as error:
        print(f"Błąd: {error}", file=sys.stderr)
        return 1

    print()
    print("Wgrywanie zakończone pomyślnie.")
    print(f"Utworzone katalogi: {directories_count}")
    print(f"Przesłane pliki: {files_count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
