# code-parser

Tree-sitter-backed Python package that turns source files into a span-bearing
intermediate `SourceTree`, with a graph projection aligned to the  
`code-graph-io` SQLite schema.

## Status

v1 covers Python, JavaScript, and TypeScript with a single graph projection.
`.tsx` files are parsed with the `tsx` tree-sitter grammar so JSX-bearing React
components are preserved; plain `.ts` files continue to use `typescript`.


## Quick start

```python
from pathlib import Path
from code_parser import parse_file, to_graph_records

tree = parse_file(Path("src/foo.py"), package="my-package")
records = to_graph_records(tree)
print(records.nodes)
print(records.edges)
```

## Tests

```bash
python -m unittest discover
```

