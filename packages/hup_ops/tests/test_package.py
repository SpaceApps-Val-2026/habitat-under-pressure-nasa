import ast
from pathlib import Path

import hup_ops


def test_package_imports():
    assert hup_ops.__doc__


def test_operation_text_literals_include_english_and_spanish():
    # All limits authored by operations are text dictionaries. Dynamic text inherited from inputs is separate.
    missing = []
    for path in Path(hup_ops.__file__).parent.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for text in (node for node in ast.walk(tree) if isinstance(node, ast.Dict)):
            keys = {key.value for key in text.keys if isinstance(key, ast.Constant)}
            if keys & {"en", "es"} and not {"en", "es"} <= keys:
                missing.append(f"{path.name}:{text.lineno}")
    assert not missing, f"Missing English or Spanish operation text: {', '.join(missing)}"


def test_a_result_names_the_version_of_the_operation_that_computed_it():
    """Found with real data: results said version 1.0 after the trend and the contrast had moved to 1.1."""
    from hup_ops._series import _version
    from hup_ops.registry import REGISTRY

    assert REGISTRY["trend"].version != "1.0"
    assert all(_version(name) == operation.version for name, operation in REGISTRY.items())
