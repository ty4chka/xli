#!/usr/bin/env python3
"""
XLI Questionnaire Base — Question dataclass
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Question:
    """Single question definition"""
    id: str
    question: str
    type: str = "text"  # text, choice, confirm
    options: Optional[List[str]] = field(default_factory=list)
    default: Optional[str] = None
    required: bool = True

