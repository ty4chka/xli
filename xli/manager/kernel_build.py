#!/usr/bin/env python3
"""
XLI Kernel Build — optional Cython acceleration for xli.core.

`xli kernel build` compiles the core modules into C extensions under
`xli/_ckernel/`. Nothing about the rest of the system depends on that having
happened: the import hook in `xli.accel` prefers a compiled module when one is
present *and* still matches its source, and otherwise transparently uses the
pure-Python module. A stale or half-built kernel can therefore never make the
agent misbehave — it just runs slower.

Why not just always compile?
  * a checkout must be runnable with no toolchain at all
  * Cython's pure-Python mode rejects some valid Python, so a module that
    fails to compile must not take the others down with it

Preflight
---------
`preflight()` checks the toolchain before any work starts and returns a
structured verdict with a copy-pasteable fix. Compiling without headers is the
single most common failure (`Python.h: No such file or directory`) and its
error message is useless unless you already know what it means, so we detect it
up front and say exactly which package to install.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import sysconfig
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from collections.abc import Sequence

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
CORE_DIR = PACKAGE_ROOT / "core"
CKERNEL_DIR = PACKAGE_ROOT / "_ckernel"
MANIFEST = CKERNEL_DIR / "manifest.json"
BUILD_DIR = CKERNEL_DIR / "build"

#: Modules that must never be compiled: they are imported during interpreter
#: startup or by the build itself, and a bad .so there is unrecoverable.
NEVER_COMPILE = frozenset({"__init__"})


# ------------------------------------------------------------------ diagnostics
@dataclass(slots=True)
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail, "fix": self.fix}


@dataclass(slots=True)
class Preflight:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def missing(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]

    def report(self) -> str:
        lines = []
        for check in self.checks:
            mark = "ok" if check.ok else "MISSING"
            lines.append(f"  [{mark:>7}] {check.name}: {check.detail}")
            if not check.ok and check.fix:
                lines.append(f"            fix: {check.fix}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "checks": [c.to_dict() for c in self.checks]}


def _package_manager_hint() -> str:
    """Best guess at the command that installs CPython headers on this box."""
    if shutil.which("apt-get"):
        return f"sudo apt-get install -y python{sys.version_info.major}.{sys.version_info.minor}-dev"
    if shutil.which("dnf"):
        return "sudo dnf install -y python3-devel"
    if shutil.which("yum"):
        return "sudo yum install -y python3-devel"
    if shutil.which("pacman"):
        return "sudo pacman -S --needed python"
    if shutil.which("brew"):
        return "brew install python"
    if shutil.which("zypper"):
        return "sudo zypper install -y python3-devel"
    return "install the python3 development headers for your platform"


def preflight() -> Preflight:
    """Verify the toolchain can actually produce an extension module."""
    result = Preflight()

    try:
        import Cython

        result.checks.append(
            Check("cython", True, f"Cython {Cython.__version__}", "pip install Cython")
        )
    except ImportError:
        result.checks.append(
            Check("cython", False, "Cython is not installed", "pip install Cython")
        )

    cc = os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if cc:
        result.checks.append(Check("compiler", True, cc, _package_manager_hint()))
    else:
        result.checks.append(
            Check("compiler", False, "no C compiler on PATH", _package_manager_hint())
        )

    include = Path(sysconfig.get_paths()["include"])
    header = include / "Python.h"
    if header.exists():
        result.checks.append(Check("python-headers", True, str(header)))
    else:
        result.checks.append(
            Check(
                "python-headers",
                False,
                f"Python.h not found in {include}",
                _package_manager_hint(),
            )
        )

    try:
        import setuptools  # noqa: F401

        result.checks.append(Check("setuptools", True, f"setuptools {setuptools.__version__}"))
    except ImportError:
        result.checks.append(
            Check("setuptools", False, "setuptools is not installed", "pip install setuptools")
        )

    if not CKERNEL_DIR.parent.is_dir():
        result.checks.append(
            Check("package", False, f"{PACKAGE_ROOT} is not a directory", "reinstall xli")
        )
    else:
        result.checks.append(Check("package", True, str(PACKAGE_ROOT)))

    return result


# ------------------------------------------------------------------- discovery
def source_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def discover_targets(only: Sequence[str] | None = None) -> list[Path]:
    """Every core module eligible for compilation, newest source first."""
    found = [
        p
        for p in sorted(CORE_DIR.glob("*.py"))
        if p.stem not in NEVER_COMPILE
    ]
    if only:
        wanted = {name.replace(".py", "") for name in only}
        found = [p for p in found if p.stem in wanted]
    return found


# -------------------------------------------------------------------- manifest
@dataclass
class Manifest:
    built_at: str = ""
    python: str = ""
    cython: str = ""
    modules: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls) -> Manifest:
        if not MANIFEST.exists():
            return cls()
        try:
            raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls()
        return cls(
            built_at=raw.get("built_at", ""),
            python=raw.get("python", ""),
            cython=raw.get("cython", ""),
            modules=raw.get("modules", {}),
        )

    def save(self) -> None:
        CKERNEL_DIR.mkdir(parents=True, exist_ok=True)
        MANIFEST.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "built_at": self.built_at,
            "python": self.python,
            "cython": self.cython,
            "modules": self.modules,
        }


def _python_tag() -> str:
    """ABI tag an extension must match to be loadable by this interpreter."""
    return f"cp{sys.version_info.major}{sys.version_info.minor}-{sys.platform}"


def is_usable(name: str, manifest: Manifest | None = None) -> bool:
    """True if a compiled module exists and still matches its Python source."""
    manifest = manifest or Manifest.load()
    entry = manifest.modules.get(name)
    if not entry:
        return False
    if entry.get("python") != _python_tag():
        return False  # built against a different interpreter — must not load

    source = CORE_DIR / f"{name}.py"
    if not source.exists():
        return False
    if entry.get("sha256") != source_hash(source):
        return False  # source changed since the build; the .so is stale

    return bool(list(CKERNEL_DIR.glob(f"{name}.*.so")) or list(CKERNEL_DIR.glob(f"{name}.pyd")))


# ------------------------------------------------------------------------ build
@dataclass
class BuildReport:
    ok: bool
    built: list[str] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    seconds: float = 0.0
    preflight: Preflight | None = None
    log: str = ""

    def summary(self) -> str:
        head = (
            f"built {len(self.built)} module(s) in {self.seconds:.1f}s"
            if self.ok
            else f"build failed ({len(self.failed)} module(s) errored)"
        )
        parts = [head]
        if self.built:
            parts.append("  built:   " + ", ".join(self.built))
        if self.skipped:
            parts.append("  skipped: " + ", ".join(self.skipped))
        for failure in self.failed:
            parts.append(f"  FAILED:  {failure['module']}: {failure['error']}")
        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "built": self.built,
            "failed": self.failed,
            "skipped": self.skipped,
            "seconds": round(self.seconds, 3),
            "preflight": self.preflight.to_dict() if self.preflight else None,
        }


def build(
    targets: Sequence[str] | None = None,
    *,
    force: bool = False,
    jobs: int | None = None,
    verbose: bool = False,
) -> BuildReport:
    """Compile the requested core modules into xli/_ckernel/.

    A module that fails to compile is reported and skipped; the rest still
    build. That matters because Cython's pure-Python mode is stricter than
    CPython, and one awkward module should not block the other thirty.
    """
    started = time.perf_counter()
    report = BuildReport(ok=True)
    report.preflight = preflight()

    if not report.preflight.ok:
        report.ok = False
        report.failed.append(
            {
                "module": "*",
                "error": "toolchain incomplete: "
                + "; ".join(f"{c.name} ({c.detail})" for c in report.preflight.missing),
            }
        )
        report.seconds = time.perf_counter() - started
        return report

    sources = discover_targets(targets)
    if not sources:
        report.ok = False
        report.failed.append({"module": "*", "error": "no matching modules found"})
        report.seconds = time.perf_counter() - started
        return report

    manifest = Manifest.load()
    todo: list[Path] = []
    for source in sources:
        if not force and is_usable(source.stem, manifest):
            report.skipped.append(source.stem)
            continue
        todo.append(source)

    if not todo:
        report.seconds = time.perf_counter() - started
        return report

    CKERNEL_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    import Cython
    from setuptools import Extension, setup
    from Cython.Build import cythonize

    extensions = [
        Extension(f"xli._ckernel.{source.stem}", [str(source)]) for source in todo
    ]

    argv_backup = sys.argv[:]
    log_chunks: list[str] = []
    try:
        cythonized = cythonize(
            extensions,
            compiler_directives={"language_level": "3"},
            build_dir=str(BUILD_DIR),
            quiet=not verbose,
            nthreads=jobs or os.cpu_count() or 1,
        )
        sys.argv = [
            "xli-kernel-build",
            "build_ext",
            "--build-lib", str(CKERNEL_DIR),
            "--build-temp", str(BUILD_DIR / "temp"),
        ]
        setup(
            name="xli-ckernel",
            ext_modules=cythonized,
            script_args=sys.argv[1:],
        )
    except Exception as exc:  # noqa: BLE001 - compiler output is unstructured
        log_chunks.append(f"{type(exc).__name__}: {exc}")
        report.ok = False
        for source in todo:
            report.failed.append({"module": source.stem, "error": str(exc)})
        report.log = "\n".join(log_chunks)
        report.seconds = time.perf_counter() - started
        return report
    finally:
        sys.argv = argv_backup

    manifest.built_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    manifest.python = _python_tag()
    manifest.cython = Cython.__version__

    for source in todo:
        if is_usable(source.stem, Manifest.load()):
            report.built.append(source.stem)
            continue
        # The .so exists but the manifest does not know about it yet.
        if list(CKERNEL_DIR.glob(f"{source.stem}.*.so")):
            manifest.modules[source.stem] = {
                "sha256": source_hash(source),
                "python": _python_tag(),
                "built_at": manifest.built_at,
            }
            report.built.append(source.stem)
        else:
            report.failed.append(
                {"module": source.stem, "error": "compiler finished but no .so was produced"}
            )
            report.ok = False

    manifest.save()
    report.seconds = time.perf_counter() - started
    return report


def clean() -> dict[str, Any]:
    """Remove every build artefact, returning what was deleted."""
    removed: list[str] = []
    if CKERNEL_DIR.exists():
        for path in sorted(CKERNEL_DIR.rglob("*")):
            if path.is_file():
                removed.append(str(path.relative_to(PACKAGE_ROOT)))
        shutil.rmtree(CKERNEL_DIR, ignore_errors=True)
    # Cython leaves generated .c files next to the sources.
    for stray in CORE_DIR.glob("*.c"):
        stray.unlink(missing_ok=True)
        removed.append(str(stray.relative_to(PACKAGE_ROOT)))
    return {"removed": len(removed), "files": removed[:50]}


def status() -> dict[str, Any]:
    """What is compiled, what is stale, and what would happen on the next build."""
    manifest = Manifest.load()
    sources = discover_targets()
    modules: list[dict[str, Any]] = []

    for source in sources:
        entry = manifest.modules.get(source.stem, {})
        if is_usable(source.stem, manifest):
            state = "compiled"
        elif entry:
            state = "stale" if entry.get("python") == _python_tag() else "wrong-interpreter"
        else:
            state = "source-only"
        modules.append({"module": source.stem, "state": state, "sha256": source_hash(source)[:12]})

    compiled = sum(1 for m in modules if m["state"] == "compiled")
    return {
        "kernel_dir": str(CKERNEL_DIR),
        "python_tag": _python_tag(),
        "built_at": manifest.built_at,
        "cython": manifest.cython,
        "total": len(modules),
        "compiled": compiled,
        "stale": sum(1 for m in modules if m["state"] == "stale"),
        "source_only": sum(1 for m in modules if m["state"] == "source-only"),
        "modules": modules,
        "preflight_ok": preflight().ok,
    }
