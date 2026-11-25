import ast
from pathlib import Path


def test_brain_and_decision_contracts_have_no_runtime_io_or_model_imports():
    root = Path(__file__).parents[2] / "src" / "sylphy_entity"
    brain_paths = list((root / "brain").rglob("*.py")) + [root / "contracts" / "decision.py"]
    forbidden_roots = {
        "asyncio", "subprocess", "sqlite3", "socket", "httpx", "requests",
        "fastapi", "uvicorn", "psutil", "ollama", "watchdog", "os", "threading",
    }
    violations: list[str] = []
    for path in brain_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".", 1)[0] in forbidden_roots:
                        violations.append(f"{path}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                root_name = module.split(".", 1)[0]
                if root_name in forbidden_roots:
                    violations.append(f"{path}: from {module} import ...")
    assert not violations, "Brain must remain a pure decision core:\n" + "\n".join(violations)
