"""Synthetic stand-in for the client's Argus layout (source not shareable).

Kept as a dict rather than a checked-in fixture tree. Real requirements.txt /
package.json files under tests/fixtures/ would be discovered as Packages when
graph-works scans this repository itself.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

ARGUS_FILES: dict[str, str] = {
    # Root file only forwards to the backend — must not become a second identity.
    "requirements.txt": "-r backend/requirements.txt\n",
    "backend/requirements.txt": "-r requirements-base.txt\n-r requirements-heavy.txt\n",
    "backend/requirements-base.txt": "fastapi>=0.110\nuvicorn[standard]==0.30.1  # server\npydantic>=2\n",
    "backend/requirements-heavy.txt": "numpy>=1.26 \\\n    --hash=sha256:deadbeef\n",
    "backend/requirements-dev.txt": "-r requirements.txt\npytest>=8\n",
    "backend/app.py": (
        "from fastapi import FastAPI\n\napp = FastAPI()\n\n\n"
        '@app.get("/health")\ndef health() -> dict[str, str]:\n    return {"status": "ok"}\n'
    ),
    "backend/services/__init__.py": "",
    "backend/services/scoring.py": "def score(x: int) -> int:\n    return x * 2\n",
    "backend/pytest.ini": "[pytest]\ntestpaths = tests\n",
    "backend/tests/test_health.py": (
        'from app import health\n\n\ndef test_health() -> None:\n    assert health() == {"status": "ok"}\n'
    ),
    "backend/startup.sh": "#!/bin/sh\nuvicorn app:app --host 0.0.0.0\n",
    # Excluded by ARGUS_IGNORE; a nested requirements root with Python source.
    "backend/scratch/requirements.txt": "requests\n",
    "backend/scratch/probe.py": "print('probe')\n",
    "frontend/package.json": json.dumps(
        {
            "name": "argus-frontend",
            "version": "0.1.0",
            "dependencies": {"react": "^18.3.0"},
            "devDependencies": {"vite": "^5.0.0", "vitest": "^1.0.0"},
        }
    ),
    "frontend/index.html": "<!doctype html>\n",
    "frontend/src/main.ts": "export const x = 1;\n",
    "frontend/tests/app.test.ts": "import { x } from '../src/main';\n",
}

ARGUS_IGNORE: tuple[str, ...] = ("backend/scratch/**",)


def write_layout(root: Path, files: Mapping[str, str]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))
