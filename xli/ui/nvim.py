#!/usr/bin/env python3
"""
xli/ui/nvim.py — Neovim UI Adapter
Специфика Neovim: RPC через pynvim, floating windows, Lua callbacks, LSP интеграция
"""

import asyncio
import json
import sys
import os
import re
import traceback
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field
from datetime import datetime

from xli.ui.base import XliUI, UIEvent, UIState
from xli.core.env import get_env_adapter
from xli.core.logger import get_logger

logger = get_logger("ui.nvim")


@dataclass
class NvimConfig:
    """Конфигурация Neovim UI"""
    float_width: int = 100
    float_height: int = 30
    float_border: str = "rounded"
    split_direction: str = "vsplit"  # vsplit / split
    filetype: str = "markdown"
    use_telescope: bool = True
    use_notify: bool = True
    timeout: int = 3000
    
    # Цветовая схема для XLI буферов
    highlight_groups: Dict[str, str] = field(default_factory=lambda: {
        "XliTitle": "guifg=#61afef gui=bold",
        "XliSuccess": "guifg=#98c379",
        "XliError": "guifg=#e06c75",
        "XliWarning": "guifg=#e5c07b",
        "XliInfo": "guifg=#56b6c2",
        "XliCode": "guifg=#abb2bf guibg=#282c34",
        "XliBorder": "guifg=#61afef",
    })


class NvimUI(XliUI):
    """
    Neovim UI Adapter — работает через pynvim RPC
    
    Features:
    - Floating windows с закруглёнными границами
    - Split buffers для результатов
    - vim.notify интеграция
    - Telescope picker для истории
    - Async операции без блокировки Neovim
    - Lua callback регистрация
    """
    
    def __init__(self, config: Optional[NvimConfig] = None):
        super().__init__()
        self.config = config or NvimConfig()
        self.env = get_env_adapter()
        self.nvim = None
        self._buffers: Dict[str, int] = {}  # name -> bufnr
        self._windows: Dict[str, int] = {}  # name -> winid
        self._callbacks: Dict[str, Callable] = {}
        self._lua_functions: Dict[str, str] = {}  # name -> lua code
        self._connected = False
        self._setup_highlight_groups()
        
    def _setup_highlight_groups(self):
        """Регистрирует highlight groups в Neovim"""
        if not self.is_available():
            return
        for group, attrs in self.config.highlight_groups.items():
            try:
                self.nvim.command(f"highlight {group} {attrs}")
            except Exception as e:
                logger.debug(f"Failed to set highlight {group}: {e}")
    
    def is_available(self) -> bool:
        """Проверяет доступность Neovim соединения"""
        if self.nvim is not None:
            try:
                _ = self.nvim.api.buf_get_name(1)  # Ping
                return True
            except Exception:
                self.nvim = None
                self._connected = False
        
        # Пробуем подключиться
        try:
            import pynvim
            listen = os.environ.get('NVIM_LISTEN_ADDRESS')
            if listen and Path(listen).exists():
                self.nvim = pynvim.attach('socket', path=listen)
                self._connected = True
                logger.info(f"Connected to Neovim via {listen}")
                return True
            elif 'NVIM' in os.environ:
                self.nvim = pynvim.attach('child', argv=sys.argv)
                self._connected = True
                logger.info("Connected to Neovim via child")
                return True
        except Exception as e:
            logger.debug(f"Neovim connection failed: {e}")
        
        return False
    
    def get_name(self) -> str:
        return "neovim"
    
    def get_capabilities(self) -> Dict[str, bool]:
        return {
            "rich_display": True,
            "interactive": True,
            "floating_windows": True,
            "split_buffers": True,
            "notifications": self.config.use_notify and self.is_available(),
            "file_editing": True,
            "shell_execution": True,
            "async_operations": True,
            "lua_integration": True,
        }
    
    # ─── XliUI abstract interface (required by base.XliUI) ───
    # NvimUI predates the shared XliUI ABC and grew its own richer,
    # differently-named methods (ask_input, ask_choice, show_progress,
    # display_result, ...). These thin wrappers satisfy the abstract
    # interface so NvimUI can actually be instantiated — without them
    # `NvimUI()` raises TypeError since XliUI's abstractmethods
    # (display, input, notify, open_buffer, progress, clear, choice,
    # confirm) were never overridden with matching signatures.

    def display(self, text: str, title: Optional[str] = None) -> None:
        self.open_float(title or "XLI", text.split("\n"), title=title)

    async def input(self, prompt: str, default: str = "") -> str:
        result = self.ask_input(prompt, default)
        return result if result is not None else default

    def open_buffer(self, content: List[str], name: str = "XLI",
                     filetype: str = "markdown") -> None:
        self.create_buffer(name, content, filetype=filetype)

    def progress(self, percent: int, message: str = "") -> None:
        self.show_progress(message, percent)

    def clear(self) -> None:
        self.clear_progress()

    def choice(self, question: str, options: List[str],
               default: Optional[int] = None) -> int:
        result = self.ask_choice(question, options, default_idx=default or 0)
        return result if result is not None else (default or 0)

    def confirm(self, question: str) -> bool:
        idx = self.ask_choice(question, ["Yes", "No"], default_idx=0)
        return idx == 0

    # ─── Notifications ───
    
    def notify(self, message: str, level: str = "info", title: str = "XLI") -> bool:
        """Отправляет уведомление через vim.notify"""
        if not self.is_available():
            print(f"[{level.upper()}] {message}")
            return False
        
        try:
            level_map = {"info": 2, "warn": 3, "warning": 3, "error": 4, "debug": 1}
            nvim_level = level_map.get(level, 2)
            
            if self.config.use_notify:
                self.nvim.call('vim.notify', message, nvim_level, {
                    'title': f'🔥 {title}',
                    'timeout': self.config.timeout,
                    'icon': '⚡' if level == "info" else '⚠️' if level in ("warn", "warning") else '❌' if level == "error" else '🔍'
                })
            else:
                self.nvim.command(f'echohl {"WarningMsg" if level in ("warn", "warning") else "ErrorMsg" if level == "error" else "None"}')
                self.nvim.command(f'echom "[XLI] {message.replace(chr(39), chr(39)+chr(39))}"')
                self.nvim.command('echohl None')
            
            logger.debug(f"Notify: [{level}] {message[:100]}")
            return True
        except Exception as e:
            logger.error(f"notify() failed: {e}")
            print(f"[{level.upper()}] {message}")
            return False
    
    # ─── Buffer Management ───
    
    def create_buffer(self, name: str, content: List[str] = None,
                      filetype: str = None, scratch: bool = True) -> Optional[int]:
        """Создаёт именованный буфер"""
        if not self.is_available():
            return None
        
        try:
            buf = self.nvim.api.create_buf(False, True)
            buf.name = f"xli://{name}"
            
            if scratch:
                buf.options['buftype'] = 'nofile'
                buf.options['bufhidden'] = 'hide'
                buf.options['swapfile'] = False
            
            buf.options['filetype'] = filetype or self.config.filetype
            
            if content:
                buf[:] = content
            
            self._buffers[name] = buf.number
            logger.debug(f"Buffer created: {name} (bufnr={buf.number})")
            return buf.number
        except Exception as e:
            logger.error(f"create_buffer failed: {e}")
            return None
    
    def update_buffer(self, name: str, content: List[str], append: bool = False,
                      scroll_to_end: bool = True) -> bool:
        """Обновляет содержимое буфера"""
        if not self.is_available():
            return False
        
        bufnr = self._buffers.get(name)
        if bufnr is None:
            bufnr = self.create_buffer(name, content)
            return bufnr is not None
        
        try:
            buf = self.nvim.buffers[bufnr]
            if append and buf[:]:
                current = buf[:]
                buf[:] = current + content
            else:
                buf[:] = content
            
            if scroll_to_end:
                # Прокручиваем все окна с этим буфером вниз
                for win in self.nvim.windows:
                    if win.buffer.number == bufnr:
                        win.cursor = (len(buf[:]), 0)
            
            return True
        except Exception as e:
            logger.error(f"update_buffer failed: {e}")
            return False
    
    def open_split(self, name: str, content: List[str] = None,
                   direction: str = None, focus: bool = True) -> Optional[int]:
        """Открывает split с буфером"""
        if not self.is_available():
            # Fallback: печать в stdout
            if content:
                print(f"\\n=== {name} ===")
                print("\\n".join(content))
            return None
        
        try:
            bufnr = self._buffers.get(name)
            if bufnr is None:
                bufnr = self.create_buffer(name, content)
                if bufnr is None:
                    return None
            
            split_cmd = direction or self.config.split_direction
            self.nvim.command(split_cmd)
            
            win = self.nvim.current.window
            win.buffer = self.nvim.buffers[bufnr]
            win.options['wrap'] = True
            win.options['cursorline'] = True
            win.options['number'] = False
            win.options['relativenumber'] = False
            
            if not focus:
                self.nvim.command('wincmd p')  # Вернуться в предыдущее окно
            
            self._windows[name] = win.number
            logger.debug(f"Split opened: {name} (win={win.number})")
            return win.number
        except Exception as e:
            logger.error(f"open_split failed: {e}")
            return None
    
    def open_float(self, name: str, content: List[str], title: str = None,
                   width: int = None, height: int = None,
                   focus: bool = True, close_key: str = "q") -> Optional[int]:
        """Открывает floating window"""
        if not self.is_available():
            print(f"\\n=== {title or name} ===")
            print("\\n".join(content))
            return None
        
        try:
            bufnr = self._buffers.get(name)
            if bufnr is None:
                bufnr = self.create_buffer(name, content, filetype=self.config.filetype)
                if bufnr is None:
                    return None
            else:
                self.update_buffer(name, content, append=False)
            
            buf = self.nvim.buffers[bufnr]
            
            # Рассчитываем размеры
            editor_w = self.nvim.options['columns']
            editor_h = self.nvim.options['lines']
            
            w = width or self.config.float_width
            h = height or self.config.float_height
            w = min(w, editor_w - 4)
            h = min(h, editor_h - 4)
            
            row = (editor_h - h) // 2
            col = (editor_w - w) // 2
            
            # Добавляем заголовок
            display_title = title or name
            border_title = f" {display_title} "
            
            win = self.nvim.api.open_win(buf, focus, {
                'relative': 'editor',
                'row': row,
                'col': col,
                'width': w,
                'height': h,
                'style': 'minimal',
                'border': self.config.float_border,
                'title': border_title,
                'title_pos': 'center',
                'zindex': 50,
            })
            
            # Клавиша закрытия
            if close_key:
                self.nvim.api.buf_set_keymap(bufnr, 'n', close_key,
                    ':q<CR>', {'noremap': True, 'silent': True})
            
            # ESC для закрытия
            self.nvim.api.buf_set_keymap(bufnr, 'n', '<Esc>',
                ':q<CR>', {'noremap': True, 'silent': True})
            
            self._windows[name] = win
            logger.debug(f"Float opened: {name} (win={win})")
            return win
        except Exception as e:
            logger.error(f"open_float failed: {e}")
            return None
    
    def close_window(self, name: str) -> bool:
        """Закрывает окно по имени"""
        if not self.is_available():
            return False
        
        winid = self._windows.get(name)
        if winid:
            try:
                self.nvim.api.win_close(winid, True)
                del self._windows[name]
                return True
            except Exception as e:
                logger.debug(f"close_window failed: {e}")
        return False
    
    # ─── Content Display ───
    
    def display_result(self, result: Dict[str, Any], title: str = "XLI Result") -> bool:
        """Отображает результат агента в красивом формате"""
        lines = self._format_result(result, title)
        return self.open_float(f"result_{title}", lines, title=title) is not None
    
    def _format_result(self, result: Dict[str, Any], title: str) -> List[str]:
        """Форматирует результат для отображения"""
        lines = [
            f"🔥 {title}",
            "═" * 60,
            "",
        ]
        
        # Статус
        success = result.get("success", False)
        status_icon = "✅" if success else "❌"
        lines.append(f"{status_icon} Status: {'SUCCESS' if success else 'FAILED'}")
        lines.append("")
        
        # Агенты
        for agent in ["planner", "coder", "debugger", "tester", "optimizer", "reviewer"]:
            if agent in result and result[agent]:
                icon = "📝" if agent == "coder" else "🐛" if agent == "debugger" else "⚡" if agent == "optimizer" else "🔍"
                lines.append(f"{icon} {agent.upper()}")
                lines.append("─" * 40)
                content = str(result[agent])
                # Обрезаем длинный вывод
                if len(content) > 2000:
                    content = content[:2000] + "\\n... (truncated)"
                for line in content.split("\\n"):
                    lines.append(f"  {line}")
                lines.append("")
        
        # Финальный результат
        if "final" in result:
            lines.append("📦 FINAL OUTPUT")
            lines.append("─" * 40)
            final = str(result["final"])
            if len(final) > 3000:
                final = final[:3000] + "\\n... (truncated, use file output for full content)"
            for line in final.split("\\n"):
                lines.append(line)
        
        return lines
    
    def display_stream_chunk(self, agent_name: str, chunk: str, 
                             buffer_name: str = "xli_stream") -> bool:
        """Отображает чанк стриминга в реальном времени"""
        if buffer_name not in self._buffers:
            # Первый чанк — создаём float
            lines = [
                f"⚡ Streaming: {agent_name}",
                "═" * 60,
                "",
                chunk,
            ]
            self.open_float(buffer_name, lines, title=f"XLI — {agent_name}", 
                          height=20, focus=False)
        else:
            # Добавляем чанк
            buf = self.nvim.buffers[self._buffers[buffer_name]]
            current = buf[:]
            # Добавляем построчно
            for line in chunk.split("\\n"):
                current.append(line)
            buf[:] = current
            
            # Авто-прокрутка
            for win in self.nvim.windows:
                if win.buffer.number == self._buffers[buffer_name]:
                    win.cursor = (len(buf[:]), 0)
        
        return True
    
    def close_stream(self, buffer_name: str = "xli_stream") -> bool:
        """Закрывает стриминговое окно"""
        return self.close_window(buffer_name)
    
    # ─── Progress & Status ───
    
    def show_progress(self, message: str, percent: int = None) -> bool:
        """Показывает прогресс через notify или statusline"""
        if percent is not None:
            bar = "█" * (percent // 5) + "░" * (20 - percent // 5)
            msg = f"[{bar}] {percent}% — {message}"
        else:
            msg = f"⏳ {message}"
        
        self.notify(msg, "info", title="Progress")
        
        # Также обновляем statusline если возможно
        if self.is_available():
            try:
                self.nvim.command(f'lua vim.opt.statusline = "{msg[:50]}"')
            except Exception:
                pass
        
        return True
    
    def clear_progress(self) -> bool:
        """Очищает прогресс"""
        if self.is_available():
            try:
                self.nvim.command('lua vim.opt.statusline = nil')
            except Exception:
                pass
        return True
    
    # ─── Input / Questionnaire ───
    
    def ask_input(self, prompt: str, default: str = "") -> Optional[str]:
        """Запрашивает ввод через vim.ui.input"""
        if not self.is_available():
            return input(f"{prompt}: ").strip() or default
        
        try:
            # Используем vim.ui.input если доступен
            lua_code = f"""
                local co = coroutine.running()
                local result = nil
                vim.ui.input({{ prompt = '{prompt.replace(chr(39), chr(39)+chr(39))}: ', default = '{default.replace(chr(39), chr(39)+chr(39))}' }}, function(input)
                    result = input
                    if co then coroutine.resume(co) end
                end)
                if co then coroutine.yield() end
                return result
            """
            result = self.nvim.exec_lua(lua_code)
            return result
        except Exception as e:
            logger.error(f"ask_input failed: {e}")
            # Fallback
            return input(f"{prompt}: ").strip() or default
    
    def ask_choice(self, prompt: str, options: List[str], 
                   default_idx: int = 0) -> Optional[int]:
        """Запрашивает выбор через vim.ui.select"""
        if not self.is_available():
            print(f"\\n{prompt}")
            for i, opt in enumerate(options, 1):
                print(f"  {i}. {opt}")
            try:
                choice = input("Select: ").strip()
                return int(choice) - 1
            except Exception:
                return default_idx
        
        try:
            lua_code = f"""
                local co = coroutine.running()
                local result = {default_idx}
                local opts = {{{', '.join(f'"{opt}"' for opt in options)}}}
                vim.ui.select(opts, {{ prompt = '{prompt.replace(chr(39), chr(39)+chr(39))}' }}, function(choice, idx)
                    result = idx - 1
                    if co then coroutine.resume(co) end
                end)
                if co then coroutine.yield() end
                return result
            """
            result = self.nvim.exec_lua(lua_code)
            return result
        except Exception as e:
            logger.error(f"ask_choice failed: {e}")
            return default_idx
    
    def show_questionnaire(self, questions: List[Dict[str, Any]]) -> Dict[str, str]:
        """Показывает интерактивный опросник в Neovim.

        Was importing xli.ui.questionnaire.nvim.NvimQuestionnaire, a module
        that doesn't exist anywhere in the project — calling this would
        raise ModuleNotFoundError. Reimplemented directly on top of the
        ask_input/ask_choice primitives already implemented on this class,
        which need no extra module.

        Each item in `questions` is expected to look like:
            {"key": "framework", "prompt": "Which framework?",
             "options": ["Django", "Flask"]}   # options optional -> free text
        """
        answers: Dict[str, str] = {}
        for q in questions:
            key = q.get("key") or q.get("name") or str(len(answers))
            prompt = q.get("prompt") or q.get("question") or key
            options = q.get("options")
            if options:
                idx = self.ask_choice(prompt, options, default_idx=q.get("default_idx", 0))
                answers[key] = options[idx] if idx is not None and 0 <= idx < len(options) else ""
            else:
                answers[key] = self.ask_input(prompt, default=q.get("default", "")) or ""
        return answers
    
    # ─── File Operations ───
    
    def open_file(self, path: str, line: int = None, 
                  command: str = "edit") -> bool:
        """Открывает файл в Neovim"""
        if not self.is_available():
            logger.warning(f"Cannot open file (no nvim): {path}")
            return False
        
        try:
            escaped = path.replace("\\", "\\\\").replace('"', '\\"')
            self.nvim.command(f'{command} {escaped}')
            if line:
                self.nvim.command(f'{line}')
            return True
        except Exception as e:
            logger.error(f"open_file failed: {e}")
            return False
    
    def write_to_file(self, path: str, content: str) -> bool:
        """Записывает файл через Neovim API"""
        if not self.is_available():
            # Fallback: прямое создание
            try:
                p = Path(path)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding='utf-8')
                return True
            except Exception as e:
                logger.error(f"write_to_file fallback failed: {e}")
                return False
        
        try:
            # Используем nvim_buf_set_lines для атомарной записи
            escaped_path = path.replace('"', '\\"')
            lines = content.split("\\n")
            lua_code = f"""
                local lines = {{{', '.join(f'"{line.replace(chr(39), chr(39)+chr(39))}"' for line in lines)}}}
                local buf = vim.fn.bufadd("{escaped_path}")
                vim.fn.bufload(buf)
                vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
                vim.api.nvim_buf_call(buf, function()
                    vim.cmd("write!")
                end)
                return true
            """
            self.nvim.exec_lua(lua_code)
            self.notify(f"File written: {Path(path).name}", "info")
            return True
        except Exception as e:
            logger.error(f"write_to_file failed: {e}")
            return False
    
    # ─── Shell & Commands ───
    
    def run_shell(self, command: str, timeout: int = 30) -> str:
        """Выполняет shell команду через Neovim"""
        from xli.core.shell_safety import is_shell_command_safe
        safe, reason = is_shell_command_safe(command)
        if not safe:
            logger.warning(f"Blocked unsafe command ({reason}): {command}")
            return f"ERROR: Blocked ({reason}): {command}"

        if not self.is_available():
            # Fallback: subprocess
            from xli.core.exec_guard import run_guarded_shell
            try:
                result = run_guarded_shell(command, timeout=timeout)
                return result.stdout + result.stderr
            except Exception as e:
                return f"Error: {e}"
        
        try:
            # Используем jobstart для async выполнения
            output = []
            
            def on_output(_, data, event):
                if data:
                    output.extend(data)
            
            job_id = self.nvim.call('jobstart', command, {
                'on_stdout': on_output,
                'on_stderr': on_output,
                'on_exit': lambda *args: None,
            })
            
            # Ждём завершения (с таймаутом)
            import time
            start = time.time()
            while time.time() - start < timeout:
                status = self.nvim.call('jobwait', [job_id], 100)
                if status[0] != -1:
                    break
                time.sleep(0.1)
            
            return "\\n".join(output)
        except Exception as e:
            logger.error(f"run_shell failed: {e}")
            return f"Error: {e}"
    
    # ─── Lua Integration ───
    
    def exec_lua(self, code: str) -> Any:
        """Выполняет Lua код в Neovim"""
        if not self.is_available():
            logger.warning("exec_lua skipped: not connected")
            return None
        
        try:
            return self.nvim.exec_lua(code)
        except Exception as e:
            logger.error(f"exec_lua failed: {e}")
            return None
    
    def register_lua_callback(self, name: str, python_callback: Callable) -> bool:
        """Регистрирует Python callback доступный из Lua"""
        self._callbacks[name] = python_callback
        
        # Создаём Lua функцию-прокси
        lua_code = f"""
            _G._xli_callbacks = _G._xli_callbacks or {{}}
            _G._xli_callbacks["{name}"] = function(...)
                local args = {{...}}
                -- Вызываем Python через RPC
                vim.rpcnotify(1, "xli_callback", "{name}", unpack(args))
            end
        """
        return self.exec_lua(lua_code) is not None
    
    # ─── Telescope Integration ───
    
    def telescope_picker(self, items: List[Dict[str, str]], 
                         on_select: Callable[[Dict], None],
                         title: str = "XLI Picker") -> bool:
        """Показывает Telescope picker"""
        if not self.is_available():
            return False
        
        try:
            # Проверяем доступность Telescope
            has_telescope = self.nvim.call('exists', ':Telescope')
            if not has_telescope:
                logger.warning("Telescope not available")
                return False
            
            # Формируем Lua код для picker
            items_lua = []
            for item in items:
                display = item.get("display", item.get("name", str(item)))
                value = json.dumps(item)
                items_lua.append(f'{{ display = "{display}", value = {value} }}')
            
            lua_code = f"""
                local pickers = require("telescope.pickers")
                local finders = require("telescope.finders")
                local conf = require("telescope.config").values
                local actions = require("telescope.actions")
                local action_state = require("telescope.actions.state")
                
                local items = {{{', '.join(items_lua)}}}
                
                pickers.new({{}}, {{
                    prompt_title = "{title}",
                    finder = finders.new_table({{
                        results = items,
                        entry_maker = function(entry)
                            return {{
                                value = entry.value,
                                display = entry.display,
                                ordinal = entry.display,
                            }}
                        end,
                    }}),
                    sorter = conf.generic_sorter({{}}),
                    attach_mappings = function(prompt_bufnr, map)
                        actions.select_default:replace(function()
                            actions.close(prompt_bufnr)
                            local selection = action_state.get_selected_entry()
                            -- Отправляем выбор обратно в Python
                            vim.rpcnotify(1, "xli_telescope_select", selection.value)
                        end)
                        return true
                    end,
                }}):find()
            """
            self.exec_lua(lua_code)
            return True
        except Exception as e:
            logger.error(f"telescope_picker failed: {e}")
            return False
    
    # ─── LSP Integration ───
    
    def lsp_request(self, method: str, params: Dict) -> Any:
        """Отправляет LSP запрос"""
        if not self.is_available():
            return None
        
        try:
            lua_code = f"""
                local clients = vim.lsp.get_active_clients()
                if #clients == 0 then return nil end
                
                local result = nil
                local done = false
                
                vim.lsp.buf_request(0, "{method}", {json.dumps(params)}, function(err, res)
                    if not err then
                        result = res
                    end
                    done = true
                end)
                
                -- Ждём ответа (blocking)
                vim.wait(5000, function() return done end)
                return result
            """
            return self.exec_lua(lua_code)
        except Exception as e:
            logger.error(f"lsp_request failed: {e}")
            return None
    
    # ─── Event Handling ───
    
    def handle_event(self, event: UIEvent) -> bool:
        """Обрабатывает UI события"""
        if event.type == "agent_start":
            agent = event.data.get("agent", "unknown")
            self.notify(f"🚀 {agent} started...", "info")
            self.show_progress(f"Running {agent}...")
        
        elif event.type == "agent_complete":
            agent = event.data.get("agent", "unknown")
            success = event.data.get("success", False)
            self.notify(f"{'✅' if success else '❌'} {agent} complete", 
                       "info" if success else "error")
        
        elif event.type == "agent_error":
            agent = event.data.get("agent", "unknown")
            error = event.data.get("error", "Unknown error")
            self.notify(f"❌ {agent} error: {error[:100]}", "error")
        
        elif event.type == "stream_chunk":
            agent = event.data.get("agent", "")
            chunk = event.data.get("chunk", "")
            self.display_stream_chunk(agent, chunk)
        
        elif event.type == "stream_end":
            self.close_stream()
        
        elif event.type == "mcp_call":
            server = event.data.get("server", "")
            tool = event.data.get("tool", "")
            self.notify(f"🔧 MCP: {server}.{tool}", "info")
        
        elif event.type == "file_created":
            path = event.data.get("path", "")
            self.notify(f"📄 Created: {Path(path).name}", "info")
        
        elif event.type == "task_complete":
            result = event.data.get("result", {})
            self.display_result(result)
            self.clear_progress()
        
        return True
    
    # ─── Cleanup ───
    
    def cleanup(self) -> bool:
        """Очищает все окна и буферы"""
        if not self.is_available():
            return False
        
        try:
            for name in list(self._windows.keys()):
                self.close_window(name)
            
            for name, bufnr in list(self._buffers.items()):
                try:
                    self.nvim.api.buf_delete(bufnr, {'force': True})
                except Exception:
                    pass
            
            self._buffers.clear()
            self._windows.clear()
            return True
        except Exception as e:
            logger.error(f"cleanup failed: {e}")
            return False
    
    def __del__(self):
        """Деструктор — cleanup при уничтожении"""
        try:
            self.cleanup()
        except Exception:
            pass


# ─── Factory ───

def create_nvim_ui(config: Optional[Dict] = None) -> NvimUI:
    """Фабричная функция для создания NvimUI"""
    nvim_config = NvimConfig(**config) if config else NvimConfig()
    return NvimUI(nvim_config)
