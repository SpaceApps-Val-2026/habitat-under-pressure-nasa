"""B3: the catalog YAML becomes a validated catalog.json; a broken reference names its file and entry."""
import json
import shutil
import subprocess
import sys

import pytest
import yaml

from hup_api import catalog, contracts
from hup_data import paths


@pytest.fixture
def catalog_copy(tmp_path):
    """A writable copy of catalog/ so a test can break one file."""
    target = tmp_path / "catalog"
    shutil.copytree(paths.CATALOG_DIR, target, ignore=shutil.ignore_patterns("catalog.json", "zones"))
    return target


def edit(folder, name, change):
    file = folder / f"{name}.yaml"
    entries = yaml.safe_load(file.read_text(encoding="utf-8"))
    change(entries)
    file.write_text(yaml.safe_dump(entries, allow_unicode=True, sort_keys=False), encoding="utf-8")


def entry(entries, entry_id):
    return next(item for item in entries if item["id"] == entry_id)


def test_repository_catalog_builds_and_validates(tmp_path):
    out = tmp_path / "catalog.json"
    built = catalog.build(out=out)
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written == built
    assert contracts.errors("catalog", written) == []


def test_yaml_split_of_the_example_rebuilds_it_exactly(tmp_path):
    """The file layout loses nothing: the example catalog split per file builds back into itself."""
    example = json.loads((paths.CONTRACTS_DIR / "examples" / "catalog.json").read_text(encoding="utf-8"))
    (tmp_path / "version.txt").write_text(example["version"] + "\n", encoding="utf-8")
    for key in catalog.FILES:
        (tmp_path / f"{key}.yaml").write_text(yaml.safe_dump(example[key], allow_unicode=True), encoding="utf-8")
    assert catalog.build(catalog_dir=tmp_path) == example


def test_load_reads_the_built_file(catalog_copy):
    built = catalog.build(catalog_dir=catalog_copy)
    assert (catalog_copy / "catalog.json").is_file()
    assert catalog.load(catalog_dir=catalog_copy) == built


def test_command_writes_catalog_json(catalog_copy):
    run = subprocess.run([sys.executable, "-m", "hup_api.catalog", "build", "--dir", str(catalog_copy)],
                         capture_output=True, text=True, encoding="utf-8")
    assert run.returncode == 0, run.stderr
    assert contracts.errors("catalog", json.loads((catalog_copy / "catalog.json").read_text(encoding="utf-8"))) == []


BROKEN = [
    ("variables", lambda e: entry(e, "lst").update(sources=["no-such-source"]),
     "variables.yaml", "lst", "no-such-source"),
    ("chains", lambda e: e[0]["links"][0].update(to="no_such_variable"), "chains.yaml", "greenness_heat",
     "no_such_variable"),
    ("norms", lambda e: e[0].update(applies_to=["no_such_variable"]), "norms.yaml",
     "es-rd-35-2023-jucar-sagunt-industria-asignacion",
     "no_such_variable"),
    ("norms", lambda e: e[0]["clause"].update(document="no-such-doc"), "norms.yaml",
     "es-rd-35-2023-jucar-sagunt-industria-asignacion",
     "no-such-doc"),
    ("norms", lambda e: (e[0]["clause"].update(quote=None), e[0].update(reviewed_by="someone")), "norms.yaml",
     "es-rd-35-2023-jucar-sagunt-industria-asignacion", "quote"),
    ("blocks", lambda e: e[0]["needs"].update(operations=["no_such_op"]), "blocks.yaml", "KeyFigure", "no_such_op"),
    ("variables", lambda e: entry(e, "ndvi").pop("unit"), "variables.yaml", "ndvi", "unit"),
    ("operations", lambda e: e.append(dict(e[0])), "operations.yaml", "read_series", "duplicate"),
]


@pytest.mark.parametrize(("name", "change", "file", "entry", "detail"), BROKEN)
def test_broken_reference_names_file_and_entry(catalog_copy, name, change, file, entry, detail):
    edit(catalog_copy, name, change)
    with pytest.raises(catalog.CatalogError) as error:
        catalog.build(catalog_dir=catalog_copy)
    message = str(error.value)
    assert file in message and entry in message and detail in message
    assert not (catalog_copy / "catalog.json").exists()


def test_command_fails_on_a_broken_reference(catalog_copy):
    edit(catalog_copy, "variables", lambda e: entry(e, "lst").update(sources=["no-such-source"]))
    run = subprocess.run([sys.executable, "-m", "hup_api.catalog", "build", "--dir", str(catalog_copy)],
                         capture_output=True, text=True, encoding="utf-8")
    assert run.returncode == 1
    assert "variables.yaml" in run.stderr and "lst" in run.stderr and "no-such-source" in run.stderr
    assert not (catalog_copy / "catalog.json").exists()
