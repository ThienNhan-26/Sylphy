import ast
from pathlib import Path


def test_entity_package_does_not_import_legacy_sylphy_package() -> None:
    package_root = Path(__file__).parents[2] / "src" / "sylphy_entity"
    violations: list[str] = []
    for source_path in package_root.rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
                if any(name == "sylphy" or name.startswith("sylphy.") for name in names):
                    violations.append(f"{source_path}: import {names}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "sylphy" or module.startswith("sylphy."):
                    violations.append(f"{source_path}: from {module} import ...")
    assert not violations, "Entity Runtime must not depend on legacy package:\n" + "\n".join(violations)
