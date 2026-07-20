# Onedata recruitment task

Projekt realizuje zadanie rekrutacyjne

## Zadanie podstawowe

W katalogu głównym znajdują się dwa skrypty:

- `upload_files.py` — przechodzi po lokalnym katalogu źródłowym i odtwarza jego strukturę w wybranym Onedata Space;
- `annotate_images.py` — przechodzi po plikach w Space, rozpoznaje obrazy, odczytuje ich wymiary i zapisuje metadane JSON w formacie:

Wymagają dodania ONEDATA_TOKEN do .env

```json
{
  "width": 1920,
  "height": 1080
}
```

Pliki niegraficzne są pomijane i nie otrzymują metadanych.

## Zadanie dodatkowe

Katalog `task3/` zawiera wersje skryptów przystosowane do lokalnego środowiska Onedata Demo Mode oraz skrypt:

- `integration_test.py` — uruchamia w Dockerze jeden Onezone i jeden Oneprovider, wykonuje upload, uruchamia anotację, a następnie weryfikuje metadane wszystkich plików.

Test integracyjny nie wymaga ręcznego podawania tokenu do lokalnego środowiska. Token Demo Mode jest pobierany automatycznie z kontenera Oneprovider i przekazywany do procesów potomnych.

## Struktura projektu

```text
Cyfronet/
├── pictures/
├── task3/
│   ├── pictures/
│   ├── .env
│   ├── annotate_images.py
│   ├── integration_test.py
│   └── upload_files.py
├── .env
├── annotate_images.py
├── README.md
├── requirements.txt
└── upload_files.py
```

Katalog `pictures/` zawiera dane testowe: pięć podkatalogów, obrazy oraz pliki niegraficzne.

## Instalacja

Utwórz i aktywuj środowisko wirtualne:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Zainstaluj zależności:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Konfiguracja zadań podstawowych

Skrypty w katalogu głównym odczytują konfigurację z pliku `.env`.

Przykład:

```dotenv
ONEDATA_PROVIDER_HOST=provider.example.org
ONEDATA_TOKEN=
ONEDATA_SPACE_ID=your-space-id
ONEDATA_SPACE_DIR_ID=your-space-directory-id
ONEDATA_SPACE_NAME=your-space-name
ONEDATA_VERIFY_SSL=true
```

Znaczenie zmiennych:

- `ONEDATA_PROVIDER_HOST` — domena Oneprovidera
- `ONEDATA_TOKEN` — token dostępu użytkownika;
- `ONEDATA_SPACE_ID` — identyfikator Space;
- `ONEDATA_SPACE_DIR_ID` — identyfikator katalogu głównego Space;
- `ONEDATA_SPACE_NAME` — nazwa Space używana w komunikatach;
- `ONEDATA_VERIFY_SSL` — weryfikacja certyfikatu TLS, domyślnie `true`.

## Uruchomienie zadania 1 — upload

```bash
python3 upload_files.py --source-dir pictures
```

Skrypt:

1. tworzy w Space katalog o nazwie zgodnej z nazwą katalogu źródłowego;
2. odtwarza wszystkie podkatalogi;
3. przesyła wszystkie zwykłe pliki;
4. wypisuje postęp i końcowe podsumowanie.

Dowiązania symboliczne są pomijane ze względów bezpieczeństwa.

## Uruchomienie zadania 2 — anotacja obrazów

```bash
python3 annotate_images.py
```

Skrypt:

1. przechodzi rekurencyjnie po całym Space;
2. pobiera zawartość każdego zwykłego pliku;
3. rozpoznaje obrazy na podstawie zawartości;
4. zapisuje dla obrazów metadane JSON z kluczami `width` i `height`;
5. pomija pliki niegraficzne;
6. kończy się kodem różnym od zera, gdy wystąpi błąd.

Przykładowy wynik:

```text
[ OK ] praktyki/pictures/cats/photo.jpg — 1920x1080px
[SKIP] praktyki/pictures/cats/readme.txt — to nie jest obraz
```

## Uruchomienie zadania 3 — test integracyjny

Przejdź do katalogu `task3`:

```bash
cd task3
```

Uruchom test:

```bash
python3 integration_test.py --source-dir pictures
```

Test wykonuje następujące kroki:

1. uruchamia kontener Onezone w Demo Mode;
2. czeka na gotowość Onezone;
3. uruchamia kontener Oneprovider w Demo Mode;
4. czeka na gotowość całego środowiska;
5. pobiera automatycznie token Demo Mode;
6. uruchamia `upload_files.py`;
7. uruchamia `annotate_images.py`;
8. przechodzi po wszystkich plikach w lokalnym `demo-space`;
9. sprawdza, czy:
   - obrazy mają metadane JSON zawierające `width` i `height`;
   - pliki niegraficzne nie mają metadanych JSON;
10. wypisuje ścieżkę każdego pliku wraz ze statusem `OK` albo `ERROR`;
11. usuwa kontenery testowe po zakończeniu.

Przykładowe zakończenie:

```text
[OK] demo-space/pictures/cats/photo.jpg — obraz ma width i height
[OK] demo-space/pictures/cats/readme.txt — plik niegraficzny nie ma metadanych JSON

TEST POWIÓDŁ SIĘ — sprawdzono 30 plików.
```

## Uwagi dotyczące Demo Mode

Komunikaty takie jak:

```text
Awaiting Onezone service readiness...
Waiting for the demo environment to be set up...
```

oznaczają, że skrypty startowe nadal oczekują na gotowość usług.

Lokalne Demo Mode używa certyfikatów testowych, dlatego skrypty w `task3/` wyłączają weryfikację TLS dla lokalnego środowiska uruchomionego w Dockerze. 

## Kody zakończenia

- `0` — operacja lub test zakończyły się powodzeniem;
- `1` — wystąpił błąd połączenia, przetwarzania albo weryfikacji;
- `2` — niepoprawne argumenty lub brak wymaganego pliku/katalogu;
- `130` — test integracyjny został przerwany przez użytkownika.
