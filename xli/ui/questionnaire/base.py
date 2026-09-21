#!/usr/bin/env python3
"""
XLI Questionnaire Base — Question dataclass
"""

from dataclasses import dataclass, field


@dataclass
class Question:
    """Single question definition"""
    id: str
    question: str
    type: str = "text"  # text, choice, confirm
    options: list[str] | None = field(default_factory=list)
    default: str | None = None
    required: bool = True

