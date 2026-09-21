#!/usr/bin/env python3
"""
xli/ui/questionnaire/nvim.py — Neovim Questionnaire
Специфика Neovim: интерактивный буфер, vim.ui.input, vim.ui.select
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass

from xli.ui.questionnaire.base import Question
from xli.core.logger import get_logger

logger = get_logger("ui.questionnaire.nvim")


@dataclass
class NvimQuestionnaire:
    """Neovim-based interactive questionnaire"""

    nvim_ui: Any  # NvimUI instance

    def run(self, questions: List[Question]) -> Dict[str, str]:
        """Run questionnaire in Neovim"""
        if not self.nvim_ui or not self.nvim_ui.is_available():
            logger.warning("Neovim not available, falling back to terminal")
            from xli.ui.questionnaire.terminal import TerminalQuestionnaire
            return TerminalQuestionnaire().run(questions)

        answers = {}

        for q in questions:
            logger.debug(f"Question: {q.question}")

            if q.options:
                # Multiple choice via vim.ui.select
                idx = self.nvim_ui.ask_choice(q.question, q.options, 0)
                if idx is not None and 0 <= idx < len(q.options):
                    answers[q.id] = q.options[idx]
                else:
                    answers[q.id] = q.default
            else:
                # Text input via vim.ui.input
                answer = self.nvim_ui.ask_input(q.question, q.default or "")
                answers[q.id] = answer if answer else q.default

        logger.info(f"Questionnaire complete: {len(answers)} answers")
        return answers

    def run_async(self, questions: List[Question]) -> Dict[str, str]:
        """Async version for use with async UI"""
        return self.run(questions)
