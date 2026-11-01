# Оценка при старте этапа 6

**Дата:** 28 октября 2026 г.  
**Проверяемая ветка:** `feat/stage-6-delivery`; Windows-пакет ещё не собран.  
**Состав:** 84 требования ТЗ-ОАМ-01 версии 1.0 с UI-12, вес каждого 1.

Подтверждено **48/84 = 57,14%**; частично — 30, не проверено — 6. Изменение относительно [предыдущей оценки](2026-10-28-stage-5-validation-full-rows.md) — **0,00 процентного пункта**: добавлены только средства поставки и инструкции, новые требования ещё не подтверждены.

## Выполнено для старта

- Добавлены `deploy/windows/opd-cli.spec` и `deploy/windows/opd-gui.spec` для PyInstaller 6.11.1.
- Добавлен `scripts/build_windows.ps1`: отдельное окружение, установка текущего checkout, тесты и сборка CLI/GUI.
- Добавлен `scripts/smoke_windows.ps1`: `check` и `calculate` на контрольном четырёхфайловом комплекте.
- Добавлена инструкция [windows-stage6-testing.md](../../../docs/development/windows-stage6-testing.md).
- На Linux исходный проект проверен: `80 passed`, `compileall` и `git diff --check` успешны.
- После исправления package entry points PyInstaller 6.11.1 собрал CLI и GUI на Linux. Упакованный CLI прошёл `check` и `calculate` на `PAK_export_reference_4files_v1.zip`; GUI-бинарник оставался запущенным 5 секунд в `QT_QPA_PLATFORM=offscreen`.

## Ограничения

Локальная сборка выполнена под Linux и создаёт Linux-бинарники, не Windows `.exe`. Windows 10/11, установка, GUI-smoke на Windows, реальный ПАК, предельный объём и согласованные критерии времени/памяти остаются открытыми. Статусы требований не повышались до полного Windows-протокола.
