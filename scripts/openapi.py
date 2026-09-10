#!/usr/bin/env python3
"""Write the OpenAPI schema to stdout, for client type generation.

The service does not serve /openapi.json: that would be an unauthenticated route handing out
the whole route surface (main.py). Generating types is a build step, not a runtime feature.

`web/scripts/generate-api-types.mjs` runs this into a scratch file and generates
`web/types/api/` from it, so the schema itself is never committed:

    cd web && npm run api:types        # regenerate
    cd web && npm run api:types:check   # fail if the committed types are stale
"""

import json
import os
import sys

os.environ.setdefault("SESSION_SECRET", "schema-generation-only")

from oneledger.main import app  # noqa: E402  — after the env it needs to import

json.dump(app.openapi(), sys.stdout, indent=2)
