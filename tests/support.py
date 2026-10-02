"""
Shared test helpers.

isolate_credentials(): the pack reads default credentials from the BYTEPLUS_*
environment variables and from ComfyUI's user/.env (nodes/credentials.py).
A tester who saved a key in Settings > BytePlus has such a file, and without
isolation the tests would see that key: "no key" cases would fail, and code
that is meant to stop at a missing key could call the real API with it. Every
test module that loads the pack calls this from setUpModule.
"""
import importlib
import os
import tempfile
from unittest import mock

PACKAGE_NAME = "byteplus_plugin_test"
CREDENTIAL_ENV_VARS = (
    "BYTEPLUS_API_KEY", "BYTEPLUS_REGION", "BYTEPLUS_SEED_SPEECH_API_KEY",
    "BYTEPLUS_VOD_MEDIAKIT_API_KEY", "BYTEPLUS_ACCESS_KEY", "BYTEPLUS_ACCESSKEY",
    "BYTEPLUS_SECRET_KEY", "BYTEPLUS_SECRETKEY", "BYTEPLUS_SESSION_TOKEN",
)


def isolate_credentials():
    """
    Point the pack's .env at an empty temporary folder and remove the
    BYTEPLUS_* variables. Returns the function that undoes it (pass it to
    unittest.addModuleCleanup).
    """
    credentials = importlib.import_module(f"{PACKAGE_NAME}.nodes.credentials")
    folder = tempfile.TemporaryDirectory()
    patches = [
        mock.patch.object(credentials, "env_file_path", lambda: os.path.join(folder.name, ".env")),
        mock.patch.dict(os.environ),
    ]
    for patch in patches:
        patch.start()
    for name in CREDENTIAL_ENV_VARS:
        os.environ.pop(name, None)
    credentials._read_cache.update(stamp=None, values={})

    def undo():
        for patch in reversed(patches):
            patch.stop()
        credentials._read_cache.update(stamp=None, values={})
        folder.cleanup()

    return undo
