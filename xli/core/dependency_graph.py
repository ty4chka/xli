#!/usr/bin/env python3
"""
XLI Dependency Graph v4 — Import graph, execution order, breaking changes
"""

import ast
from pathlib import Path
from collections import defaultdict, deque

from xli.core.logger import StructuredLogger

logger = StructuredLogger("xli.deps")


class DependencyGraph:
    """Python import dependency graph"""

    def __init__(self, directory: str = "."):
        self.directory = Path(directory)
        self.graph: dict[str, set[str]] = defaultdict(set)
        self.reverse_graph: dict[str, set[str]] = defaultdict(set)
        self.file_modules: dict[str, str] = {}
        self._build()

    def _build(self):
        """Build import graph"""
        for py_file in self.directory.rglob("*.py"):
            if any(x in str(py_file) for x in ["venv", "__pycache__", ".git", "node_modules"]):
                continue

            module_name = self._get_module_name(py_file)
            self.file_modules[str(py_file)] = module_name

            try:
                with open(py_file, encoding="utf-8") as f:
                    tree = ast.parse(f.read())

                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            self.graph[module_name].add(alias.name)

                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            self.graph[module_name].add(node.module)

            except Exception as e:
                logger.log_error("deps", f"Parse failed: {py_file}", exc=e)

        # Build reverse graph
        for module, deps in self.graph.items():
            for dep in deps:
                self.reverse_graph[dep].add(module)

        logger.log_structured("INFO", "deps",
                             f"Graph built: {len(self.graph)} modules")

    def _get_module_name(self, path: Path) -> str:
        """Convert path to module name"""
        rel = path.relative_to(self.directory)
        return str(rel.with_suffix("")).replace("/", ".").replace("\\", ".")

    def get_execution_order(self) -> list[str]:
        """Topological sort for execution order"""
        in_degree = dict.fromkeys(self.graph, 0)
        for deps in self.graph.values():
            for dep in deps:
                if dep in in_degree:
                    in_degree[dep] += 1

        queue = deque([m for m, d in in_degree.items() if d == 0])
        order = []

        while queue:
            module = queue.popleft()
            order.append(module)

            for dependent in self.reverse_graph.get(module, []):
                if dependent in in_degree:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        queue.append(dependent)

        if len(order) != len(in_degree):
            # Cycle detected
            remaining = set(in_degree.keys()) - set(order)
            logger.log_structured("WARN", "deps",
                                 f"Cycle detected in: {remaining}")
            order.extend(sorted(remaining))

        return order

    def detect_cycles(self) -> list[tuple[str, str]]:
        """Find circular dependencies"""
        cycles = []
        visited = set()
        rec_stack = set()

        def dfs(node, path):
            visited.add(node)
            rec_stack.add(node)

            for neighbor in self.graph.get(node, []):
                if neighbor not in visited:
                    result = dfs(neighbor, path + [neighbor])
                    if result:
                        return result
                elif neighbor in rec_stack:
                    # Found cycle
                    cycle_start = path.index(neighbor)
                    return path[cycle_start:] + [neighbor]

            rec_stack.remove(node)
            return None

        for module in self.graph:
            if module not in visited:
                cycle = dfs(module, [module])
                if cycle:
                    for i in range(len(cycle) - 1):
                        cycles.append((cycle[i], cycle[i + 1]))

        logger.log_structured("INFO", "deps",
                             f"Found {len(cycles)} cycle edges")
        return cycles

    def get_affected_files(self, changed_file: str) -> list[str]:
        """Get files affected by change"""
        module = self._get_module_name(Path(changed_file))

        affected = set()
        queue = deque([module])

        while queue:
            current = queue.popleft()
            for dependent in self.reverse_graph.get(current, []):
                if dependent not in affected:
                    affected.add(dependent)
                    queue.append(dependent)

        # Convert back to files
        result = []
        for file_path, mod in self.file_modules.items():
            if mod in affected:
                result.append(file_path)

        logger.log_structured("DEBUG", "deps",
                             f"Change in {changed_file} affects {len(result)} files")
        return result

    def get_dependencies(self, module: str) -> set[str]:
        """Get direct dependencies of module"""
        return self.graph.get(module, set())

    def get_dependents(self, module: str) -> set[str]:
        """Get modules that depend on this"""
        return self.reverse_graph.get(module, set())


def get_dependency_graph(directory: str = ".") -> DependencyGraph:
    """Get DependencyGraph instance"""
    return DependencyGraph(directory)

