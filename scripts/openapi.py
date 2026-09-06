#!/usr/bin/env python3
"""Write the OpenAPI schema to stdout, for client type generation.

The service does not serve /openapi.json: that would be an unauthenticated route handing out
the whole route surface (main.py). Generating types is a build step, not a runtime feature.

    uv run --project api python scripts/openapi.py > web/openapi.json
"""

import json
import os
import sys

os.environ.setdefault("SESSION_SECRET", "schema-generation-only")

from oneledger.main import app  # noqa: E402  — after the env it needs to import

json.dump(app.openapi(), sys.stdout, indent=2)
