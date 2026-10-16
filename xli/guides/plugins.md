# Плагины XPI: разработка, авторство и инструменты

XPI — доверенные Python-плагины внутри процесса XLI. MCP — отдельные серверы;
skills — инструкции модели. Для добавления инструмента или наблюдения за работой
агента используйте XPI. Установка GUI/Textual/Node.js для этого не нужна.

## Быстрый старт

```bash
xli plugins init my-plugin --author "Ваше имя" --license MIT
xli plugins list
xli plugins info my-plugin
```

Команда создаёт `~/.xli/xpi/my-plugin/` с `manifest.json`, `plugin.py`, `README.md`.
Если задан `XLI_CONFIG_DIR`, используется `$XLI_CONFIG_DIR/xpi/`.
Существующая директория не перезаписывается. Новый плагин **выключен**.
Прочитайте код, затем явно разрешите его загрузку:

```bash
xli plugins enable my-plugin
xli tools schema --json
xli run 'Вызови my-plugin.hello с name="мир"'
xli plugins disable my-plugin
```

Плагин появляется в каталоге инструментов и системном промпте агента.
Инструменты работают через общий ToolRegistry в CLI, REPL, TUI и ядре.
`xli plugins info my-plugin --json` выдаёт метаданные для автоматизации.
В TUI команда `/plugins` показывает авторов и состояние загрузки.

**Важно:** команды `list`, `info`, `enable`, `reload` загружают включённые плагины.
Это не безопасный просмотр недоверенного кода. Сначала проверяйте файлы вручную.

## Manifest API v1

```json
{
  "name": "my-plugin",
  "version": "0.1.0",
  "api_version": 1,
  "author": "Ваше имя",
  "license": "MIT",
  "description": "Приветствие и учёт запусков",
  "homepage": "https://github.com/your-account/your-plugin",
  "main": "plugin.py",
  "entry": "Plugin",
  "platform": "all",
  "enabled": false,
  "config": {"greeting": "Привет"}
}
```

- `name`: латинская буква, затем буквы, цифры, `_`, `-`; совпадает с именем папки.
- `version`: строка; это публичная версия в списке плагинов. Версия из manifest
  является источником истины для UI/реестра; старое `self.version` не переписывается.
- `api_version`: целое `1`. Старые manifests без этого поля считаются v1.
- `author`, `license`, `description`, `homepage`: строки. Для старых плагинов
  необязательны. Авторство — заявление издателя, **не проверенная подпись**.
- `main`: Python-файл внутри папки, включая проверку разрешённых symlink-путей.
  По умолчанию `<name>.py`; для совместимости есть fallback на `__init__.py`.
- `entry`: имя класса XpiPlugin. Без него в модуле должен быть ровно один свой
  подкласс XpiPlugin. Относительные импорты `from .helpers import ...` поддерживаются.
- `platform`: `all`, `headless`, `tui`, `nvim`. CLI/REPL и стандартный RPC-каталог
  используют `headless`, curses — `tui`. Для универсальных инструментов ставьте `all`.
  Встраивающий клиент может создать `default_registry(platform="nvim")`.
- `enabled`: настоящий JSON boolean, не строка `"false"`.
- `config`: JSON-объект; доступен через `self.context.config`.
- Старое поле `hooks` принимается для совместимости. После загрузки список hooks
  определяется по фактическим переопределениям методов, а не по обещаниям manifest.

Включение/выключение сохраняется в manifest атомарной заменой файла.
Некорректный manifest, импорт или setup попадают в `error`, не останавливая соседей.

## Минимальный инструмент

```python
import asyncio
from xli.xpi import XpiPlugin, Param, ToolResult, tool

class Plugin(XpiPlugin):
    def on_load(self, context):
        self.greeting = self.context.config.get("greeting", "Привет")

    def tools(self):
        @tool(
            "hello",
            "Поздороваться с пользователем",
            [Param("name", type="string", required=True)],
            mutates=False,
            tags=["greetings"],
        )
        async def hello(name):
            return ToolResult.success(
                {"message": f"{self.greeting}, {name}!"},
                summary=f"Приветствие для {name}",
            )
        return [hello]
```

Локальное имя `hello` публикуется как `my-plugin.hello`. Нельзя заменить встроенный
инструмент или зарегистрировать повторное имя. `tools()` вызывается после `on_load`.
Можно вернуть экземпляры `Tool`, в том числе результаты декоратора `@tool`.

`Param` поддерживает типы `string`, `integer`, `number`, `boolean`, `object`, `array`,
описание, required, enum, default. Проверяются обязательные значения, объявленные
типы и enum. Это **не полная реализация JSON Schema**: вложенную структуру проверяйте
в инструменте. `default` описывает схему; задавайте Python-default в сигнатуре функции.

Функция возвращает JSON-совместимые данные или `ToolResult`. Для ожидаемой ошибки:

```python
return ToolResult.failure("Файл не найден")
```

Исключение тоже становится ошибкой инструмента. Не печатайте в stdout/stderr:
верните данные/summary или используйте `self.logger`.

### Асинхронность и права

Инструменты могут быть `async def`. Синхронные функции исполняются на потоке агента,
поэтому блокирующий I/O выносите явно:

```python
text = await asyncio.to_thread(path.read_text, encoding="utf-8")
```

Учтите: отмена ожидания `to_thread` не останавливает сам поток. Для длительных
операций используйте отменяемые async API и собственные таймауты.

Объявляйте `mutates=True` для записи, удаления, изменения внешних сервисов и запуска
команд с побочными эффектами. В readonly такие инструменты запрещены; confirm
требует согласия, auto разрешает с учётом deny-правил. Сам инструмент не должен
обходить ToolRegistry. Это контроль действий агента, **не песочница для Python**:
недоверенный плагин может выполнить произвольный код уже при импорте.

## Жизненный цикл

`on_load` вызывается менеджером один раз на загрузку, **до** регистрации tools.
`on_unload` вызывается при disable, reload, unload_all и reset менеджера.
Освобождайте открытые файлы/соединения; не рассчитывайте на cleanup после kill/crash.
При частично неудачном setup менеджер тоже пытается вызвать cleanup.

Хуки v1 синхронные, короткие; async hook диагностируется как ошибка, а не теряется
неожиданно. Асинхронная работа поддерживается в инструментах, не в lifecycle hooks.

| Hook | Данные context |
| --- | --- |
| on_load | plugin, config |
| on_agent_start | task, max_steps, mode |
| on_tool_call | name, args |
| on_tool_result | name, ok, summary |
| on_agent_end | ok, summary, steps, stopped_reason |
| on_unload | пустой объект |
| on_tui_mount / on_nvim_attach / on_headless_start | контекст, переданный frontend/embedder |

Обычные agent hooks доставляются автоматически через менеджер, связанный с реестром.
Каждый получает отдельную копию context: это события наблюдения, не способ изменить
аргументы чужого вызова. Возвращаемые значения собираются в DispatchReport;
для вывода ответа модели используйте tools, не возврат из hook.
`on_agent_end` описывает штатное завершение; при отмене/аварийном завершении не гарантирован.

```python
report = manager.dispatch("on_tool_call", platform="tui", name="read", args={})
# report.called, report.errors, report.results
```

`xli plugins dispatch <hook>` — ручной диагностический вызов, не автоматическая
регистрация. Ручной `dispatch on_load` повторно вызовет hook — обычно нужен reload.

## Состояние и конфигурация

```python
self.context.config.get("greeting", "Привет")
self.context.set_state("last_result", {"ok": True})
value = self.context.get_state("last_result", {})
self.context.delete_state("last_result")
```

Состояние хранится в `xpi_state.json` с префиксом `plugin:<name>:`. Чужие ключи не
пересекаются случайно, значения копируются. Только JSON-совместимые данные;
ключи API/секреты сюда не сохраняйте. Используйте переменные окружения для секретов.
Текущая версия state store не предоставляет межпроцессных транзакций: для
конкурентных счётчиков/больших данных используйте свою SQLite-базу с блокировками.
Namespacing — удобство, не изоляция доступа между доверенными плагинами.

Измените `config` в manifest и вызовите reload. Редактирование конфигурации не
изменяет уже созданный instance до перезагрузки.

## Reload, отладка, тесты

```bash
xli plugins reload my-plugin
xli plugins info my-plugin --json
XLI_LOG_LEVEL=DEBUG xli plugins list
```

Reload удаляет старые инструменты из всех связанных реестров и загружает новые.
Относительные модули и их bytecode-кеш сбрасываются, включая правки одинаковой длины
в пределах одной секунды. Неудачный reload оставляет плагин без рабочих tools;
ошибка видна в `info`. Это не транзакционный rollback на старую версию.

CLI reload работает в **текущем процессе команды**. Чтобы обновить уже работающий
RPC kernel, используйте `plugins.reload`; для открытого TUI перезапустите его.
Не подразумевается магическая перезагрузка всех процессов.

Для unit-тестов используйте временную папку и `XpiManager.reset()` до/после теста,
`ToolRegistry(policy=Policy(...))` и `manager.attach_registry(registry)`.
`FakeProvider` позволяет проверить весь agent loop без ключей и сети.
Пример интеграционных тестов: `tests/test_terminal_plugin_regressions.py`.

## Публикация и авторство

Сохраните автора, версию, ссылку на исходники, лицензию и текст LICENSE в пакете.
Не присваивайте себе чужой код: сохраняйте copyright/notice и условия зависимостей.
Сгенерированное поле license — декларация, не автоматически созданный текст лицензии.
XLI не скачивает зависимости, не выполняет install-скрипты, не проверяет подписи и
не выдаёт значок «проверенный автор». Пока нет удалённого marketplace/автообновления:
пользователь вручную копирует проверенную папку в `xpi/`.
