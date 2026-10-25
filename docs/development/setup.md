# Среда разработки и проверки

Текущая версия кода указана в корневом `VERSION`; манифест `pyproject.toml` должен совпадать с ним. Для этапов 2–4 требуется Python 3.12 и PySide6 Essentials 6.12.0. Версия Windows и способ упаковки `.exe` ещё не согласованы; эта инструкция проверена на Linux в локальной среде разработки, а Windows-сборка относится к этапу 6.

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
.venv/bin/opd check path/to/four-files
.venv/bin/opd calculate path/to/four-files --profile path/to/profile.json --output assessment.json
.venv/bin/opd-gui
```

На Windows использовать `.venv\Scripts\python.exe` и `.venv\Scripts\opd.exe`; переменная `QT_QPA_PLATFORM=offscreen` нужна только для автоматической проверки GUI без экрана. Для исходного кода без установки пакета можно указать `PYTHONPATH=src` и запустить `python -m scanner.cli` или `python -m scanner.gui`.

Команды CLI:

```text
opd check <folder>
opd save-draft <folder> --store <drafts.sqlite3> --id <draft-id>
opd export-draft --store <drafts.sqlite3> --id <draft-id> [--revision N] <empty-folder>
opd calculate <folder> --profile <profile.json> [--output <assessment.json>]
```

`check` проверяет завершённый четырёхфайловый комплект. `save-draft` сохраняет новую неизменяемую версию в SQLite вместе с оригинальными байтами. `export-draft` выдаёт новый комплект с пересчитанными числом строк, размерами и SHA-256. Команда откажет в экспорте неполного или ошибочного набора.

`calculate` запускает К0–К2 без GUI и записывает результаты с промежуточной трассой. В `opd-gui` доступны запуск расчёта, история, JSON/CSV-экспорт результатов и резервная копия локального хранилища; детали и оставшиеся ограничения описаны в [руководстве по расчёту](../user-guide/calculation.md) и [архитектуре запуска](../architecture/run-and-results.md). Вычисления на тестовых данных и рабочие допуски описаны в [алгоритме](../algorithms/k0-k2.md) и [плане численных проверок](numerical-test-plan.md).

Автоматические тесты извлекают исходные контрольные архивы во временные каталоги и не изменяют архивы. В `tests/integration/` находятся проверки импорта, обратного экспорта, сохранения версий и Qt-редактора; в `tests/unit/` — проверка профиля; в `tests/numerical/` — сверка формул и граничных случаев К0–К2. Для браузерного Playwright-сценария интерфейса нет: приложение настольное Qt.
