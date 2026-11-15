# catalog

The closed catalogue the engine works over. The YAML files are the source; `catalog.json` is built from them
with `python -m hup_api.catalog build` and must not be edited by hand. Shape:
[`docs/contratos/catalog.schema.json`](../docs/contratos/catalog.schema.json).
