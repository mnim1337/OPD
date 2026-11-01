# Среда разработки и проверки

Текущая версия кода указана в корневом `VERSION`; манифест `pyproject.toml` должен совпадать с ним. Для разработки требуется Python 3.12 и PySide6 Essentials 6.12.0. Целевые ОС — Windows 10/11; архитектура и окончательный вид поставки ещё не согласованы. Проверочная сборка `.exe` описана в [инструкции этапа 6](windows-stage6-testing.md).

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
.venv/bin/opd check path/to/four-files
.venv/bin/opd calculate path/to/four-files --profile path/to/profile.json --output assessment.json
.venv/bin/opd-gui
```

На Windows использовать `.venv\Scripts\python.exe` и `.venv\Scripts\opd.exe`; переменная `QT_QPA_PLATFORM=offscreen` нужна только для автоматической проверки GUI без экрана. Для исходного кода без установки пакета можно указать `PYTHONPATH=src` и запустить `python -m scanner.cli` или `python -m scanner.gui`.

После повышения `VERSION` повторите `.venv/bin/python -m pip install -e '.[test]'` для обновления установленных entry points и метаданных пакета. Запуск с `PYTHONPATH=src` использует исходный код текущего checkout даже при старых метаданных `.venv`; такую среду нужно отметить в протоколе и обновить до поставки.

Если Python установлен через **MSYS2 UCRT64**, команда `pip install -e '.[test]'` не найдёт официальный wheel `PySide6_Essentials==6.12.0`: MSYS2 Python использует несовместимый с wheel PyPI ABI. Проверенный пользователем 26.10.2026 способ запуска из исходников — открыть терминал MSYS2 UCRT64 и выполнить:

```bash
pacman -S mingw-w64-ucrt-x86_64-pyside6
cd /c/path/to/OPD  # заменить на путь к своему каталогу
PYTHONPATH=src /ucrt64/bin/python -m scanner.gui
```

Пакет MSYS2 PySide6 версии 6.12.0 включает Qt Widgets. Этот способ проверен только на запуск GUI в пользовательской Windows 11; сборка `.exe` и полный набор испытаний на Windows ещё не выполнялись. Для стандартной установки через `pip` требуется обычная сборка CPython для Windows x64, а не Python из MSYS2. Для сборки тестового `.exe` используйте [инструкцию поставки Windows](windows-stage6-testing.md) и `scripts/build_windows.ps1`.

Команды CLI:

```text
opd check <folder>
opd save-draft <folder> --store <drafts.sqlite3> --id <draft-id>
opd export-draft --store <drafts.sqlite3> --id <draft-id> [--revision N] <empty-folder>
opd calculate <folder> --profile <profile.json> [--output <assessment.json>]
opd sensitivity <first-folder> <second-folder> --profile <profile.json> [--epsilon-weight <0..2>] [--output <json>]
opd repeats <folder> [<folder> ...] --profile <profile.json> [--independence-basis <text>] [--output <json>]
```

`check` проверяет завершённый четырёхфайловый комплект. `save-draft` сохраняет новую неизменяемую версию в SQLite вместе с оригинальными байтами. `export-draft` выдаёт новый комплект с пересчитанными числом строк, размерами и SHA-256. Команда откажет в экспорте неполного или ошибочного набора.

`calculate` запускает К0–К2 без GUI и записывает результаты с промежуточной трассой. `sensitivity` и `repeats` выполняют К3; формулы и ограничения описаны в [документе К3](../algorithms/k3.md). В `opd-gui` доступны запуск расчёта, история, JSON/CSV-экспорт результатов и резервная копия локального хранилища; детали и оставшиеся ограничения описаны в [руководстве по расчёту](../user-guide/calculation.md) и [архитектуре запуска](../architecture/run-and-results.md). Вычисления на тестовых данных и рабочие допуски описаны в [алгоритме](../algorithms/k0-k2.md) и [плане численных проверок](numerical-test-plan.md).

Автоматические тесты извлекают исходные контрольные архивы во временные каталоги и не изменяют архивы. В `tests/integration/` находятся проверки импорта, обратного экспорта, сохранения версий и Qt-редактора; в `tests/unit/` — проверка профиля; в `tests/numerical/` — сверка формул и граничных случаев К0–К2. Для браузерного Playwright-сценария интерфейса нет: приложение настольное Qt.
