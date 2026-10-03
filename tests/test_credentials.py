"""
Default credentials: the .env file in ComfyUI's user folder and the BYTEPLUS_*
variables (nodes/credentials.py), the routes behind Settings > BytePlus
(nodes/credentials_routes.py), and the optional client input of the nodes.

Needs a ComfyUI checkout and a Python env with torch and the BytePlus SDK:
  COMFYUI_ROOT=/path/to/ComfyUI python -m unittest tests.test_credentials
The BytePlus API is never called; key validation is faked.
"""
import asyncio
import importlib
import inspect
import json
import os
import stat
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace
from unittest import mock

COMFY_ROOT = os.environ.get("COMFYUI_ROOT")
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
requires_comfyui = unittest.skipUnless(
    COMFY_ROOT, "Set COMFYUI_ROOT to a ComfyUI checkout to run these tests."
)

if COMFY_ROOT:
    if COMFY_ROOT not in sys.path:
        sys.path.insert(0, COMFY_ROOT)
    import utils  # noqa: F401, E402  (ComfyUI's utils package, before anything shadows it)
    import comfy.cli_args  # noqa: E402

    comfy.cli_args.args.cpu = True

    PACKAGE_NAME = "byteplus_plugin_test"
    if PACKAGE_NAME not in sys.modules:
        package = types.ModuleType(PACKAGE_NAME)
        package.__path__ = [PLUGIN_ROOT]
        sys.modules[PACKAGE_NAME] = package


    credentials = importlib.import_module(f"{PACKAGE_NAME}.nodes.credentials")
    credentials_routes = importlib.import_module(f"{PACKAGE_NAME}.nodes.credentials_routes")
    nodes_shared = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_shared")
    core_style = importlib.import_module(f"{PACKAGE_NAME}.nodes.core_style")
    nodes_speech = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_speech")
    nodes_mediakit = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_mediakit")
    nodes_assets = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_assets")
    nodes_seedream = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_seedream")
    nodes_seedance1 = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_seedance1")
    nodes_seedance2 = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_seedance2")
    nodes_seed = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_seed")
    nodes_video = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_video")
    BytePlusException = nodes_shared.BytePlusException


def setUpModule():
    # Hide the tester's own BYTEPLUS_* variables and user/.env (see tests/support.py).
    if COMFY_ROOT:
        from tests.support import isolate_credentials

        unittest.addModuleCleanup(isolate_credentials())


def constants_base(region):
    return importlib.import_module(f"{PACKAGE_NAME}.nodes.constants").REGION_BASE_URLS[region]


CREDENTIAL_ENV_VARS = (
    "BYTEPLUS_API_KEY", "BYTEPLUS_REGION", "BYTEPLUS_SEED_SPEECH_API_KEY",
    "BYTEPLUS_VOD_MEDIAKIT_API_KEY", "BYTEPLUS_ACCESS_KEY", "BYTEPLUS_ACCESSKEY",
    "BYTEPLUS_SECRET_KEY", "BYTEPLUS_SECRETKEY", "BYTEPLUS_SESSION_TOKEN",
)


class IsolatedCredentials(unittest.TestCase):
    """A temporary user folder and no BYTEPLUS_* variables, whatever the machine has set."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env_path = os.path.join(self.tmp.name, "user", ".env")
        patches = [
            mock.patch.object(credentials, "env_file_path", lambda: self.env_path),
            mock.patch.dict(os.environ, {}, clear=False),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        for name in CREDENTIAL_ENV_VARS:
            os.environ.pop(name, None)
        credentials._read_cache.update(stamp=None, values={})

    def write_env(self, text):
        os.makedirs(os.path.dirname(self.env_path), exist_ok=True)
        with open(self.env_path, "w", encoding="utf-8") as file:
            file.write(text)


@requires_comfyui
class TestIsolationTests(unittest.TestCase):
    def test_every_module_that_loads_the_pack_hides_real_credentials(self):
        # A tester's own user/.env (Settings > BytePlus) must never reach the tests.
        tests_dir = os.path.dirname(os.path.abspath(__file__))
        for name in sorted(os.listdir(tests_dir)):
            if not (name.startswith("test_") and name.endswith(".py")):
                continue
            with open(os.path.join(tests_dir, name), encoding="utf-8") as file:
                source = file.read()
            if 'PACKAGE_NAME}.nodes.' in source:
                with self.subTest(module=name):
                    self.assertIn("def setUpModule():", source)
                    self.assertIn("unittest.addModuleCleanup(isolate_credentials())", source)

    def test_the_real_user_folder_is_not_read(self):
        import folder_paths

        real = os.path.realpath(folder_paths.get_user_directory())
        self.assertFalse(os.path.realpath(credentials.env_file_path()).startswith(real + os.sep))
        for name in CREDENTIAL_ENV_VARS:
            self.assertNotIn(name, os.environ)


@requires_comfyui
class EnvFileTests(IsolatedCredentials):
    def test_parse(self):
        text = (
            "# comment\n\nBYTEPLUS_API_KEY=abc123  \nexport BYTEPLUS_REGION=eu-west-1\n"
            'QUOTED="has # hash and \\"quotes\\""\nSINGLE=\'a b\'\nTRAILING=value # note\nbroken line\n=novalue\n'
        )
        self.assertEqual(
            credentials.parse_env_text(text),
            {
                "BYTEPLUS_API_KEY": "abc123",
                "BYTEPLUS_REGION": "eu-west-1",
                "QUOTED": 'has # hash and "quotes"',
                "SINGLE": "a b",
                "TRAILING": "value",
            },
        )

    def test_environment_wins_over_the_file(self):
        self.write_env("BYTEPLUS_API_KEY=from-file\n")
        self.assertEqual(credentials.get_setting("BYTEPLUS_API_KEY"), "from-file")
        self.assertEqual(credentials.setting_source("BYTEPLUS_API_KEY"), "file")
        os.environ["BYTEPLUS_API_KEY"] = "from-env"
        self.assertEqual(credentials.get_setting("BYTEPLUS_API_KEY"), "from-env")
        self.assertEqual(credentials.setting_source("BYTEPLUS_API_KEY"), "environment")
        os.environ["BYTEPLUS_API_KEY"] = "   "  # blank counts as unset
        self.assertEqual(credentials.get_setting("BYTEPLUS_API_KEY"), "from-file")

    def test_nothing_set(self):
        self.assertEqual(credentials.get_setting("BYTEPLUS_API_KEY"), "")
        self.assertIsNone(credentials.setting_source("BYTEPLUS_API_KEY"))
        self.assertIsNone(credentials.get_asset_credentials())

    def test_file_changes_are_picked_up_without_a_restart(self):
        self.write_env("BYTEPLUS_API_KEY=one\n")
        self.assertEqual(credentials.get_setting("BYTEPLUS_API_KEY"), "one")
        self.write_env("BYTEPLUS_API_KEY=second-key\n")
        self.assertEqual(credentials.get_setting("BYTEPLUS_API_KEY"), "second-key")
        os.remove(self.env_path)
        self.assertEqual(credentials.get_setting("BYTEPLUS_API_KEY"), "")

    def test_update_keeps_other_lines_and_is_private(self):
        self.write_env("# my notes\nOTHER=keep me\nexport BYTEPLUS_API_KEY=old\n")
        credentials.update_env_file({"BYTEPLUS_API_KEY": "new key", "BYTEPLUS_REGION": "eu-west-1"})
        with open(self.env_path, encoding="utf-8") as file:
            text = file.read()
        self.assertEqual(text, '# my notes\nOTHER=keep me\nBYTEPLUS_API_KEY="new key"\nBYTEPLUS_REGION=eu-west-1\n')
        self.assertEqual(credentials.get_setting("BYTEPLUS_API_KEY"), "new key")
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(os.stat(self.env_path).st_mode), 0o600)
        # An empty value removes the variable.
        credentials.update_env_file({"BYTEPLUS_API_KEY": ""})
        self.assertEqual(credentials.get_setting("BYTEPLUS_API_KEY"), "")
        with open(self.env_path, encoding="utf-8") as file:
            self.assertEqual(file.read(), "# my notes\nOTHER=keep me\nBYTEPLUS_REGION=eu-west-1\n")

    def test_update_replaces_every_line_that_sets_the_variable(self):
        # A hand-edited file may set a variable twice (the last one wins on read) or
        # use "export<TAB>": saving must win and removing must remove all of them.
        self.write_env("BYTEPLUS_API_KEY=first\nOTHER=x\nexport\tBYTEPLUS_API_KEY=second\n")
        self.assertEqual(credentials.get_setting("BYTEPLUS_API_KEY"), "second")
        credentials.update_env_file({"BYTEPLUS_API_KEY": "new"})
        with open(self.env_path, encoding="utf-8") as file:
            self.assertEqual(file.read(), "BYTEPLUS_API_KEY=new\nOTHER=x\n")
        self.assertEqual(credentials.get_setting("BYTEPLUS_API_KEY"), "new")
        self.write_env("export\tBYTEPLUS_API_KEY=old\nBYTEPLUS_API_KEY=old2\n")
        credentials.update_env_file({"BYTEPLUS_API_KEY": ""})
        self.assertEqual(credentials.get_setting("BYTEPLUS_API_KEY"), "")
        with open(self.env_path, encoding="utf-8") as file:
            self.assertEqual(file.read(), "")

    def test_update_creates_the_folder_and_round_trips_odd_values(self):
        value = 'a "quoted" \\ value # not a comment'
        credentials.update_env_file({"BYTEPLUS_API_KEY": value})
        self.assertEqual(credentials.get_setting("BYTEPLUS_API_KEY"), value)

    def test_update_refuses_other_variables_and_multiline_values(self):
        with self.assertRaises(ValueError):
            credentials.update_env_file({"PATH": "/tmp"})
        with self.assertRaises(ValueError):
            credentials.update_env_file({"BYTEPLUS_API_KEY": "line one\nBYTEPLUS_REGION=x"})
        self.assertFalse(os.path.exists(self.env_path))

    def test_failed_write_leaves_the_file_and_no_temp_files(self):
        self.write_env("BYTEPLUS_API_KEY=keep\n")
        with mock.patch.object(credentials.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                credentials.update_env_file({"BYTEPLUS_API_KEY": "new"})
        self.assertEqual(os.listdir(os.path.dirname(self.env_path)), [".env"])
        self.assertEqual(credentials.get_setting("BYTEPLUS_API_KEY"), "keep")

    def test_a_byte_order_mark_does_not_hide_the_first_line(self):
        # Notepad saves UTF-8 with a BOM.
        with open(self._mkdir(), "w", encoding="utf-8-sig") as file:
            file.write("BYTEPLUS_API_KEY=first-line\nOTHER=x\n")
        self.assertEqual(credentials.get_setting("BYTEPLUS_API_KEY"), "first-line")
        credentials.update_env_file({"BYTEPLUS_API_KEY": "replaced"})
        with open(self.env_path, encoding="utf-8") as file:
            self.assertEqual(file.read(), "BYTEPLUS_API_KEY=replaced\nOTHER=x\n")

    def _mkdir(self):
        os.makedirs(os.path.dirname(self.env_path), exist_ok=True)
        return self.env_path

    def test_aliases_go_when_the_variable_is_written_or_removed(self):
        self.write_env("BYTEPLUS_ACCESSKEY=old-ak\nBYTEPLUS_SECRETKEY=old-sk\nKEEP=1\n")
        self.assertEqual(credentials.get_asset_credentials()["access_key"], "old-ak")
        credentials.update_env_file({"BYTEPLUS_ACCESS_KEY": "", "BYTEPLUS_SECRET_KEY": ""})
        self.assertIsNone(credentials.get_asset_credentials())
        self.write_env("BYTEPLUS_ACCESSKEY=old-ak\nBYTEPLUS_SECRETKEY=old-sk\n")
        credentials.update_env_file({"BYTEPLUS_ACCESS_KEY": "AK2", "BYTEPLUS_SECRET_KEY": "SK2"})
        with open(self.env_path, encoding="utf-8") as file:
            self.assertEqual(file.read(), "BYTEPLUS_ACCESS_KEY=AK2\nBYTEPLUS_SECRET_KEY=SK2\n")

    @unittest.skipIf(os.name != "posix", "symlinks need POSIX here")
    def test_a_symlinked_file_stays_a_link(self):
        target = os.path.join(self.tmp.name, "secrets", "byteplus.env")
        os.makedirs(os.path.dirname(target))
        with open(target, "w", encoding="utf-8") as file:
            file.write("OTHER=1\n")
        self._mkdir()
        os.symlink(target, self.env_path)
        credentials.update_env_file({"BYTEPLUS_API_KEY": "via-link"})
        self.assertTrue(os.path.islink(self.env_path))
        with open(target, encoding="utf-8") as file:
            self.assertEqual(file.read(), "OTHER=1\nBYTEPLUS_API_KEY=via-link\n")
        self.assertEqual(credentials.get_setting("BYTEPLUS_API_KEY"), "via-link")

    def test_the_new_file_is_on_disk_before_it_replaces_the_old_one(self):
        order = []
        real_fsync, real_replace = os.fsync, os.replace
        with mock.patch.object(credentials.os, "fsync", lambda fd: (order.append("fsync"), real_fsync(fd))), \
                mock.patch.object(credentials.os, "replace", lambda a, b: (order.append("replace"), real_replace(a, b))):
            credentials.update_env_file({"BYTEPLUS_API_KEY": "k"})
        self.assertEqual(order, ["fsync", "replace"])

    def test_region_and_asset_credentials(self):
        self.assertEqual(credentials.get_default_region(), "ap-southeast-1")
        self.write_env("BYTEPLUS_REGION=eu-west-1\nBYTEPLUS_ACCESS_KEY=AK\nBYTEPLUS_SECRET_KEY=SK\n")
        self.assertEqual(credentials.get_default_region(), "eu-west-1")
        self.assertEqual(
            credentials.get_asset_credentials(), {"access_key": "AK", "secret_key": "SK", "session_token": ""}
        )
        self.write_env("BYTEPLUS_REGION=mars-1\nBYTEPLUS_ACCESS_KEY=AK\n")
        self.assertEqual(credentials.get_default_region(), "ap-southeast-1")  # unknown region ignored
        self.assertIsNone(credentials.get_asset_credentials())  # a pair or nothing

    def test_status_never_contains_a_key(self):
        secret = "sk-very-secret-value-9876"
        self.write_env(f"BYTEPLUS_API_KEY={secret}\nBYTEPLUS_SECRET_KEY={secret}\nBYTEPLUS_ACCESS_KEY=AKIDEXAMPLE0001\n")
        os.environ["BYTEPLUS_SEED_SPEECH_API_KEY"] = secret
        status = credentials.credential_status()
        self.assertNotIn(secret, json.dumps(status))
        self.assertNotIn("AKIDEXAMPLE0001", json.dumps(status))
        modelark = status["credentials"]["modelark"]
        self.assertEqual((modelark["configured"], modelark["source"], modelark["hint"]), (True, "file", "ends in 9876"))
        speech = status["credentials"]["speech"]
        self.assertEqual((speech["configured"], speech["source"]), (True, "environment"))
        self.assertFalse(status["credentials"]["mediakit"]["configured"])
        self.assertTrue(status["credentials"]["iam"]["configured"])
        self.assertEqual(status["regions"], ["ap-southeast-1", "eu-west-1"])

    def test_the_path_is_shown_with_the_home_folder_as_a_tilde(self):
        home = os.path.expanduser("~")
        self.assertEqual(credentials.display_path(os.path.join(home, "ComfyUI", "user", ".env")),
                         os.path.join("~", "ComfyUI", "user", ".env"))
        self.assertEqual(credentials.display_path(home + "-other/x"), home + "-other/x")
        self.assertEqual(credentials.display_path("/opt/ComfyUI/user/.env"), "/opt/ComfyUI/user/.env")
        status = credentials.credential_status()
        self.assertEqual(status["env_file_display"], credentials.display_path(self.env_path))

    def test_an_environment_variable_that_hides_the_file_is_reported(self):
        self.write_env("BYTEPLUS_API_KEY=file-key-0001\n")
        os.environ["BYTEPLUS_API_KEY"] = "env-key-0002"
        info = credentials.credential_status()["credentials"]["modelark"]
        self.assertEqual((info["source"], info["shadowed"], info["hint"]), ("environment", True, "ends in 0002"))
        # The file's value can still be removed from Settings.
        self.assertTrue(info["in_file"])
        os.environ["BYTEPLUS_API_KEY"] = "file-key-0001"  # the same value: not shadowed, still in the file
        info = credentials.credential_status()["credentials"]["modelark"]
        self.assertEqual((info["shadowed"], info["in_file"]), (False, True))
        os.remove(self.env_path)
        self.assertFalse(credentials.credential_status()["credentials"]["modelark"]["in_file"])

    def test_in_file_counts_the_alias_spellings(self):
        self.write_env("BYTEPLUS_ACCESSKEY=AK\nBYTEPLUS_SECRETKEY=SK\n")
        iam = credentials.credential_status()["credentials"]["iam"]
        self.assertEqual((iam["configured"], iam["in_file"], iam["source"]), (True, True, "file"))


@requires_comfyui
class ClientTests(IsolatedCredentials):
    def setUp(self):
        super().setUp()
        patch = mock.patch.object(nodes_shared, "_CLIENT_CACHE", {})
        patch.start()
        self.addCleanup(patch.stop)

    def test_key_from_the_environment_or_the_file(self):
        os.environ["BYTEPLUS_API_KEY"] = "env-key"
        client = nodes_shared.get_client()
        self.assertEqual((client.api_key, client.region), ("env-key", "ap-southeast-1"))
        self.assertIsNotNone(client.ark)
        self.assertIsNot(client.ark, client.billed_ark)
        del os.environ["BYTEPLUS_API_KEY"]
        self.write_env("BYTEPLUS_API_KEY=file-key\nBYTEPLUS_REGION=eu-west-1\n")
        built = []
        with mock.patch.object(nodes_shared, "Ark", lambda **kwargs: built.append(kwargs) or SimpleNamespace(**kwargs)):
            client = nodes_shared.get_client()
        self.assertEqual((client.api_key, client.region), ("file-key", "eu-west-1"))
        # The regular client keeps the SDK's retries, the billed one has none.
        self.assertEqual(
            [(k["api_key"], k["base_url"], k.get("max_retries")) for k in built],
            [("file-key", constants_base("eu-west-1"), None), ("file-key", constants_base("eu-west-1"), 0)],
        )

    def test_the_client_is_reused_until_the_key_or_region_changes(self):
        self.write_env("BYTEPLUS_API_KEY=key-one\n")
        first = nodes_shared.get_client()
        self.assertIs(nodes_shared.get_client(), first)
        self.write_env("BYTEPLUS_API_KEY=key-two\n")
        second = nodes_shared.get_client()
        self.assertIsNot(second, first)
        self.assertEqual(second.api_key, "key-two")
        self.write_env("BYTEPLUS_API_KEY=key-two\nBYTEPLUS_REGION=eu-west-1\n")
        self.assertEqual(nodes_shared.get_client().region, "eu-west-1")

    def test_no_key_names_the_places_to_set_it(self):
        with self.assertRaises(BytePlusException) as caught:
            nodes_shared.get_client()
        message = str(caught.exception)
        for part in ("Settings > BytePlus", "BYTEPLUS_API_KEY", self.env_path):
            self.assertIn(part, message)

    def test_asset_credentials_reach_the_asset_library(self):
        self.write_env("BYTEPLUS_API_KEY=k\nBYTEPLUS_ACCESS_KEY=AK\nBYTEPLUS_SECRET_KEY=SK\n")
        client = nodes_shared.get_client()
        self.assertEqual(client.asset_credentials["secret_key"], "SK")
        self.assertEqual(nodes_assets.resolve_asset_credentials(SimpleNamespace())["access_key"], "AK")
        os.remove(self.env_path)
        with self.assertRaisesRegex(BytePlusException, "Settings > BytePlus"):
            nodes_assets.resolve_asset_credentials(SimpleNamespace())

    def test_new_asset_credentials_reach_the_cached_asset_library(self):
        # The client is cached across runs; a new AK/SK pair in Settings must not keep
        # signing asset lookups with the old one.
        self.write_env("BYTEPLUS_API_KEY=k\nBYTEPLUS_ACCESS_KEY=AK_OLD\nBYTEPLUS_SECRET_KEY=SK_OLD\n")
        client = nodes_shared.get_client()
        first = core_style._asset_library(client)
        self.assertIs(core_style._asset_library(client), first)  # reused within the same pair
        self.assertEqual(first._api.api_client.configuration.ak, "AK_OLD")
        self.write_env("BYTEPLUS_API_KEY=k\nBYTEPLUS_ACCESS_KEY=AK_NEW\nBYTEPLUS_SECRET_KEY=SK_NEW\n")
        same_client = nodes_shared.get_client()
        self.assertIs(same_client, client)
        second = core_style._asset_library(same_client)
        self.assertIsNot(second, first)
        self.assertEqual(second._api.api_client.configuration.ak, "AK_NEW")
        self.assertEqual(second._create_api.api_client.configuration.sk, "SK_NEW")
        # A new secret key alone (same access key) also counts.
        self.write_env("BYTEPLUS_API_KEY=k\nBYTEPLUS_ACCESS_KEY=AK_NEW\nBYTEPLUS_SECRET_KEY=SK_ROTATED\n")
        third = core_style._asset_library(nodes_shared.get_client())
        self.assertIsNot(third, second)
        self.assertEqual(third._api.api_client.configuration.sk, "SK_ROTATED")

    def test_speech_and_mediakit_keys(self):
        with self.assertRaisesRegex(BytePlusException, "BYTEPLUS_SEED_SPEECH_API_KEY"):
            nodes_speech.get_speech_client()
        with self.assertRaisesRegex(BytePlusException, "BYTEPLUS_VOD_MEDIAKIT_API_KEY"):
            nodes_mediakit.get_mediakit_client()
        self.write_env("BYTEPLUS_SEED_SPEECH_API_KEY=speech-key\nBYTEPLUS_VOD_MEDIAKIT_API_KEY=mk-key\n")
        self.assertEqual(nodes_speech.get_speech_client().api_key, "speech-key")
        self.assertEqual(nodes_mediakit.get_mediakit_client().api_key, "mk-key")

    def test_a_node_runs_with_the_saved_key(self):
        # Asset Library sends one ListAssets call; the fake library records which client it got.
        seen = []

        class FakeLibrary:
            def __init__(self, client):
                seen.append(client)

            def call(self, action, body):
                return {"Items": []}

        node = nodes_assets.BytePlusAssetLibrary
        node.hidden = SimpleNamespace(unique_id="1", prompt={})
        self.write_env("BYTEPLUS_API_KEY=saved-key\n")
        with mock.patch.object(nodes_assets, "AssetLibrary", FakeLibrary):
            asyncio.run(node.execute(group_type="AIGC", status="all", project_name="default"))
            self.assertEqual(seen[-1].api_key, "saved-key")
            mine = SimpleNamespace(api_key="test-key", region="ap-southeast-1")
            asyncio.run(node.execute(mine, group_type="AIGC", status="all", project_name="default"))
            self.assertIs(seen[-1], mine)
        os.remove(self.env_path)
        with self.assertRaisesRegex(BytePlusException, "No BytePlus API key"):
            asyncio.run(node.execute(group_type="AIGC", status="all", project_name="default"))


@requires_comfyui
class WithClientTests(unittest.TestCase):
    def test_injects_the_client_unless_a_test_passes_one(self):
        calls = []

        def factory():
            calls.append("built")
            return "saved-client"

        class Node:
            @classmethod
            @nodes_shared.with_client("client", factory)
            async def run(cls, client, prompt="p"):
                return client, prompt

            @classmethod
            @nodes_shared.with_client("client", factory)
            def sync(cls, client, prompt="p"):
                return client, prompt

        self.assertTrue(inspect.iscoroutinefunction(Node.run))
        self.assertFalse(inspect.iscoroutinefunction(Node.sync))
        self.assertEqual(asyncio.run(Node.run(prompt="x")), ("saved-client", "x"))
        self.assertEqual(Node.sync(prompt="y"), ("saved-client", "y"))
        self.assertEqual(calls, ["built"] * 2)
        # A client passed by keyword or position is used as is, and nothing is built.
        self.assertEqual(asyncio.run(Node.run(client="mine")), ("mine", "p"))
        self.assertEqual(asyncio.run(Node.run("mine", "z")), ("mine", "z"))
        self.assertEqual(Node.sync("mine"), ("mine", "p"))
        self.assertEqual(calls, ["built"] * 2)

    def test_a_missing_key_is_the_nodes_error(self):
        def factory():
            raise BytePlusException("no key")

        class Node:
            @classmethod
            @nodes_shared.with_client("client", factory)
            async def run(cls, client):
                return client

        with self.assertRaisesRegex(BytePlusException, "no key"):
            asyncio.run(Node.run())


ALL_NODES = [
    *nodes_seedream.NODES, *nodes_seedance1.NODES, *nodes_seedance2.NODES, *nodes_seed.NODES,
    *nodes_assets.NODES, *nodes_speech.NODES, *nodes_mediakit.NODES, nodes_video.BytePlusVideoQueryTasks,
] if COMFY_ROOT else []


@requires_comfyui
class NoClientInputTests(unittest.TestCase):
    def test_nodes_take_no_client_input_and_get_one_injected(self):
        for node_cls in ALL_NODES:
            with self.subTest(node=node_cls.__name__):
                info = node_cls.GET_NODE_INFO_V1()
                inputs = {**info["input"]["required"], **info["input"].get("optional", {})}
                self.assertFalse({"client", "speech_client", "mediakit_client"} & set(inputs))
                self.assertFalse(any("CLIENT" in str(spec[0]) for spec in inputs.values()))
                self.assertTrue(hasattr(node_cls.execute.__func__, "__wrapped__"))
                params = list(inspect.signature(node_cls.execute.__func__.__wrapped__).parameters)
                self.assertIn(params[1], ("client", "speech_client", "mediakit_client"))


@requires_comfyui
class RouteTests(IsolatedCredentials, unittest.IsolatedAsyncioTestCase):
    def request(self, body=None, headers=None, raw=None):
        async def read(limit=-1):
            if isinstance(body, Exception):
                data = b"{not json"
            else:
                data = raw if raw is not None else json.dumps(body).encode()
            return data[:limit] if limit >= 0 else data

        return SimpleNamespace(
            headers={"Host": "127.0.0.1:8188", **(headers or {})},
            content=SimpleNamespace(read=read),
        )

    async def save(self, body, **kwargs):
        response = await credentials_routes.handle_save(self.request(body, **kwargs))
        return response.status, json.loads(response.text)

    async def test_status(self):
        response = await credentials_routes.handle_status(self.request())
        self.assertEqual(response.status, 200)
        self.assertIn("credentials", json.loads(response.text))
        response = await credentials_routes.handle_status(self.request(headers={"Origin": "http://evil.example"}))
        self.assertEqual(response.status, 403)

    async def test_origin_check(self):
        same = {"Origin": "http://127.0.0.1:8188"}
        status, _ = await self.save({"credential": "speech", "value": "s-key-123456"}, headers=same)
        self.assertEqual(status, 200)
        refused = (
            {"Origin": "https://evil.example"},
            {"Origin": "null"},
            {"Origin": "https://evil.example", "X-Forwarded-Host": "comfy.example.com"},
            # Sec-Fetch-Site decides when the browser sends it, whatever Origin says.
            {"Origin": "http://127.0.0.1:8188", "Sec-Fetch-Site": "cross-site"},
            {"Origin": "https://other.example.com", "Sec-Fetch-Site": "same-site"},
        )
        for headers in refused:
            with self.subTest(headers=headers):
                status, _ = await self.save({"credential": "speech", "value": "other"}, headers=headers)
                self.assertEqual(status, 403)
        self.assertEqual(credentials.get_setting("BYTEPLUS_SEED_SPEECH_API_KEY"), "s-key-123456")

    async def test_behind_a_reverse_proxy_or_tunnel(self):
        # The browser addresses https://comfy.example.com; ComfyUI sees the upstream Host.
        for headers in (
            {"Origin": "https://comfy.example.com", "Sec-Fetch-Site": "same-origin"},
            {"Origin": "https://comfy.example.com", "X-Forwarded-Host": "comfy.example.com"},
            {"Sec-Fetch-Site": "none"},
            {},  # not from a web page (curl, local scripts)
        ):
            with self.subTest(headers=headers):
                status, _ = await self.save(
                    {"credential": "speech", "value": "proxied-key-1234"}, headers=headers
                )
                self.assertEqual(status, 200)
        response = await credentials_routes.handle_status(self.request(headers={
            "Origin": "https://comfy.example.com", "Sec-Fetch-Site": "same-origin",
        }))
        self.assertEqual(response.status, 200)

    async def test_save_and_clear_a_speech_key(self):
        status, body = await self.save({"credential": "speech", "value": "  speech-key-123456 "})
        self.assertEqual(status, 200)
        self.assertEqual(credentials.get_setting("BYTEPLUS_SEED_SPEECH_API_KEY"), "speech-key-123456")
        self.assertNotIn("speech-key-123456", json.dumps(body))
        self.assertEqual(body["credentials"]["speech"]["hint"], "ends in 3456")
        status, body = await self.save({"credential": "speech", "clear": True})
        self.assertEqual(status, 200)
        self.assertFalse(body["credentials"]["speech"]["configured"])

    async def test_modelark_key_is_checked_with_the_region_before_it_is_saved(self):
        seen = []

        def validate(key, base_url):
            seen.append((key, base_url))
            return key == "good-key-0001"

        with mock.patch.object(credentials_routes, "check_api_key", validate):
            status, body = await self.save({"credential": "modelark", "value": "bad-key", "region": "eu-west-1"})
            self.assertEqual(status, 422)
            self.assertIn("rejected", body["error"])
            self.assertEqual(credentials.get_setting("BYTEPLUS_API_KEY"), "")
            self.assertFalse(os.path.exists(self.env_path))
            status, body = await self.save({"credential": "modelark", "value": "good-key-0001", "region": "eu-west-1"})
        self.assertEqual(status, 200)
        self.assertEqual(seen[-1][0], "good-key-0001")
        self.assertIn("eu-west", seen[-1][1])
        self.assertEqual(credentials.get_setting("BYTEPLUS_API_KEY"), "good-key-0001")
        self.assertEqual(credentials.get_default_region(), "eu-west-1")
        self.assertEqual(body["region"], "eu-west-1")
        self.assertEqual(body["message"], "Saved.")

    async def test_an_unreachable_endpoint_is_not_a_rejected_key(self):
        with mock.patch.object(credentials_routes, "check_api_key", lambda key, url: None):
            status, body = await self.save({"credential": "modelark", "value": "key-0001"})
        self.assertEqual(status, 200)
        self.assertIn("could not be reached", body["message"])
        self.assertEqual(credentials.get_setting("BYTEPLUS_API_KEY"), "key-0001")

    async def test_a_region_fixed_by_the_environment_is_not_overridden(self):
        os.environ["BYTEPLUS_REGION"] = "ap-southeast-1"
        checked = []
        with mock.patch.object(credentials_routes, "check_api_key", lambda key, url: checked.append(url) or True):
            status, body = await self.save({"credential": "modelark", "value": "key-0001", "region": "eu-west-1"})
            self.assertEqual(status, 409)
            self.assertIn("BYTEPLUS_REGION", body["error"])
            self.assertEqual(checked, [])
            self.assertFalse(os.path.exists(self.env_path))
            status, body = await self.save({"credential": "modelark", "value": "key-0001", "region": "ap-southeast-1"})
        self.assertEqual(status, 200)
        self.assertEqual(body["region_source"], "environment")

    async def test_iam_pair(self):
        status, body = await self.save({"credential": "iam", "access_key": "AK"})
        self.assertEqual(status, 400)
        self.assertIn("go together", body["error"])
        status, body = await self.save({"credential": "iam", "access_key": "AK", "secret_key": "SK"})
        self.assertEqual(status, 200)
        self.assertTrue(body["credentials"]["iam"]["configured"])
        self.assertEqual(credentials.get_asset_credentials()["secret_key"], "SK")
        status, body = await self.save({"credential": "iam", "clear": True})
        self.assertIsNone(credentials.get_asset_credentials())

    async def test_a_new_or_removed_iam_pair_drops_the_old_session_token(self):
        self.write_env("BYTEPLUS_ACCESS_KEY=AK\nBYTEPLUS_SECRET_KEY=SK\nBYTEPLUS_SESSION_TOKEN=sts-token\n")
        self.assertEqual(credentials.get_asset_credentials()["session_token"], "sts-token")
        status, _ = await self.save({"credential": "iam", "access_key": "AK2", "secret_key": "SK2"})
        self.assertEqual(status, 200)
        self.assertEqual(credentials.get_asset_credentials(),
                         {"access_key": "AK2", "secret_key": "SK2", "session_token": ""})

    async def test_bad_requests(self):
        for body, expected in (
            ({"credential": "other", "value": "x"}, 400),
            ({"credential": "speech", "value": "   "}, 400),
            ({"credential": "speech"}, 400),
            ({"credential": "modelark", "value": "key-0001", "region": "mars-1"}, 400),
            ({"credential": "modelark", "value": "key-0001", "region": ["eu-west-1"]}, 400),
            (["not", "an", "object"], 400),
            (ValueError("not json"), 400),
        ):
            with self.subTest(body=body):
                status, payload = await self.save(body)
                self.assertEqual(status, expected)
                self.assertIn("error", payload)
        status, _ = await self.save({}, raw=b'{"credential": "speech", "value": "' + b"x" * (20 * 1024) + b'"}')
        self.assertEqual(status, 413)
        self.assertFalse(os.path.exists(self.env_path))

    async def test_a_value_that_cannot_be_written_is_refused(self):
        status, body = await self.save({"credential": "speech", "value": "line1\nBYTEPLUS_API_KEY=stolen"})
        self.assertEqual(status, 400)
        self.assertFalse(os.path.exists(self.env_path))
        self.assertEqual(credentials.get_setting("BYTEPLUS_API_KEY"), "")

    async def test_write_failure_is_reported_without_the_key(self):
        with mock.patch.object(credentials, "update_env_file", side_effect=PermissionError("denied")):
            status, body = await self.save({"credential": "speech", "value": "secret-value-1234"})
        self.assertEqual(status, 500)
        self.assertNotIn("secret-value-1234", json.dumps(body))

    def test_register_without_a_server_is_harmless(self):
        with mock.patch.dict(sys.modules, {"server": None}):
            self.assertFalse(credentials_routes.register())


if __name__ == "__main__":
    unittest.main()
