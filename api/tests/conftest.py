"""Environment these tests need before oneledger.main is imported.

The service refuses to start without SESSION_SECRET, deliberately — so the suite has to supply
one. It is a throwaway value: nothing here signs anything a real client will ever present.
"""

import os

os.environ.setdefault("SESSION_SECRET", "test-secret-not-used-outside-this-suite")
os.environ.setdefault("ALLOWED_USER_EMAIL", "owner@example.com")
