# Документация OPD

Открывайте раздел по задаче; историю не используйте как текущее состояние.

| Задача | Актуальный источник |
| --- | --- |
| Что делает программа, как запустить | [README](../README.md), [установка](development/setup.md) |
| Требования и открытые решения | [ТЗ](../requirements/ТЗ_Оценка_маршрутизации_единый_документ.docx), [матрица](../requirements/traceability.md), [вопросы](../requirements/open-questions.md) |
| Ввод, паспорт, хранение | [руководство](user-guide/data-entry.md), [архитектура](architecture/data-entry.md) |
| Расчёт К0–К3 | [К0–К2](algorithms/k0-k2.md), [К3](algorithms/k3.md), [архитектура](architecture/calculation-core.md) |
| Результаты и серии | [руководство](user-guide/calculation.md), [архитектура](architecture/run-and-results.md) |
| Windows `.exe` и визуальная сверка | [сборка](development/windows-stage6-testing.md), [протокол](development/windows-visual-acceptance.md) |
| Договорённости по архитектуре | [ADR](decisions/) |
| История работы | [старое understanding](archive/understanding-through-2026-10-30.md), [описания MR](development/merge-requests/) |

Каталоги: `architecture/` — контракты компонентов; `algorithms/` — формулы; `decisions/` — ADR; `user-guide/` — действия пользователя; `development/` — установка, проверки и планы; `archive/` — история. Новые постоянные каталоги добавляйте сюда.
