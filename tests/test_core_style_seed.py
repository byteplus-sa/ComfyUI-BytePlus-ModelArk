"""
Seed LLM (BytePlusSeed) and Seed Audio (BytePlusSeedAudio): the UI shape of
ComfyUI core's ByteDanceSeedNode / ByteDanceSeedAudioNode, this pack's
requests, core's validations, and the example workflows.

Needs a ComfyUI checkout and a Python env with torch and the BytePlus SDK:
  COMFYUI_ROOT=/path/to/ComfyUI python -m unittest tests.test_core_style_seed
"""
import base64
import importlib
import inspect
import io
import json
import os
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace

COMFY_ROOT = os.environ.get("COMFYUI_ROOT")
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW_DIR = os.path.join(PLUGIN_ROOT, "example_workflows")
requires_comfyui = unittest.skipUnless(
    COMFY_ROOT, "Set COMFYUI_ROOT to a ComfyUI checkout to run these tests."
)
SEED_MAX = 2147483647
SEED_TOOLTIP = (
    "Seed controls whether the node should re-run; results are non-deterministic regardless of seed."
)

if COMFY_ROOT:
    if COMFY_ROOT not in sys.path:
        sys.path.insert(0, COMFY_ROOT)
    import utils  # noqa: F401, E402  (ComfyUI's utils package, before anything shadows it)

    PACKAGE_NAME = "byteplus_plugin_test"
    if PACKAGE_NAME not in sys.modules:
        package = types.ModuleType(PACKAGE_NAME)
        package.__path__ = [PLUGIN_ROOT]
        sys.modules[PACKAGE_NAME] = package

    from comfy_api.latest import io as comfy_io

    constants = importlib.import_module(f"{PACKAGE_NAME}.nodes.constants")
    models_config = importlib.import_module(f"{PACKAGE_NAME}.nodes.models_config")
    nodes_shared = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_shared")
    nodes_seed = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_seed")
    nodes_visual = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_visual")
    nodes_speech = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_speech")
    speech_api = importlib.import_module(f"{PACKAGE_NAME}.nodes.speech_api")
    audio_utils = importlib.import_module(f"{PACKAGE_NAME}.nodes.audio_utils")
    voices = importlib.import_module(f"{PACKAGE_NAME}.nodes.seed_speech_voices")
    BytePlusException = nodes_shared.BytePlusException


# --- schema helpers ---------------------------------------------------------

def ordered(inputs):
    """Frontend order: required inputs first, then optional ones, each in schema order."""
    return [i for i in inputs if not i.optional] + [i for i in inputs if i.optional]


def workflow_input_names(inputs, selections, prefix=""):
    """
    Input names as saved in workflow JSON: DynamicCombo children follow their
    combo as '<combo>.<child>', Autogrow shows its first slot '<id>.<name>'.
    """
    names = []
    for item in ordered(inputs):
        name = prefix + item.id
        if isinstance(item, comfy_io.DynamicCombo.Input):
            names.append(name)
            option = next(o for o in item.options if o.key == selections[item.id])
            names += workflow_input_names(option.inputs, selections, prefix=name + ".")
        elif isinstance(item, comfy_io.Autogrow.Input):
            names.append(f"{name}.{item.template.names[0]}")
        else:
            names.append(name)
    return names


def flat_inputs(inputs, selections, prefix=""):
    """{workflow name: Input} for the selected DynamicCombo options."""
    found = {}
    for item in inputs:
        name = prefix + item.id
        found[name] = item
        if isinstance(item, comfy_io.DynamicCombo.Input):
            option = next(o for o in item.options if o.key == selections[item.id])
            found.update(flat_inputs(option.inputs, selections, prefix=name + "."))
    return found


def by_id(inputs):
    return {item.id: item for item in inputs}


def option_children(schema_input, key):
    option = next(o for o in schema_input.options if o.key == key)
    return [i.id for i in ordered(option.inputs)]


def load_workflow(name):
    with open(os.path.join(WORKFLOW_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def sdk_accepts(method, kwargs):
    """Bind request kwargs to the real SDK method (a wrong name fails here, not only live)."""
    fn = inspect.unwrap(method)
    for cell in fn.__closure__ or ():
        if inspect.isfunction(cell.cell_contents) and cell.cell_contents.__name__ == fn.__name__:
            fn = cell.cell_contents
    inspect.signature(fn).bind(None, **kwargs)


def sine_audio(seconds=1.0, sample_rate=24000, channels=1):
    import math

    import torch

    t = torch.arange(int(seconds * sample_rate)) / sample_rate
    wave = 0.5 * torch.sin(2 * math.pi * 440 * t)
    return {"waveform": wave.repeat(channels, 1)[None], "sample_rate": sample_rate}


# --- Seed LLM -----------------------------------------------------------------

@requires_comfyui
class SeedSchemaTests(unittest.TestCase):
    def setUp(self):
        self.schema = nodes_seed.BytePlusSeed.define_schema()
        self.inputs = by_id(self.schema.inputs)

    def test_registered_and_named_like_core(self):
        self.assertIn(nodes_seed.BytePlusSeed, nodes_seed.NODES)
        self.assertEqual(self.schema.node_id, "BytePlusSeed")
        self.assertEqual(self.schema.display_name, "BytePlus Seed")
        self.assertFalse(self.schema.is_api_node)

    def test_input_order_client_first_core_then_extras(self):
        self.assertEqual([i.id for i in ordered(self.schema.inputs)], [
            "client", "prompt", "model", "seed", "system_prompt",
            "detail", "fps", "reasoning_mode", "reasoning_effort", "turns", "stream", "file_expire_seconds",
        ])
        for name in ("detail", "fps", "reasoning_mode", "reasoning_effort", "turns", "stream",
                     "file_expire_seconds"):
            with self.subTest(extra=name):
                self.assertTrue(self.inputs[name].optional)
                self.assertTrue(self.inputs[name].advanced)

    def test_core_inputs(self):
        prompt = self.inputs["prompt"]
        self.assertEqual((prompt.default, prompt.multiline, prompt.optional), ("", True, False))
        self.assertEqual(prompt.tooltip, "Text input to the model.")
        seed = self.inputs["seed"]
        self.assertEqual((seed.default, seed.min, seed.max), (0, 0, SEED_MAX))
        self.assertTrue(seed.control_after_generate)
        self.assertEqual(seed.tooltip, SEED_TOOLTIP)
        system = self.inputs["system_prompt"]
        self.assertEqual((system.default, system.multiline, system.optional, system.advanced), ("", True, True, True))
        self.assertEqual(system.tooltip, "Foundational instructions that dictate the model's behavior.")

    def test_model_options_and_children(self):
        model = self.inputs["model"]
        self.assertIsInstance(model, comfy_io.DynamicCombo.Input)
        keys = [o.key for o in model.options]
        self.assertEqual(keys[:3], ["Seed 2.0 Pro", "Seed 2.0 Lite", "Seed 2.0 Mini"])
        self.assertEqual(keys, [
            "Seed 2.0 Pro", "Seed 2.0 Lite", "Seed 2.0 Mini",
            "Seed 2.1 Turbo", "Seed 1.8", "Seed 1.6", "Seed 1.6 Flash",
        ])
        for key in keys:
            with self.subTest(model=key):
                option = next(o for o in model.options if o.key == key)
                self.assertEqual([i.id for i in option.inputs], ["images", "videos", "temperature"])
                images, videos, temperature = option.inputs
                self.assertEqual(images.template.names, [f"image_{i}" for i in range(1, 21)])
                self.assertEqual(images.template.min, 0)
                self.assertEqual(videos.template.names, [f"video_{i}" for i in range(1, 5)])
                self.assertEqual(
                    (temperature.default, temperature.min, temperature.max, temperature.step, temperature.advanced),
                    (1.0, 0.0, 2.0, 0.01, True),
                )

    def test_byteplus_model_ids(self):
        self.assertEqual(models_config.SEED_LLM_MODEL_MAP, {
            "Seed 2.0 Pro": "seed-2-0-pro-260328",
            "Seed 2.0 Lite": "seed-2-0-lite-260428",
            "Seed 2.0 Mini": "seed-2-0-mini-260428",
            "Seed 2.1 Turbo": "dola-seed-2-1-turbo-260628",
            "Seed 1.8": "seed-1-8-251228",
            "Seed 1.6": "seed-1-6-250915",
            "Seed 1.6 Flash": "seed-1-6-flash-250715",
        })

    def test_outputs(self):
        outputs = self.schema.outputs
        self.assertEqual([o.io_type for o in outputs], ["STRING", "STRING"])
        self.assertIsNone(outputs[0].display_name)  # core's unnamed STRING output
        self.assertEqual(outputs[1].display_name, "raw_json")

    def test_legacy_visual_node(self):
        schema = nodes_visual.BytePlusVisualUnderstanding.define_schema()
        self.assertTrue(schema.is_deprecated)
        self.assertEqual(schema.display_name, "BytePlus Visual Understanding (Legacy)")
        self.assertEqual(schema.node_id, "BytePlusVisualUnderstanding")


class FakeResponses:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def create(self, **kwargs):
        from byteplussdkarkruntime.resources.responses.responses import Responses

        sdk_accepts(Responses.create, kwargs)
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        return SimpleNamespace(id=reply.get("id"), model_dump=lambda reply=reply: reply)


class FakeVideo:
    def __init__(self, data=b"fake mp4 bytes"):
        self.data = data
        self.formats = []

    def save_to(self, path, format=None, **_kwargs):
        self.formats.append(format)
        with open(path, "wb") as f:
            f.write(self.data)


def reply(text="An answer.", response_id="resp-1", **extra):
    return {
        "id": response_id,
        "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]},
        ],
        **extra,
    }


@requires_comfyui
class SeedRequestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.uploads = []

        async def fake_upload(client, path, fps=None, expire_seconds=604800, return_meta=False):
            with open(path, "rb") as f:
                head = f.read(3)
            self.uploads.append({"path": path, "fps": fps, "expire": expire_seconds, "head": head})
            return f"file-{len(self.uploads)}"

        self._old = (nodes_seed.upload_file_to_ark, nodes_seed._media_cache_dir)
        nodes_seed.upload_file_to_ark = fake_upload
        nodes_seed._media_cache_dir = lambda: self.tmp.name
        nodes_seed.SEED_LAST_RESPONSES.clear()
        nodes_seed.BytePlusSeed.hidden = SimpleNamespace(unique_id="7", prompt={})

    def tearDown(self):
        nodes_seed.upload_file_to_ark, nodes_seed._media_cache_dir = self._old
        nodes_seed.SEED_LAST_RESPONSES.clear()
        delattr(nodes_seed.BytePlusSeed, "hidden")
        self.tmp.cleanup()

    def client(self, *replies, api_key="ark-key", region="ap-southeast-1"):
        responses = FakeResponses(replies)
        return SimpleNamespace(api_key=api_key, region=region, ark=SimpleNamespace(responses=responses)), responses

    async def run_node(self, client, prompt="Describe it.", model=None, **kwargs):
        model = model or {"model": "Seed 2.0 Pro", "temperature": 1.0}
        return (await nodes_seed.BytePlusSeed.execute(client, prompt, model, 0, **kwargs)).result

    async def test_request_matches_core_payload_with_media(self):
        import torch

        client, responses = self.client(reply("A red square."))
        video = FakeVideo()
        text, raw = await self.run_node(
            client,
            "What is shown?",
            {
                "model": "Seed 2.0 Lite",
                "temperature": 0.3,
                "images": {"image_1": torch.zeros((2, 16, 16, 3)), "image_2": torch.ones((1, 8, 8, 3))},
                "videos": {"video_1": video},
            },
            system_prompt="Answer in one sentence.",
            detail="low",
            fps=2.0,
            file_expire_seconds=86400,
        )
        self.assertEqual(text, "A red square.")
        self.assertEqual(json.loads(raw)["id"], "resp-1")
        call, = responses.calls
        self.assertEqual(call["model"], "seed-2-0-lite-260428")
        self.assertEqual(call["instructions"], "Answer in one sentence.")
        self.assertEqual(call["temperature"], 0.3)
        self.assertIs(call["store"], False)       # like core: single-turn responses are not stored
        self.assertIs(call["stream"], False)
        self.assertNotIn("previous_response_id", call)
        self.assertNotIn("thinking", call)        # reasoning_mode auto: model default
        self.assertEqual(call["reasoning"], {"effort": "medium"})
        self.assertEqual(call["input"], [{"role": "user", "content": [
            {"type": "input_image", "file_id": "file-1", "detail": "low"},
            {"type": "input_image", "file_id": "file-2", "detail": "low"},
            {"type": "input_image", "file_id": "file-3", "detail": "low"},
            {"type": "input_video", "file_id": "file-4"},
            {"type": "input_text", "text": "What is shown?"},
        ]}])
        # Every image of each batch is uploaded as JPEG; videos as MP4 with the fps.
        self.assertEqual([u["head"] for u in self.uploads[:3]], [b"\xff\xd8\xff"] * 3)
        self.assertEqual([u["fps"] for u in self.uploads], [None, None, None, 2.0])
        self.assertEqual({u["expire"] for u in self.uploads}, {86400})
        self.assertTrue(self.uploads[3]["path"].endswith(".mp4"))
        from comfy_api.latest import Types

        self.assertEqual(video.formats, [Types.VideoContainer.MP4])

    async def test_text_only_request_omits_instructions_and_media(self):
        client, responses = self.client(reply())
        await self.run_node(client, model={"model": "Seed 2.0 Pro", "temperature": 1.0, "images": {}, "videos": {}})
        call = responses.calls[0]
        self.assertNotIn("instructions", call)
        self.assertEqual(call["model"], "seed-2-0-pro-260328")
        self.assertEqual(call["input"][0]["content"], [{"type": "input_text", "text": "Describe it."}])
        self.assertEqual(self.uploads, [])

    async def test_reasoning_options(self):
        cases = [
            ("Seed 2.1 Turbo", "enabled", "high", {"type": "enabled"}, {"effort": "high"}),
            ("Seed 2.0 Mini", "disabled", "high", {"type": "disabled"}, None),
            ("Seed 1.6 Flash", "enabled", "low", {"type": "enabled"}, None),
            ("Seed 1.8", "auto", "minimal", None, {"effort": "minimal"}),
        ]
        for label, mode, effort, thinking, reasoning in cases:
            with self.subTest(model=label, mode=mode):
                client, responses = self.client(reply())
                await self.run_node(client, model={"model": label, "temperature": 1.0},
                                    reasoning_mode=mode, reasoning_effort=effort)
                call = responses.calls[0]
                self.assertEqual(call["model"], models_config.SEED_LLM_MODEL_MAP[label])
                self.assertEqual(call.get("thinking"), thinking)
                self.assertEqual(call.get("reasoning"), reasoning)

    async def test_multi_turn_continues_with_prompt_only(self):
        import torch

        client, responses = self.client(reply("First.", "resp-a"), reply("Second.", "resp-b"), reply("New.", "resp-c"))
        model = {"model": "Seed 2.0 Pro", "temperature": 1.0, "images": {"image_1": torch.zeros((1, 8, 8, 3))}}
        await self.run_node(client, "Look.", model, turns=2, system_prompt="Be brief.")
        await self.run_node(client, "And now?", model, turns=2, system_prompt="Be brief.")
        first, second = responses.calls
        self.assertNotIn("store", first)          # stored so the next turn can continue it
        self.assertNotIn("previous_response_id", first)
        self.assertEqual(second["previous_response_id"], "resp-a")
        self.assertEqual(second["instructions"], "Be brief.")   # instructions are not carried over
        self.assertEqual(second["input"][0]["content"], [{"type": "input_text", "text": "And now?"}])
        self.assertEqual(len(self.uploads), 1)
        # turns=1 starts over and forgets the conversation
        await self.run_node(client, "Fresh.", model, turns=1)
        self.assertNotIn("previous_response_id", responses.calls[2])
        self.assertNotIn("7", nodes_seed.SEED_LAST_RESPONSES)

    async def test_conversation_is_scoped_to_key(self):
        client_a, responses_a = self.client(reply("A", "resp-a"))
        client_b, responses_b = self.client(reply("B", "resp-b"), api_key="other-key")
        await self.run_node(client_a, turns=2)
        await self.run_node(client_b, turns=2)
        self.assertNotIn("previous_response_id", responses_b.calls[0])

    async def test_output_text_joins_blocks_and_errors_raise(self):
        joined = {"id": "r", "output": [{"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": "Line 1"}, {"type": "output_text", "text": "Line 2"}]}]}
        client, _ = self.client(joined)
        text, _ = await self.run_node(client)
        self.assertEqual(text, "Line 1\nLine 2")

        bad_replies = [
            {"id": "r", "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "No."}]}]},
            {"id": "r", "error": {"code": "InvalidParameter", "message": "bad"}, "output": []},
            {"id": "r", "output": []},
        ]
        for bad in bad_replies:
            with self.subTest(reply=bad):
                client, _ = self.client(bad)
                with self.assertRaisesRegex(BytePlusException, r"^\[BytePlus\] "):
                    await self.run_node(client)

    async def test_stream_uses_executor_stream(self):
        payloads = []

        class FakeExecutor:
            def __init__(self, client):
                pass

            async def stream_response_task(self, payload, is_single_node=True):
                payloads.append((payload, is_single_node))
                return "Streamed.", json.dumps(reply("Streamed.", "resp-s"))

        old = nodes_seed.BytePlusVisualExecutor
        nodes_seed.BytePlusVisualExecutor = FakeExecutor
        try:
            client, _ = self.client()
            text, raw = await self.run_node(client, stream=True)
        finally:
            nodes_seed.BytePlusVisualExecutor = old
        self.assertEqual(text, "Streamed.")
        self.assertEqual(json.loads(raw)["id"], "resp-s")
        self.assertTrue(payloads[0][1])

    async def test_validation(self):
        import torch

        cases = [
            {"prompt": "   "},
            {"model": {"model": "Seed 9", "temperature": 1.0}},
            {"model": {"model": "Seed 2.0 Pro", "temperature": 1.0, "images": {"image_1": torch.zeros((21, 4, 4, 3))}}},
            {"model": {"model": "Seed 2.0 Pro", "temperature": 1.0,
                       "videos": {f"video_{i}": FakeVideo() for i in range(1, 6)}}},
        ]
        for kwargs in cases:
            with self.subTest(kwargs=list(kwargs)):
                client, responses = self.client(reply())
                with self.assertRaises(BytePlusException):
                    await self.run_node(client, **kwargs)
                self.assertEqual(responses.calls, [])

    async def test_video_conversion_error_is_readable(self):
        class BrokenVideo:
            def save_to(self, *_args, **_kwargs):
                raise ValueError("codec")

        client, _ = self.client(reply())
        with self.assertRaisesRegex(BytePlusException, "video_1"):
            await self.run_node(client, model={"model": "Seed 2.0 Pro", "temperature": 1.0,
                                               "videos": {"video_1": BrokenVideo()}})
        self.assertEqual(os.listdir(self.tmp.name), [])  # no partial file left behind


# --- Seed Audio -----------------------------------------------------------------

@requires_comfyui
class SeedAudioSchemaTests(unittest.TestCase):
    def setUp(self):
        self.schema = nodes_speech.BytePlusSeedAudio.define_schema()
        self.inputs = by_id(self.schema.inputs)

    def test_same_node_id_and_core_display_name(self):
        self.assertEqual(self.schema.node_id, "BytePlusSeedAudio")
        self.assertEqual(self.schema.display_name, "BytePlus Seed Audio 1.0")

    def test_input_order_client_first_core_then_extras(self):
        self.assertEqual([i.id for i in ordered(self.schema.inputs)], [
            "speech_client", "text_prompt", "reference_mode", "sample_rate", "speech_rate",
            "loudness_rate", "pitch_rate", "seed", "model",
            "audio_format", "enable_subtitle", "aigc_watermark", "aigc_metadata",
            "content_producer", "produce_id", "content_propagator", "propagate_id",
        ])
        for name in ("audio_format", "enable_subtitle", "aigc_watermark", "aigc_metadata",
                     "content_producer", "produce_id", "content_propagator", "propagate_id"):
            with self.subTest(extra=name):
                self.assertTrue(self.inputs[name].optional)
                self.assertTrue(self.inputs[name].advanced)

    def test_reference_mode_options(self):
        mode = self.inputs["reference_mode"]
        self.assertIsInstance(mode, comfy_io.DynamicCombo.Input)
        self.assertEqual([o.key for o in mode.options],
                         ["text only", "audio reference", "image reference", "preset voice"])
        self.assertEqual(option_children(mode, "text only"), [])
        self.assertEqual(option_children(mode, "audio reference"), [
            "reference_audio_1", "reference_audio_2", "reference_audio_3",
            "ref_audio_1_source", "ref_audio_2_source", "ref_audio_3_source",
        ])
        self.assertEqual(option_children(mode, "image reference"), ["reference_image", "ref_image_url"])
        self.assertEqual(option_children(mode, "preset voice"), ["preset_voice"])
        audio_option = by_id(next(o for o in mode.options if o.key == "audio reference").inputs)
        for n in (1, 2, 3):
            self.assertEqual(audio_option[f"reference_audio_{n}"].io_type, "AUDIO")
            self.assertTrue(audio_option[f"reference_audio_{n}"].optional)
            self.assertTrue(audio_option[f"ref_audio_{n}_source"].advanced)
        image_option = by_id(next(o for o in mode.options if o.key == "image reference").inputs)
        self.assertTrue(image_option["reference_image"].optional)
        self.assertTrue(image_option["ref_image_url"].advanced)

    def test_preset_voices_use_byteplus_list(self):
        mode = self.inputs["reference_mode"]
        preset = by_id(next(o for o in mode.options if o.key == "preset voice").inputs)["preset_voice"]
        labels = preset.options
        self.assertEqual(len(labels), len(voices.TTS_2_VOICES))
        self.assertEqual(len(set(labels)), len(labels))
        self.assertEqual(preset.default, labels[0])
        self.assertEqual(labels[0], "Stokie (Female, American English)")
        voice_map = nodes_speech.SEED_AUDIO_VOICE_MAP
        self.assertEqual(voice_map["Stokie (Female, American English)"], "en_female_stokie_uranus_bigtts")
        self.assertEqual(set(voice_map.values()), set(voices.TTS_2_VOICE_IDS))
        for label in labels:
            self.assertRegex(label, r"^[A-Za-z][^()]* \((Female|Male), [A-Za-z ]+\)$")
        # The official list has no name for this voice; the label falls back to the ID.
        self.assertEqual(voice_map["Shane (Male, Korean)"], "ko_male_shane_uranus_bigtts")

    def test_core_widgets(self):
        sample_rate = self.inputs["sample_rate"]
        self.assertEqual(sample_rate.options, ["8000", "16000", "24000", "32000", "44100", "48000"])
        self.assertEqual(sample_rate.default, "24000")
        for name, low, high in (("speech_rate", -50, 100), ("loudness_rate", -50, 100), ("pitch_rate", -12, 12)):
            item = self.inputs[name]
            self.assertEqual((item.default, item.min, item.max), (0, low, high), name)
        seed = self.inputs["seed"]
        self.assertEqual((seed.default, seed.min, seed.max), (42, 0, SEED_MAX))
        self.assertTrue(seed.control_after_generate)
        self.assertEqual(seed.tooltip, SEED_TOOLTIP)
        model = self.inputs["model"]
        self.assertTrue(model.optional)
        self.assertEqual(model.options, ["seed-audio-1.0"])
        self.assertEqual(model.default, "seed-audio-1.0")

    def test_outputs(self):
        outputs = self.schema.outputs
        self.assertEqual([o.io_type for o in outputs], ["AUDIO", "STRING", "STRING", "FLOAT", "STRING"])
        self.assertEqual([o.display_name for o in outputs], [None, "subtitles_json", "srt", "duration", "url"])


class FakeSpeechHTTP:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, method, url, headers, body, timeout):
        self.calls.append(SimpleNamespace(method=method, url=url, headers=dict(headers), body=body))
        status, headers_out, payload = self.responses.pop(0)
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload).encode()
        return speech_api.SpeechResponse(status, headers_out, payload)


@requires_comfyui
class SeedAudioRequestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._old_send = speech_api._send
        self.client = speech_api.SeedSpeechClient("speech-key-1")

    def tearDown(self):
        speech_api._send = self._old_send

    def serve(self, count=1):
        wav = audio_utils.audio_to_wav_bytes(sine_audio(0.25, 24000))
        payload = {"code": 0, "audio": base64.b64encode(wav).decode(), "duration": 0.25}
        fake = FakeSpeechHTTP([(200, {}, payload)] * count)
        speech_api._send = fake
        return fake

    async def run_node(self, reference_mode, text_prompt="Hello there", **kwargs):
        return await nodes_speech.BytePlusSeedAudio.execute(self.client, text_prompt, reference_mode, **kwargs)

    async def test_preset_voice_request(self):
        fake = self.serve()
        await self.run_node(
            {"reference_mode": "preset voice", "preset_voice": "Tim (Male, American English)"},
            "@Audio1 says hello.", sample_rate="48000", speech_rate=20, loudness_rate=-10, pitch_rate=3, seed=7,
        )
        body = fake.calls[0].body
        self.assertEqual(body, {
            "model": "seed-audio-1.0",
            "text_prompt": "@Audio1 says hello.",
            "references": [{"speaker": "en_male_tim_uranus_bigtts"}],
            "audio_config": {"format": "wav", "sample_rate": 48000, "speech_rate": 20, "loudness_rate": -10,
                             "pitch_rate": 3},
        })
        self.assertNotIn("seed", body)  # like core, seed only re-runs the node
        self.assertNotIn("seed", body["audio_config"])

    async def test_text_only_defaults(self):
        fake = self.serve()
        await self.run_node({"reference_mode": "text only"})
        body = fake.calls[0].body
        self.assertNotIn("references", body)
        self.assertEqual(body["audio_config"]["sample_rate"], 24000)

    async def test_audio_reference_sockets_and_sources(self):
        fake = self.serve()
        await self.run_node(
            {
                "reference_mode": "audio reference",
                "reference_audio_1": sine_audio(1.0, 16000),
                "ref_audio_2_source": "S_custom_voice",
                "ref_audio_3_source": "https://cdn.example/clip.mp3",
            },
            "@Audio1 asks, @Audio2 answers, @Audio3 laughs.",
        )
        refs = fake.calls[0].body["references"]
        self.assertEqual(list(refs[0]), ["audio_data"])
        self.assertEqual(base64.b64decode(refs[0]["audio_data"])[:4], b"RIFF")
        self.assertEqual(refs[1:], [{"speaker": "S_custom_voice"}, {"audio_url": "https://cdn.example/clip.mp3"}])

    async def test_image_reference_is_upscaled_to_core_minimum(self):
        import torch
        from PIL import Image

        fake = self.serve(2)
        await self.run_node({"reference_mode": "image reference", "reference_image": torch.rand((1, 100, 200, 3))},
                            "Hello from the picture.")
        data = base64.b64decode(fake.calls[0].body["references"][0]["image_data"])
        width, height = Image.open(io.BytesIO(data)).size
        self.assertGreaterEqual(width * height, 160_000)
        self.assertAlmostEqual(width / height, 2.0, places=2)
        await self.run_node({"reference_mode": "image reference", "ref_image_url": "https://cdn.example/face.png"})
        self.assertEqual(fake.calls[1].body["references"], [{"image_url": "https://cdn.example/face.png"}])

    def test_image_pixel_bounds(self):
        import torch

        small = nodes_speech.fit_image_pixels(torch.rand((2, 10, 30, 3)))
        self.assertEqual(small.shape[0], 1)
        self.assertGreaterEqual(small.shape[1] * small.shape[2], 160_000)
        big = nodes_speech.fit_image_pixels(torch.rand((1, 3000, 2000, 3)))
        self.assertLessEqual(big.shape[1] * big.shape[2], 2048 * 2048)
        same = torch.rand((1, 400, 400, 3))
        self.assertTrue(torch.equal(nodes_speech.fit_image_pixels(same), same))

    async def test_core_validations(self):
        import torch

        audio_mode = {"reference_mode": "audio reference"}
        cases = [
            ({"reference_mode": "text only"}, "Hi @Audio1", "text only"),
            (audio_mode, "Hi", "requires at least one"),
            ({**audio_mode, "reference_audio_2": sine_audio()}, "Hi", "without gaps"),
            ({**audio_mode, "ref_audio_1_source": "voice", "ref_audio_3_source": "voice"}, "Hi", "without gaps"),
            ({**audio_mode, "ref_audio_1_source": "voice"}, "@Audio1 and @Audio2", "only 1 reference"),
            ({**audio_mode, "reference_audio_1": sine_audio(), "ref_audio_1_source": "voice"}, "Hi", "not both"),
            ({**audio_mode, "reference_audio_1": sine_audio(31.0, 8000)}, "Hi", "maximum is 30"),
            ({"reference_mode": "image reference"}, "Hi", "requires a reference_image"),
            ({"reference_mode": "image reference", "reference_image": torch.ones((1, 8, 8, 3))}, "@Audio1 hi",
             "not used in 'image reference'"),
            ({"reference_mode": "image reference", "reference_image": torch.ones((1, 8, 8, 3)),
              "ref_image_url": "https://x/i.png"}, "Hi", "not both"),
            ({"reference_mode": "preset voice"}, "Hi", "requires selecting a preset voice"),
            ({"reference_mode": "preset voice", "preset_voice": "Nobody (Male, Klingon)"}, "Hi",
             "requires selecting a preset voice"),
            ({"reference_mode": "preset voice", "preset_voice": "Tim (Male, American English)"},
             "@Audio2 speaks", "single voice"),
            ({"reference_mode": "something else"}, "Hi", "Unknown reference mode"),
            ({"reference_mode": "text only"}, "x" * 3001, "maximum is 3000"),
            ({"reference_mode": "text only"}, "  ", "empty"),
        ]
        for reference_mode, prompt, message in cases:
            with self.subTest(mode=reference_mode.get("reference_mode"), prompt=prompt[:20], message=message):
                fake = self.serve()
                with self.assertRaisesRegex(BytePlusException, message):
                    await self.run_node(reference_mode, prompt)
                self.assertEqual(fake.calls, [])

    async def test_limits_that_pass(self):
        fake = self.serve(3)
        await self.run_node({"reference_mode": "text only"}, "x" * 3000)
        await self.run_node({"reference_mode": "preset voice", "preset_voice": "Tim (Male, American English)"},
                            "@audio1 speaks")  # one tag is fine, case-insensitive like core
        await self.run_node({"reference_mode": "audio reference", "ref_audio_1_source": "voice"}, "No tags at all.")
        self.assertEqual(len(fake.calls), 3)

    async def test_pcm_uses_selected_sample_rate(self):
        import struct

        pcm = struct.pack("<4h", 0, 1000, -1000, 0)
        speech_api._send = FakeSpeechHTTP([(200, {}, {"code": 0, "audio": base64.b64encode(pcm).decode()})])
        audio, *_ = (await self.run_node({"reference_mode": "text only"}, audio_format="pcm",
                                         sample_rate="8000")).result
        self.assertEqual(audio["sample_rate"], 8000)
        self.assertEqual(tuple(audio["waveform"].shape), (1, 1, 4))


# --- templates and messages -----------------------------------------------------

@requires_comfyui
class TemplateTests(unittest.TestCase):
    CASES = {
        "Seed.json": ("BytePlusSeed", lambda: nodes_seed.BytePlusSeed, "model"),
        "Seed Audio.json": ("BytePlusSeedAudio", lambda: nodes_speech.BytePlusSeedAudio, "reference_mode"),
    }

    def test_templates_match_schema(self):
        sys.path.insert(0, PLUGIN_ROOT)
        from tests.test_workflow_templates import WorkflowTemplateTests

        for file_name, (node_type, node_cls, combo) in self.CASES.items():
            with self.subTest(template=file_name):
                node = next(n for n in load_workflow(file_name)["nodes"] if n["type"] == node_type)
                schema = node_cls().define_schema()
                values = list(node["widgets_values"])
                selections = {combo: values[1]}
                names = [item["name"] for item in node["inputs"]]
                self.assertEqual(names, workflow_input_names(schema.inputs, selections))
                self.assertEqual(names, WorkflowTemplateTests.CURRENT_INPUT_ORDERS[node_type])
                combo_input = next(item for item in node["inputs"] if item["name"] == combo)
                self.assertEqual(combo_input["type"], "COMFY_DYNAMICCOMBO_V3")

                # widgets_values are positional; seed is followed by its control value.
                spec = flat_inputs(schema.inputs, selections)
                widget_names = [item["name"] for item in node["inputs"] if "widget" in item]
                assigned = {}
                for name in widget_names:
                    assigned[name] = values.pop(0)
                    if name == "seed":
                        self.assertIn(values.pop(0), ["fixed", "increment", "decrement", "randomize"])
                self.assertEqual(values, [])
                for name, value in assigned.items():
                    item = spec[name]
                    if isinstance(item, comfy_io.DynamicCombo.Input):
                        self.assertIn(value, [o.key for o in item.options], name)
                    elif isinstance(item, comfy_io.Combo.Input):
                        self.assertIn(value, item.options, name)
                    elif isinstance(item, (comfy_io.Int.Input, comfy_io.Float.Input)):
                        self.assertGreaterEqual(value, item.min, name)
                        self.assertLessEqual(value, item.max, name)
                    elif isinstance(item, comfy_io.Boolean.Input):
                        self.assertIsInstance(value, bool, name)
                    else:
                        self.assertIsInstance(value, str, name)

                output_names = [o["name"] for o in node["outputs"]]
                expected = [o.display_name or o.io_type for o in schema.outputs]
                self.assertEqual(output_names, expected)

    def test_visual_template_was_replaced(self):
        self.assertFalse(os.path.exists(os.path.join(WORKFLOW_DIR, "VisualUnderstanding.json")))

    def test_messages_exist_and_are_english(self):
        import re

        for name in ("nodes_seed.py", "nodes_speech.py", "nodes_visual.py"):
            with open(os.path.join(PLUGIN_ROOT, "nodes", name), encoding="utf-8") as f:
                source = f.read()
            self.assertTrue(source.isascii(), name)
            for key in set(re.findall(r'(?:get_text|log_msg)\(\s*"([a-z0-9_]+)"', source)):
                self.assertIn(key, constants.MESSAGES, f"{name}: missing message {key}")


if __name__ == "__main__":
    unittest.main()
