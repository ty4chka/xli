#!/usr/bin/env python3
"""
XLI Skills Manager v4 — SQLite FTS, lazy load, 200+ skills
"""

import sqlite3
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from xli.core.logger import StructuredLogger

logger = StructuredLogger("xli.skills")

SKILLS_DIR = Path.home() / ".xli" / "skills"
SKILLS_DB = Path.home() / ".xli" / "skills.db"


class SkillsManager:
    """Manages skills with SQLite FTS indexing"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._scan_skills()

        logger.log_structured("INFO", "skills", "SkillsManager initialized")

    def _init_db(self):
        """Initialize SQLite with FTS4"""
        conn = sqlite3.connect(str(SKILLS_DB))
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS skills (
                id INTEGER PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                path TEXT NOT NULL,
                category TEXT,
                content TEXT,
                size INTEGER,
                modified TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS skills_fts USING fts4(
                name, content, category
            )
        """)

        conn.commit()
        conn.close()

    def _scan_skills(self):
        """Scan skills directory and update index"""
        conn = sqlite3.connect(str(SKILLS_DB))
        cursor = conn.cursor()

        cursor.execute("SELECT name, path, modified FROM skills")
        existing = {row[0]: (row[1], row[2]) for row in cursor.fetchall()}

        current_skills = set()
        if SKILLS_DIR.exists():
            for skill_file in SKILLS_DIR.rglob("*.md"):
                if skill_file.name.startswith(".") or skill_file.name.startswith("_"):
                    continue

                rel_path = str(skill_file.relative_to(SKILLS_DIR))
                name = rel_path.replace("/", "_").replace("\\", "_").replace(".md", "")
                current_skills.add(name)

                mtime = datetime.fromtimestamp(skill_file.stat().st_mtime)

                if name in existing:
                    old_path, old_mtime = existing[name]
                    if old_path == str(skill_file) and old_mtime == mtime.isoformat():
                        continue

                try:
                    content = skill_file.read_text(encoding="utf-8")
                    category = str(skill_file.parent.relative_to(SKILLS_DIR)) if skill_file.parent != SKILLS_DIR else "core"

                    cursor.execute("""
                        INSERT OR REPLACE INTO skills (name, path, category, content, size, modified)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (name, str(skill_file), category, content, len(content), mtime))

                    cursor.execute("""
                        INSERT OR REPLACE INTO skills_fts (name, content, category)
                        VALUES (?, ?, ?)
                    """, (name, content, category))

                    logger.log_structured("DEBUG", "skills", f"Indexed: {name}")

                except Exception as e:
                    logger.log_error("skills", f"Failed to index {skill_file}", exc=e)

        for name in existing:
            if name not in current_skills:
                cursor.execute("DELETE FROM skills WHERE name = ?", (name,))
                cursor.execute("DELETE FROM skills_fts WHERE name = ?", (name,))
                logger.log_structured("DEBUG", "skills", f"Removed: {name}")

        conn.commit()
        conn.close()

    def load_skill(self, name: str) -> Optional[str]:
        """Lazy load skill content by name"""
        conn = sqlite3.connect(str(SKILLS_DB))
        cursor = conn.cursor()
        cursor.execute("SELECT content FROM skills WHERE name = ?", (name,))
        row = cursor.fetchone()
        conn.close()

        if row:
            return row[0]
        return None

    def search_skills(self, query: str, limit: int = 10) -> List[Tuple[str, str, float]]:
        """Full-text search skills (FTS4 compatible, no rank)"""
        conn = sqlite3.connect(str(SKILLS_DB))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT s.name, s.category, 0.0 as score
                FROM skills_fts sf
                JOIN skills s ON sf.name = s.name
                WHERE skills_fts MATCH ?
                LIMIT ?
            """, (query, limit))
            results = [(row[0], row[1], row[2]) for row in cursor.fetchall()]
        except sqlite3.OperationalError:
            cursor.execute("""
                SELECT name, category, 0.0 as score
                FROM skills
                WHERE name LIKE ? OR content LIKE ?
                LIMIT ?
            """, (f"%{query}%", f"%{query}%", limit))
            results = [(row[0], row[1], row[2]) for row in cursor.fetchall()]

        conn.close()
        logger.log_structured("DEBUG", "skills", f"Search '{query[:30]}': {len(results)} results")
        return results

    def get_skills_context(self, agent_name: str, max_skills: int = 6) -> str:
        """Get relevant skills context for agent"""
        keywords = {
            "CODER": ["python", "code", "file", "script", "function", "class", "import", "pip", "git", "bash"],
            "DEBUGGER": ["error", "debug", "bug", "fix", "test", "trace", "exception", "logging", "pytest"],
            "OPTIMIZER": ["optimize", "performance", "speed", "memory", "fast", "profile", "refactor", "efficient"],
            "PLANNER": ["plan", "architecture", "design", "structure", "module", "component"],
            "REVIEWER": ["review", "quality", "standard", "pattern", "clean", "solid"]
        }

        agent_keywords = keywords.get(agent_name.upper(), ["code"])

        all_results = []
        for kw in agent_keywords[:3]:
            results = self.search_skills(kw, limit=5)
            for name, category, score in results:
                all_results.append((abs(score) if score else 0, name, category))

        seen = set()
        unique_results = []
        for score, name, category in sorted(all_results, reverse=True):
            if name not in seen:
                seen.add(name)
                unique_results.append((score, name, category))

        selected = unique_results[:max_skills]

        if not selected:
            return ""

        result = "\n\n📚 **Available Skills:**\n"
        for score, name, category in selected:
            content = self.load_skill(name)
            if content:
                short = content[:600] + "..." if len(content) > 600 else content
                result += f"\n### {name} ({category})\n{short}\n"

        return result

    def add_skill(self, path: Path) -> bool:
        """Add new skill file and index it"""
        if not path.exists() or not path.suffix == ".md":
            return False

        try:
            dest = SKILLS_DIR / path.name
            dest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            self._scan_skills()
            logger.log_structured("INFO", "skills", f"Added skill: {path.name}")
            return True
        except Exception as e:
            logger.log_error("skills", f"Failed to add skill {path}", exc=e)
            return False

    def list_skills(self) -> List[Tuple[str, str, int]]:
        """List all skills with metadata"""
        conn = sqlite3.connect(str(SKILLS_DB))
        cursor = conn.cursor()
        cursor.execute("SELECT name, category, size FROM skills ORDER BY category, name")
        results = [(row[0], row[1], row[2]) for row in cursor.fetchall()]
        conn.close()
        return results

    def rebuild_index(self):
        """Force rebuild FTS index"""
        logger.log_structured("INFO", "skills", "Rebuilding index")
        conn = sqlite3.connect(str(SKILLS_DB))
        cursor = conn.cursor()
        cursor.execute("DELETE FROM skills_fts")
        cursor.execute("SELECT name, content, category FROM skills")
        for row in cursor.fetchall():
            cursor.execute("INSERT INTO skills_fts (name, content, category) VALUES (?, ?, ?)", row)
        conn.commit()
        conn.close()
        logger.log_structured("INFO", "skills", "Index rebuilt")


def get_skills_manager() -> SkillsManager:
    """Get singleton SkillsManager"""
    return SkillsManager()
