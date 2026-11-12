import copy
import importlib
import os
import sys
import types
import unittest
from unittest import mock

COMFY_ROOT = os.environ.get("COMFYUI_ROOT")
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
requires_comfyui = unittest.skipUnless(COMFY_ROOT, "Set COMFYUI_ROOT to a ComfyUI checkout to run these tests.")

if COMFY_ROOT:
    if COMFY_ROOT not in sys.path:
        sys.path.insert(0, COMFY_ROOT)
    import utils  # noqa: F401, E402
    import comfy.cli_args  # noqa: E402

    comfy.cli_args.args.cpu = True
    PACKAGE_NAME = "byteplus_plugin_test"
    if PACKAGE_NAME not in sys.modules:
        package = types.ModuleType(PACKAGE_NAME)
        package.__path__ = [PLUGIN_ROOT]
        sys.modules[PACKAGE_NAME] = package
    config = importlib.import_module(f"{PACKAGE_NAME}.nodes.las_config")
    requests = importlib.import_module(f"{PACKAGE_NAME}.nodes.las_requests")
    shared = importlib.import_module(f"{PACKAGE_NAME}.nodes.nodes_shared")


def setUpModule():
    if COMFY_ROOT:
        from tests.support import isolate_credentials

        unittest.addModuleCleanup(isolate_credentials())


@requires_comfyui
class LASRequestTests(unittest.TestCase):
    def setUp(self):
        self.source = "tos://example-bucket/input.mp4"
        self.output = "tos://example-bucket/output/"
        self.image = "https://media.example/image.png"
        patcher = mock.patch.object(requests, "get_text", lambda key, **kwargs: f"[BytePlus] {key}: {kwargs['detail']}")
        patcher.start()
        self.addCleanup(patcher.stop)

    def genjutsu(self, template="basic", **options):
        return requests.build_genjutsu_request(self.source, template, "Preserve motion and improve clarity.", **options)

    def translation(self, mode=None, **options):
        if mode is None:
            mode = {"mode": "translate", "audio_language": "en-US", "output_languages": "es-MX"}
        return requests.build_translation_request(self.source, mode, self.output, **options)

    def invalid(self, callback, field=None):
        with self.assertRaises(shared.BytePlusException) as caught:
            callback()
        self.assertIn("[BytePlus] err_las_input:", str(caught.exception))
        if field:
            self.assertIn(field, str(caught.exception))

    def test_exact_source_backed_language_support_and_special_fonts(self):
        input_only = {"bn-BD", "ne-NP", "ro-RO", "uk-UA"}
        output_only = {"da-DK", "fi-FI", "he-IL", "hi-IN", "km-KH", "lo-LA", "my-MM", "no-NO", "sv-SE", "sw-KE"}
        self.assertEqual(len(config.INPUT_LANGUAGES), 25)
        self.assertEqual(len(config.OUTPUT_LANGUAGES), 31)
        self.assertEqual(set(config.INPUT_LANGUAGES) - set(config.OUTPUT_LANGUAGES), input_only)
        self.assertEqual(set(config.OUTPUT_LANGUAGES) - set(config.INPUT_LANGUAGES), output_only)
        self.assertEqual(set(config.OUTPUT_LANGUAGE_FONTS), set(config.OUTPUT_LANGUAGES))
        self.assertEqual(
            config.OUTPUT_LANGUAGE_FONTS["hi-IN"],
            ("NotoSansDevanagari-Regular", "AnekDevanagari[wdth,wght]", "FiraGO-Regular"),
        )
        self.assertEqual(config.OUTPUT_LANGUAGE_FONTS["km-KH"], ("NotoSansKhmer-Regular", "KantumruyPro[wght]"))
        self.assertEqual(
            config.OUTPUT_LANGUAGE_FONTS["yue-CN"], ("NotoSansHK-Regular", "NotoSansTC-Regular", "ChironHeiHK-Text-R")
        )
        self.assertNotIn("tl-PH", config.OUTPUT_LANGUAGES)
        self.assertEqual(
            set(config.STYLE_FIELDS),
            {
                "subtitle_font_by_language",
                "subtitle_font_size",
                "subtitle_font_color",
                "subtitle_bold",
                "subtitle_outline_color",
                "subtitle_outline_width",
                "subtitle_background_color",
                "subtitle_background_padding_x",
                "subtitle_background_padding_y",
                "subtitle_shadow_color",
                "subtitle_opacity",
                "subtitle_shadow_offset_x",
                "subtitle_shadow_offset_y",
                "subtitle_shadow_blur",
                "subtitle_line_spacing",
                "subtitle_width_ratio",
                "subtitle_top_margin_ratio",
            },
        )

    def test_all_templates_return_full_envelopes_and_preserve_reference_positions(self):
        self.assertEqual(
            tuple(config.GENJUTSU_TEMPLATES),
            ("basic", "general", "replace/person_replace", "replace/subject_replace", "style_transfer/general"),
        )
        for template in config.GENJUTSU_TEMPLATES:
            with self.subTest(template=template):
                envelope = self.genjutsu(
                    {"template": template},
                    image_urls=[self.image, self.image],
                    audio_urls="tos://example-bucket/audio.wav\nasset://audio-1",
                )
                self.assertEqual(envelope["operator_id"], "las_video_edit_enhance")
                self.assertEqual(envelope["operator_version"], "v1")
                self.assertEqual(envelope["data"]["template"], {"id": template})
                self.assertEqual(envelope["data"]["image_urls"], [self.image, self.image])
                self.assertEqual(envelope["data"]["audio_urls"], ["tos://example-bucket/audio.wav", "asset://audio-1"])
                self.assertNotIn("audio_url", envelope["data"])

    def test_reference_requirements_and_prompt_are_validated(self):
        for template in ("general", "replace/person_replace", "replace/subject_replace"):
            self.invalid(lambda template=template: self.genjutsu(template), "image_urls")
        for template in ("basic", "style_transfer/general"):
            self.assertNotIn("image_urls", self.genjutsu(template)["data"])
        for prompt in ("", " \n ", None, 4):
            self.invalid(
                lambda prompt=prompt: requests.build_genjutsu_request(self.source, "basic", prompt), "user_prompt"
            )

    def test_genjutsu_defaults_optional_omission_and_local_fields(self):
        data = self.genjutsu(
            model=None, duration=None, resolution=None, asset_group_id=None, callback_url=None, safety_identifier=None
        )["data"]
        for field in ("model", "duration", "resolution", "asset_group_id", "callback_url", "safety_identifier", "seed"):
            self.assertNotIn(field, data)
        self.assertFalse(data["allow_fallback"])
        self.assertTrue(data["generate_audio"])
        self.assertFalse(data["watermark"])
        self.assertFalse(data["return_last_frame"])
        self.assertFalse(data["use_asset_api"])
        self.assertEqual(data["ratio"], "adaptive")
        self.assertEqual(data["priority"], 0)
        self.assertEqual(data["execution_expires_after"], 172800)
        self.invalid(lambda: self.genjutsu(seed=1), "seed")
        self.invalid(lambda: self.genjutsu(output_tos_path=self.output), "output_tos_path")

    def test_genjutsu_assets_callbacks_and_all_declared_scalar_fields(self):
        request = requests.build_genjutsu_request(
            "asset-opaque-123",
            {"id": "basic", "paras": {}},
            "Keep motion.",
            model="dreamina-seedance-2-0-260128",
            resolution="1080p",
            duration=0,
            ratio="21:9",
            allow_fallback=True,
            generate_audio=False,
            watermark=True,
            return_last_frame=True,
            use_asset_api=True,
            asset_group_id="group-example",
            priority=-100000,
            execution_expires_after=3600,
            callback_url="https://events.example/callback",
            safety_identifier="anonymous-user",
        )
        data = request["data"]
        self.assertEqual(data["video_url"], "asset-opaque-123")
        self.assertEqual(data["asset_group_id"], "group-example")
        self.assertEqual(data["callback_url"], "https://events.example/callback")
        self.assertNotIn("callback", request)
        self.assertEqual(data["duration"], 0)
        self.assertEqual(data["priority"], -100000)
        self.invalid(lambda: self.genjutsu(asset_group_id="group-example"), "use_asset_api")

    def test_models_resolutions_and_duration_use_las_contract_not_modelark(self):
        models = (
            "dreamina-seedance-2-0-mini-260615",
            "dreamina-seedance-2-0-fast-260128",
            "dreamina-seedance-2-0-260128",
            "dreamina-seedance-2-5-260628",
        )
        self.assertEqual(tuple(config.GENJUTSU_MODELS), models)
        for model in (*models, config.SERVER_DEFAULT):
            for resolution in ("480p", "720p"):
                self.assertEqual(self.genjutsu(model=model, resolution=resolution)["data"]["resolution"], resolution)
            if model != "dreamina-seedance-2-0-260128":
                self.invalid(lambda model=model: self.genjutsu(model=model, resolution="1080p"), "resolution")
            self.invalid(lambda model=model: self.genjutsu(model=model, resolution="4k"), "resolution")
            self.assertEqual(self.genjutsu(model=model, duration=-1)["data"]["duration"], -1)
            if model in ("dreamina-seedance-2-5-260628", config.SERVER_DEFAULT):
                self.invalid(lambda model=model: self.genjutsu(model=model, duration=5), "duration")
        self.invalid(lambda: self.genjutsu(model="ep-example"), "model")
        self.invalid(lambda: self.genjutsu(model="seedance-2-0-260128"), "model")
        self.assertNotIn("duration", self.genjutsu(duration={"duration": "server_default"})["data"])
        self.assertEqual(self.genjutsu(duration={"duration": "auto"})["data"]["duration"], -1)
        self.assertEqual(
            self.genjutsu(model=models[0], duration={"duration": "explicit", "seconds": 8})["data"]["duration"], 8
        )

    def test_unverified_controls_and_undeclared_template_parameters_are_gated(self):
        for field, value in (
            ("moderation_options", {}),
            ("moderation_options_json", "{}"),
            ("output_format", "mp4"),
            ("omni_reference_task_type", "auto"),
        ):
            self.invalid(lambda field=field, value=value: self.genjutsu(**{field: value}), field)
        self.invalid(lambda: self.genjutsu({"id": "basic", "paras": {"anything": 1}}), "paras")
        self.invalid(
            lambda: self.genjutsu({"template": "style_transfer/general", "style_template": "2d_japanese"}),
            "style_template",
        )
        self.invalid(
            lambda: self.genjutsu({"id": "style_transfer/general", "paras": {"style_template": "2d_japanese"}}), "paras"
        )
        self.invalid(lambda: self.genjutsu({"id": "unknown"}), "template")
        self.invalid(lambda: self.genjutsu({"id": "basic", "paras": []}), "paras")

    def test_scalar_type_and_bounds_validation_does_not_accept_boolean_integers(self):
        for field in ("allow_fallback", "generate_audio", "watermark", "return_last_frame", "use_asset_api"):
            for value in (1, "false", [], {}):
                self.invalid(lambda field=field, value=value: self.genjutsu(**{field: value}), field)
        for field, value in (
            ("priority", True),
            ("priority", 1.2),
            ("duration", True),
            ("duration", -2),
            ("execution_expires_after", 3599),
            ("execution_expires_after", 259201),
            ("execution_expires_after", False),
            ("ratio", "auto"),
        ):
            self.invalid(lambda field=field, value=value: self.genjutsu(**{field: value}), field)
        self.assertEqual(self.genjutsu(execution_expires_after=259200)["data"]["execution_expires_after"], 259200)

    def test_reference_validation_rejects_local_private_malformed_or_credential_urls_without_echoing_them(self):
        for source in (
            "",
            None,
            2,
            "/tmp/video.mp4",
            "file:///tmp/video.mp4",
            "http://localhost/video",
            "https://127.0.0.1/video",
            "https://[::1]/video",
            "https://10.2.3.4/video",
            "https://user:private-password@media.example/video?token=private-token",
            "https://media.example:bad/video",
            "https://media.example/\nvideo",
            "tos://bucket/",
            "https://media.example/video#fragment",
            "https://media.example\\@127.0.0.1/video",
        ):
            self.invalid(
                lambda source=source: requests.build_translation_request(
                    source, {"mode": "translate", "audio_language": "en-US", "output_languages": "es-MX"}, self.output
                ),
                "video_url",
            )
        for source in (
            "http://media.example/video.mp4",
            "https://media.example/video?signature=example",
            "tos://example-bucket/folder/video.mp4",
        ):
            self.assertEqual(
                requests.build_translation_request(
                    source, {"mode": "translate", "audio_language": "en-US", "output_languages": "es-MX"}, self.output
                )["data"]["video_url"],
                source,
            )
        for source in ("asset://las-id", "las-id"):
            self.invalid(
                lambda source=source: requests.build_translation_request(
                    source, {"mode": "translate", "audio_language": "en-US", "output_languages": "es-MX"}, self.output
                )
            )
            self.assertEqual(
                requests.build_genjutsu_request(source, "basic", "Keep motion.")["data"]["video_url"], source
            )
        with self.assertRaises(shared.BytePlusException) as caught:
            requests.build_genjutsu_request(
                "https://user:private-password@media.example/v?token=private-token", "basic", "Keep motion."
            )
        self.assertNotIn("private-password", str(caught.exception))
        self.assertNotIn("private-token", str(caught.exception))

    def test_translation_defaults_and_top_level_callback(self):
        envelope = self.translation(callback_url="https://events.example/callback")
        self.assertEqual(envelope["operator_id"], "las_video_translate")
        self.assertEqual(envelope["operator_version"], "v1")
        self.assertEqual(envelope["callback"], {"url": "https://events.example/callback"})
        data = envelope["data"]
        self.assertEqual(data["output_tos_path"], self.output)
        self.assertEqual(data["audio_language"], "en-US")
        self.assertEqual(data["output_languages"], ["es-MX"])
        self.assertEqual(data["caption_formats"], [".srt"])
        self.assertEqual(data["dubbing_mode"], "high_quality")
        for field in (
            "enable_fast_mode",
            "enable_dynamic_duration",
            "lip_translate",
            "output_dubbing_audio",
            "burn_translated_subtitles",
            "enable_video_inpaint",
        ):
            self.assertFalse(data[field])
        for field in (
            "mode",
            "seed",
            "callback",
            "callback_url",
            "primary_caption_format",
            "subtitle_style",
            "restoration",
            "lip_translate_param",
            "video_inpaint_params",
            *config.STYLE_FIELDS,
        ):
            self.assertNotIn(field, data)

    def test_none_dynamic_children_use_pack_defaults_without_mutating_inputs(self):
        mode = {
            "mode": "translate",
            "audio_language": None,
            "output_languages": None,
            "dubbing_mode": None,
            "enable_fast_mode": None,
            "enable_dynamic_duration": None,
            "lip_translate": None,
            "lip_fuzzy_matching": None,
        }
        before = copy.deepcopy(mode)
        result = self.translation(
            mode,
            subtitle_style=None,
            restoration=None,
            caption_formats=None,
            primary_caption_format=None,
            callback_url=None,
        )["data"]
        self.assertEqual(result["audio_language"], "en-US")
        self.assertEqual(result["output_languages"], ["es-MX"])
        self.assertEqual(mode, before)

    def test_target_order_deduplication_input_output_distinction_and_case(self):
        mode = {"mode": "translate", "audio_language": "bn-BD", "output_languages": " ja-JP,\nfil-PH\nja-JP,\nhi-IN "}
        self.assertEqual(self.translation(mode)["data"]["output_languages"], ["ja-JP", "fil-PH", "hi-IN"])
        for source, targets in (
            ("hi-IN", "en-US"),
            ("en-US", ""),
            ("en-US", "en-US"),
            ("en-US", "bn-BD"),
            ("en-US", "tl-PH"),
            ("en-US", "ES-MX"),
            ("en-US", [3]),
            ("en-US", True),
        ):
            self.invalid(
                lambda source=source, targets=targets: self.translation(
                    {"mode": "translate", "audio_language": source, "output_languages": targets}
                )
            )
        for source in config.INPUT_LANGUAGES:
            targets = [language for language in config.OUTPUT_LANGUAGES if language != source]
            data = self.translation({"mode": "translate", "audio_language": source, "output_languages": targets})[
                "data"
            ]
            self.assertEqual(data["output_languages"], targets)

    def test_dubbing_modes_fast_mode_and_lip_dynamic_duration_relationships(self):
        for dubbing in ("emotion", "timbre", "high_quality"):
            mode = {"mode": "translate", "dubbing_mode": dubbing}
            self.assertEqual(self.translation(mode)["data"]["dubbing_mode"], dubbing)
            mode["enable_fast_mode"] = True
            if dubbing == "high_quality":
                self.assertTrue(self.translation(mode)["data"]["enable_fast_mode"])
            else:
                self.invalid(lambda mode=mode: self.translation(mode), "enable_fast_mode")
        self.invalid(lambda: self.translation({"mode": "translate", "dubbing_mode": "standard"}), "dubbing_mode")
        self.invalid(
            lambda: self.translation({"mode": "translate", "lip_translate": True, "enable_dynamic_duration": True})
        )
        self.invalid(lambda: self.translation({"mode": "translate", "lip_fuzzy_matching": True}), "lip_translate")
        data = self.translation({"mode": "translate", "lip_translate": True, "lip_fuzzy_matching": True})["data"]
        self.assertEqual(data["lip_translate_param"], {"lip_fuzzy_matching": True})
        self.assertNotIn("lip_translate_param", self.translation({"mode": "translate"})["data"])
        for field in ("enable_fast_mode", "enable_dynamic_duration", "lip_translate", "lip_fuzzy_matching"):
            self.invalid(lambda field=field: self.translation({"mode": "translate", field: "true"}), field)

    def test_external_lip_sync_nesting_language_override_and_no_translation_fields(self):
        mode = {
            "mode": "lip_sync_external_audio",
            "dubbing_audio_url": "tos://example-bucket/dub.wav",
            "dubbing_language": "fil-PH",
            "audio_language": "bn-BD",
            "ignore_audio_video_duration_mismatch": True,
        }
        data = self.translation(mode, caption_formats=".srt,.ass", primary_caption_format=".ass")["data"]
        self.assertTrue(data["lip_translate"])
        self.assertEqual(data["audio_language"], "bn-BD")
        self.assertEqual(
            data["lip_translate_param"],
            {
                "dubbing_audio": {
                    "source": "url",
                    "url": "tos://example-bucket/dub.wav",
                    "language": "fil-PH",
                    "ignore_audio_video_duration_mismatch": True,
                }
            },
        )
        for field in (
            "output_languages",
            "caption_formats",
            "dubbing_mode",
            "enable_fast_mode",
            "enable_dynamic_duration",
            "output_dubbing_audio",
            "burn_translated_subtitles",
            *config.STYLE_FIELDS,
        ):
            self.assertNotIn(field, data)
        automatic = self.translation(
            {"mode": "lip_sync_external_audio", "dubbing_audio_url": self.image, "dubbing_language": None}
        )["data"]
        self.assertNotIn("audio_language", automatic)
        self.assertNotIn("language", automatic["lip_translate_param"]["dubbing_audio"])
        self.invalid(lambda: self.translation({"mode": "lip_sync_external_audio"}), "dubbing_audio_url")
        self.invalid(
            lambda: self.translation(
                {"mode": "lip_sync_external_audio", "dubbing_audio_url": self.image, "dubbing_language": "hi-IN"}
            ),
            "dubbing_language",
        )
        self.invalid(
            lambda: self.translation(
                {"mode": "lip_sync_external_audio", "dubbing_audio_url": self.image, "lip_fuzzy_matching": True}
            ),
            "lip_fuzzy_matching",
        )
        self.invalid(
            lambda: self.translation(
                {
                    "mode": "lip_sync_external_audio",
                    "dubbing_audio_url": self.image,
                    "ignore_audio_video_duration_mismatch": 1,
                }
            )
        )

    def test_source_audio_mode_requires_input_language_and_never_sends_url(self):
        data = self.translation({"mode": "lip_sync_source_audio", "audio_language": "en-US"})["data"]
        self.assertEqual(data["lip_translate_param"], {"dubbing_audio": {"source": "video"}})
        self.assertEqual(data["audio_language"], "en-US")
        self.assertNotIn("caption_formats", data)
        self.assertNotIn("output_languages", data)
        for value in ("", None, "he-IL"):
            self.invalid(
                lambda value=value: self.translation({"mode": "lip_sync_source_audio", "audio_language": value}),
                "audio_language",
            )
        self.invalid(
            lambda: self.translation(
                {"mode": "lip_sync_source_audio", "audio_language": "en-US", "dubbing_audio_url": self.image}
            ),
            "dubbing_audio_url",
        )

    def test_caption_formats_primary_selection_and_tos_and_callback_requirements(self):
        data = self.translation(caption_formats=".ass,.txt\n.srt,.xml,.vtt,.srt", primary_caption_format=".ass")["data"]
        self.assertEqual(data["caption_formats"], [".ass", ".txt", ".srt", ".xml", ".vtt"])
        for formats, primary in (("", ".srt"), (".srt", ".ass"), ("srt", ".srt"), (".pdf", ".pdf"), ([1], ".srt")):
            self.invalid(
                lambda formats=formats, primary=primary: self.translation(
                    caption_formats=formats, primary_caption_format=primary
                )
            )
        for path in ("", None, "https://media.example/out", "tos://bucket", "tos://bucket/", "tos://bucket/path?q=1"):
            self.invalid(
                lambda path=path: requests.build_translation_request(self.source, {"mode": "translate"}, path),
                "output_tos_path",
            )
        for callback in ("http://events.example/hook", "https://127.0.0.1/hook", "tos://bucket/hook", "not-a-url"):
            self.invalid(lambda callback=callback: self.translation(callback_url=callback), "callback_url")
            self.invalid(lambda callback=callback: self.genjutsu(callback_url=callback), "callback_url")

    def test_separate_picture_requires_burn_and_excludes_restoration_and_specified_audio(self):
        self.invalid(lambda: self.translation(subtitle_video_url=self.image), "burn_translated_subtitles")
        data = self.translation(subtitle_video_url=self.image, burn_translated_subtitles=True)["data"]
        self.assertEqual(data["subtitle_video_url"], self.image)
        self.invalid(
            lambda: self.translation(
                subtitle_video_url=self.image, burn_translated_subtitles=True, restoration={"restoration": "on"}
            )
        )
        for mode in (
            {"mode": "lip_sync_source_audio", "audio_language": "en-US"},
            {"mode": "lip_sync_external_audio", "dubbing_audio_url": self.image},
        ):
            self.invalid(lambda mode=mode: self.translation(mode, restoration={"restoration": "on"}))
            self.invalid(
                lambda mode=mode: self.translation(mode, subtitle_video_url=self.image, burn_translated_subtitles=True)
            )
            self.invalid(lambda mode=mode: self.translation(mode, subtitle_style={"subtitle_style": "custom"}))
            self.invalid(lambda mode=mode: self.translation(mode, output_dubbing_audio=True))

    def test_custom_style_defaults_flat_fields_and_automatic_omission(self):
        data = self.translation(subtitle_style={"subtitle_style": "custom"})["data"]
        for field, spec in config.STYLE_FIELDS.items():
            if field == "subtitle_font_by_language":
                self.assertNotIn(field, data)
            else:
                self.assertEqual(data[field], spec["default"])
        self.assertNotIn("subtitle_style", data)
        self.invalid(lambda: self.translation(subtitle_style={"subtitle_style": "automatic", "subtitle_font_size": 40}))
        self.invalid(lambda: self.translation(subtitle_style={"subtitle_style": "custom", "remote_font": self.image}))
        self.invalid(lambda: self.translation(subtitle_style=[]))

    def test_every_style_numeric_boundary_and_nonfinite_type_failure(self):
        for field, spec in config.STYLE_FIELDS.items():
            if spec["type"] not in ("int", "float"):
                continue
            for value in (spec["min"], spec["max"] - 0.000001 if spec.get("max_exclusive") else spec["max"]):
                with self.subTest(field=field, valid=value):
                    self.assertEqual(
                        self.translation(subtitle_style={"subtitle_style": "custom", field: value})["data"][field],
                        value,
                    )
            for value in (
                spec["min"] - 1,
                spec["max"] + 1,
                float("nan"),
                float("inf"),
                float("-inf"),
                True,
                "1",
                [],
                {},
            ):
                with self.subTest(field=field, invalid=value):
                    self.invalid(
                        lambda field=field, value=value: self.translation(
                            subtitle_style={"subtitle_style": "custom", field: value}
                        ),
                        field,
                    )
        self.invalid(lambda: self.translation(subtitle_style={"subtitle_style": "custom", "subtitle_font_size": 40.0}))
        self.invalid(
            lambda: self.translation(subtitle_style={"subtitle_style": "custom", "subtitle_top_margin_ratio": 1})
        )
        self.assertEqual(
            self.translation(subtitle_style={"subtitle_style": "custom", "subtitle_font_size": None})["data"][
                "subtitle_font_size"
            ],
            40,
        )

    def test_colors_alpha_and_style_boolean_are_strict(self):
        colors = [field for field, spec in config.STYLE_FIELDS.items() if spec["type"] == "color"]
        for field in colors:
            for value in ("#a0B1c2", "#a0B1c200", "#FFFFFFff"):
                self.assertEqual(
                    self.translation(subtitle_style={"subtitle_style": "custom", field: value})["data"][field], value
                )
            for value in ("white", "#FFF", "#FFFFFFFFF", "#GG0000", "FFFFFF", 0, True):
                self.invalid(
                    lambda field=field, value=value: self.translation(
                        subtitle_style={"subtitle_style": "custom", field: value}
                    ),
                    field,
                )
        self.invalid(
            lambda: self.translation(subtitle_style={"subtitle_style": "custom", "subtitle_bold": 1}), "subtitle_bold"
        )

    def test_per_language_fonts_nullable_defaults_exact_count_and_target_positions(self):
        for language, fonts in config.OUTPUT_LANGUAGE_FONTS.items():
            for font in fonts:
                mode = {"mode": "translate", "audio_language": "bn-BD", "output_languages": [language]}
                self.assertEqual(
                    self.translation(
                        mode,
                        subtitle_style={
                            "subtitle_style": "custom",
                            "subtitle_font_by_language": [font],
                        },
                    )["data"]["subtitle_font_by_language"],
                    [font],
                )
        mode = {"mode": "translate", "audio_language": "en-US", "output_languages": "ja-JP,es-MX,hi-IN"}
        fonts = '["NotoSansJP-Regular", null, ""]'
        data = self.translation(mode, subtitle_style={"subtitle_style": "custom", "subtitle_font_by_language": fonts})[
            "data"
        ]
        self.assertEqual(data["subtitle_font_by_language"], ["NotoSansJP-Regular", None, None])
        for invalid in (
            '["NotoSans-Regular"]',
            '["NotoSansJP-Regular", null, 2]',
            '[null, null, "file:///font.ttf"]',
            '[null, null, "https://fonts.example/f.ttf"]',
            '["NotoSans-Regular", null, null]',
            "not JSON",
            "{}",
        ):
            self.invalid(
                lambda invalid=invalid: self.translation(
                    mode,
                    subtitle_style={
                        "subtitle_style": "custom",
                        "subtitle_font_by_language": invalid,
                    },
                ),
                "subtitle_font_by_language",
            )
        for targets in ("es-MX,es-MX", "es-MX,", ["es-MX", ""]):
            self.invalid(
                lambda targets=targets: self.translation(
                    {"mode": "translate", "output_languages": targets},
                    subtitle_style={"subtitle_style": "custom", "subtitle_font_by_language": [None]},
                ),
                "output_languages",
            )
        self.assertNotIn(
            "subtitle_font_by_language",
            self.translation(subtitle_style={"subtitle_style": "custom", "subtitle_font_by_language": "null"})["data"],
        )

    def test_restoration_defaults_inheritance_custom_path_and_all_wrapper_fields(self):
        data = self.translation(restoration={"restoration": "on"})["data"]
        self.assertTrue(data["enable_video_inpaint"])
        repair = data["video_inpaint_params"]
        self.assertEqual(repair["output_tos_path"], self.output)
        self.assertEqual(repair["model_level"], "pro")
        self.assertEqual(repair["detection_mode"], "standard_subtitle")
        self.assertEqual(repair["max_process_frames"], -1)
        self.assertEqual(repair["maximum_output_fps"], 30)
        self.assertEqual(repair["maximum_resolution"], "1080p")
        self.assertEqual(repair["output_video_mode"], "balanced")
        self.assertTrue(repair["return_subtitle_bbox"])
        self.assertFalse(repair["subtitle_persistent"])
        self.assertNotIn("output_codec", repair)
        self.assertNotIn("output_bitrate", repair)
        supplied = {
            "restoration": "on",
            "output_tos_path": "tos://example-bucket/repair/",
            "model_level": "pro",
            "detection_mode": "precise",
            "targets": "watermark,subtitle,scrolling_subtitle,drama_info",
            "fixed_bboxes": "[[0, 700, 1000, 950]]",
            "return_subtitle_bbox": False,
            "max_process_frames": 300,
            "maximum_output_fps": 60,
            "maximum_resolution": "4k",
            "output_video_mode": "source",
            "output_codec": "h265",
            "output_bitrate": 4000000,
            "subtitle_persistent": True,
        }
        before = copy.deepcopy(supplied)
        repair = self.translation(restoration=supplied)["data"]["video_inpaint_params"]
        self.assertEqual(repair["output_bitrate"], 4000000)
        self.assertEqual(repair["fixed_bboxes"], [[0, 700, 1000, 950]])
        self.assertEqual(repair["targets"], ["watermark", "subtitle", "scrolling_subtitle", "drama_info"])
        self.assertEqual(supplied, before)
        repair["fixed_bboxes"][0][0] = 1
        self.assertEqual(supplied, before)
        self.assertFalse(config.INPAINT_BITRATE_UI_ENABLED)
        self.assertEqual(config.INPAINT_BITRATE_UNIT, "bps")

    def test_restoration_enums_precise_targets_and_fixed_boxes(self):
        for tier in ("lite", "pro"):
            for detection in ("standard_subtitle", "standard_watermark", "standard_subtitle_watermark", "precise"):
                options = {"restoration": "on", "model_level": tier, "detection_mode": detection, "targets": "subtitle"}
                if tier == "lite" and detection == "precise":
                    self.invalid(lambda options=options: self.translation(restoration=options), "model_level")
                else:
                    self.assertEqual(
                        self.translation(restoration=options)["data"]["video_inpaint_params"]["detection_mode"],
                        detection,
                    )
        self.invalid(
            lambda: self.translation(restoration={"restoration": "on", "detection_mode": "precise"}), "targets"
        )
        self.assertEqual(
            self.translation(
                restoration={"restoration": "on", "detection_mode": "precise", "fixed_bboxes": [[0, 0, 1000, 1000]]}
            )["data"]["video_inpaint_params"]["fixed_bboxes"],
            [[0, 0, 1000, 1000]],
        )
        for field, values in (
            ("maximum_resolution", ("720p", "1080p", "4k")),
            ("output_video_mode", ("economy", "balanced", "high", "source")),
            ("output_codec", ("h264", "h265")),
        ):
            for value in values:
                self.assertEqual(
                    self.translation(restoration={"restoration": "on", field: value})["data"]["video_inpaint_params"][
                        field
                    ],
                    value,
                )
        for field, value in (
            ("model_level", "ultra"),
            ("detection_mode", "auto"),
            ("targets", "text"),
            ("maximum_resolution", "2160p"),
            ("output_video_mode", "quality"),
            ("output_codec", "av1"),
            ("output_tos_path", "https://media.example/out"),
            ("fixed_bboxes_intervals", []),
            ("subtitle_protect", True),
        ):
            self.invalid(
                lambda field=field, value=value: self.translation(restoration={"restoration": "on", field: value}),
                field,
            )

    def test_restoration_boxes_json_types_finite_scalars_and_optional_none(self):
        for boxes in (
            [],
            "[]",
            {},
            "{}",
            "NaN",
            "[[0,0,1000,Infinity]]",
            [[0, 0, 0, 10]],
            [[1, 2, 0, 3]],
            [[0, 10, 2, 10]],
            [[0, 0, 1001, 1000]],
            [[-1, 0, 1000, 1000]],
            [[0.0, 0, 1, 1]],
            [[True, 0, 1, 1]],
            [[0, 0, 1]],
            [None],
            [[0, 0, "1", 1]],
        ):
            self.invalid(
                lambda boxes=boxes: self.translation(restoration={"restoration": "on", "fixed_bboxes": boxes}),
                "fixed_bboxes",
            )
        for field, values in (
            ("output_bitrate", (0, -1, True, 1.0, "4000000")),
            ("maximum_output_fps", (0, -1, True, 30.0)),
            ("max_process_frames", (-2, 0, True, 1.2)),
            ("return_subtitle_bbox", (1, "false")),
            ("subtitle_persistent", ("true", 0)),
        ):
            for value in values:
                self.invalid(
                    lambda field=field, value=value: self.translation(restoration={"restoration": "on", field: value}),
                    field,
                )
        repair = self.translation(
            restoration={
                "restoration": "on",
                "output_tos_path": "",
                "fixed_bboxes": None,
                "targets": None,
                "output_codec": None,
                "output_bitrate": None,
                "model_level": None,
            }
        )["data"]["video_inpaint_params"]
        self.assertEqual(repair["output_tos_path"], self.output)
        self.assertEqual(repair["model_level"], "pro")
        self.assertNotIn("fixed_bboxes", repair)
        self.invalid(lambda: self.translation(restoration={"restoration": "off", "model_level": "pro"}))

    def test_string_mode_accepts_flat_children_and_none_dict_child_can_be_resolved(self):
        data = self.translation("translate", audio_language="bn-BD", output_languages="fil-PH")["data"]
        self.assertEqual(data["audio_language"], "bn-BD")
        self.assertEqual(data["output_languages"], ["fil-PH"])
        data = self.translation(
            {"mode": "lip_sync_external_audio", "audio_language": None},
            audio_language="en-US",
            dubbing_audio_url=self.image,
        )["data"]
        self.assertEqual(data["audio_language"], "en-US")
        self.assertEqual(data["lip_translate_param"]["dubbing_audio"]["url"], self.image)

    def test_public_reference_parser_preserves_positions_and_validates_every_item(self):
        for empty in (None, "", "\n \n", [], ()):
            self.assertEqual(requests.parse_reference_list(empty), [])
        references = [self.image, "tos://example-bucket/audio.wav", "asset://las-reference", self.image]
        self.assertEqual(requests.parse_reference_list(references, "audio_urls"), references)
        self.assertEqual(requests.parse_reference_list("\n" + "\n".join(references) + "\n"), references)
        parsed = requests.parse_reference_list(references)
        parsed.append(self.image)
        self.assertEqual(len(references), 4)
        for invalid in (1, {}, [None], [""], ["file:///tmp/image.png"], ["bare-las-id"]):
            self.invalid(lambda invalid=invalid: requests.parse_reference_list(invalid), "image_urls")
        self.assertEqual(len(requests.parse_reference_list([self.image] * 100)), 100)

    def test_native_duration_seconds_child_and_conflicting_names(self):
        data = self.genjutsu(model=config.GENJUTSU_MODELS[0], duration={"duration": "explicit", "duration_seconds": 4})[
            "data"
        ]
        self.assertEqual(data["duration"], 4)
        self.invalid(
            lambda: self.genjutsu(
                model=config.GENJUTSU_MODELS[0], duration={"duration": "explicit", "seconds": 4, "duration_seconds": 4}
            ),
            "duration",
        )

    def test_crlf_separator_does_not_create_a_blank_target_with_positional_fonts(self):
        mode = {"mode": "translate", "output_languages": "ja-JP\r\nes-MX"}
        style = {"subtitle_style": "custom", "subtitle_font_by_language": ["NotoSansJP-Regular", None]}
        data = self.translation(mode, subtitle_style=style)["data"]
        self.assertEqual(data["output_languages"], ["ja-JP", "es-MX"])
        self.assertEqual(data["subtitle_font_by_language"], ["NotoSansJP-Regular", None])

    def test_unknown_options_and_invalid_group_shapes_never_bypass_validation(self):
        for mode in ("unknown", [], {"mode": "unknown"}, {"other": "translate"}, {"mode": "translate", "extra": 1}):
            self.invalid(lambda mode=mode: self.translation(mode))
        for options in (
            {"extra": 1},
            {"seed": 1},
            {"lip_translate_param": {}},
            {"video_inpaint_params": {}},
            {"callback": {"url": self.image}},
            {"output_dubbing_audio": 1},
            {"burn_translated_subtitles": "false"},
        ):
            self.invalid(lambda options=options: self.translation(**options))
        self.invalid(lambda: self.genjutsu(template={"template": "basic", "id": "general"}))
        self.invalid(lambda: self.genjutsu(duration={"duration": "explicit", "seconds": None}), "duration")
        self.invalid(lambda: self.translation({"mode": "translate", "audio_language": "en-US"}, audio_language="fr-FR"))

    def test_validation_uses_shared_message_key_and_does_not_access_network(self):
        with mock.patch.object(requests, "get_text", return_value="[BytePlus] Invalid LAS input.") as text:
            with self.assertRaises(shared.BytePlusException):
                self.genjutsu(ratio="bad")
        text.assert_called_once()
        self.assertEqual(text.call_args.args, ("err_las_input",))
        self.assertIn("detail", text.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
