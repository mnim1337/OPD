# Среда разработки и проверки

Текущая версия кода указана в корневом `VERSION`; манифест `pyproject.toml` должен совпадать с ним. Для этапа 2 требуется Python 3.12 и PySide6 Essentials 6.12.0. Версия Windows и способ упаковки `.exe` ещё не согласованы; эта инструкция проверена на Linux в локальной среде разработки, а Windows-сборка относится к этапу 6.

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
.venv/bin/opd check path/to/four-files
.venv/bin/opd-gui
```

На Windows использовать `.venv\Scripts\python.exe` и `.venv\Scripts\opd.exe`; переменная `QT_QPA_PLATFORM=offscreen` нужна только для автоматической проверки GUI без экрана. Для исходного кода без установки пакета можно указать `PYTHONPATH=src` и запустить `python -m scanner.cli` или `python -m scanner.gui`.

Команды CLI:

```text
opd check <folder>
opd save-draft <folder> --store <drafts.sqlite3> --id <draft-id>
opd export-draft --store <drafts.sqlite3> --id <draft-id> [--revision N] <empty-folder>
```

`check` проверяет завершённый четырёхфайловый комплект. `save-draft` сохраняет новую неизменяемую версию в SQLite вместе с оригинальными байтами. `export-draft` выдаёт новый комплект с пересчитанными числом строк, размерами и SHA-256. Команда откажет в экспорте неполного или ошибочного набора.

Автоматические тесты извлекают исходные контрольные архивы во временные каталоги и не изменяют архивы. В `tests/integration/` находятся проверки импорта, обратного экспорта, сохранения версий и Qt-редактора; в `tests/unit/` — проверка профиля. Для браузерного Playwright-сценария интерфейса нет: приложение настольное Qt.
