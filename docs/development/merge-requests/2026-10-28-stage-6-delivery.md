# Этап 6: подготовка Windows-поставки для тестирования

## Проблема и результат

Для пользовательской проверки нужен воспроизводимый способ собрать CLI и GUI в Windows `.exe`. В ветке `feat/stage-6-delivery` добавлены PyInstaller-спецификации, PowerShell-сборка и smoke-проверка на контрольном комплекте. Текущая версия кода остаётся `0.5.0`; это старт поставки, а не финальная приёмка.

## Проверки

- Linux: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q` — **80 passed**.
- `python -m py_compile deploy/windows/opd-cli.spec deploy/windows/opd-gui.spec` — успешно.
- `git diff --check` — успешно.
- Windows `.exe`, установка и smoke-проверка пока не выполнялись: PyInstaller отсутствует в Linux `.venv`.

## Как проверить на Windows

1. Открыть PowerShell в корне репозитория на официальном CPython 3.12 x64.
2. Запустить `Set-ExecutionPolicy -Scope Process Bypass`.
3. Запустить `.\scripts\build_windows.ps1`.
4. Запустить `.\scripts\smoke_windows.ps1`.
5. Открыть `.\dist\opd-gui\opd-gui.exe` и пройти импорт, загрузку Ω, расчёт и открытие результата.

Подробности: [windows-stage6-testing.md](../../development/windows-stage6-testing.md).

## Требования и ограничения

DEL-01 получает обновлённое доказательство наличия исходного кода, зависимостей, скриптов и инструкции, но остаётся частичным до Windows-проверки. Q-05, Q-06, Q-07 и Q-08 не изменяются. Независимая приёмка итогового commit обязательна перед merge; удалённый MR ещё не создан.
