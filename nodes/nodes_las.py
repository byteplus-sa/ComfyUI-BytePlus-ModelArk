import asyncio
import hashlib
import json

from comfy_api.latest import io as comfy_io
from comfy_execution.graph_utils import ExecutionBlocker

from . import credentials
from .constants import LAS_API_KEY_ENV
from .las_api import (
    LAS_TASK_HANDLES,
    clear_las_task_handle as clear_task_handle,
    get_las_client,
    poll_las_task,
    redact_las_response,
    submit_and_wait,
    wait_for_las_task,
)
from .las_config import (
    CAPTION_FORMATS,
    DUBBING_MODES,
    GENJUTSU_MODEL_MAP,
    GENJUTSU_RATIOS,
    GENJUTSU_TEMPLATES,
    INPUT_LANGUAGES,
    RESTORATION_FIELDS,
    SERVER_DEFAULT,
    STYLE_FIELDS,
)
from .las_media import (
    check_source,
    collect_genjutsu,
    collect_translation,
    invalid,
    split_audio,
    upload_audio,
    upload_images,
    upload_video,
    validate_alignment,
    validate_translation_metadata,
    video_metadata,
)
from .las_requests import build_genjutsu_request, build_translation_request, parse_reference_list
from .nodes_shared import GLOBAL_CATEGORY, get_text, send_node_text, wait_interruptible, with_client

LAS_CATEGORY = f"{GLOBAL_CATEGORY}/LAS"
OPERATORS = {"Genjutsu": "las_video_edit_enhance", "Video Translation": "las_video_translate"}


def _hidden():
    return [comfy_io.Hidden.unique_id, comfy_io.Hidden.auth_token_comfy_org, comfy_io.Hidden.api_key_comfy_org]


def _seed():
    return comfy_io.Int.Input(
        "seed",
        default=0,
        min=0,
        max=0xFFFFFFFFFFFFFFFF,
        control_after_generate=True,
        optional=True,
        advanced=True,
        tooltip="Local rerun control, not an API generation seed. Keep fixed when recovering an accepted task.",
    )


def _video_inputs():
    return [
        comfy_io.Video.Input("video", optional=True),
        comfy_io.String.Input(
            "video_url",
            default="",
            optional=True,
            tooltip="Use a persistent public HTTP(S) URL or TOS reference, or connect video, not both. Large uploads may exceed Comfy.org limits.",
        ),
    ]


def _output_contract():
    return [
        comfy_io.Video.Output("video", is_output_list=True),
        comfy_io.Audio.Output("audio", is_output_list=True),
        comfy_io.String.Output("subtitle", is_output_list=True),
        comfy_io.String.Output("language", is_output_list=True),
        comfy_io.Video.Output("repaired_video"),
        comfy_io.String.Output("artifacts_json"),
        comfy_io.String.Output("task_id"),
        comfy_io.String.Output("response"),
    ]


def _mode_input():
    def language():
        return comfy_io.Combo.Input("audio_language", options=list(INPUT_LANGUAGES), default="en-US")

    return comfy_io.DynamicCombo.Input(
        "mode",
        options=[
            comfy_io.DynamicCombo.Option(
                "translate",
                [
                    language(),
                    comfy_io.String.Input(
                        "output_languages",
                        default="es-MX",
                        multiline=True,
                        tooltip="Case-sensitive LAS language codes, comma-separated or one per line. Outputs follow this order.",
                    ),
                    comfy_io.Combo.Input("dubbing_mode", options=list(DUBBING_MODES), default="high_quality"),
                    comfy_io.Boolean.Input("enable_fast_mode", default=False, tooltip="Requires high_quality dubbing."),
                    comfy_io.Boolean.Input(
                        "enable_dynamic_duration", default=False, tooltip="Cannot combine with lip sync."
                    ),
                    comfy_io.Boolean.Input(
                        "lip_translate", default=False, tooltip="Lip synchronization incurs additional charges."
                    ),
                    comfy_io.Boolean.Input(
                        "lip_fuzzy_matching",
                        default=False,
                        tooltip="Visual-only face matching; requires lip_translate.",
                    ),
                ],
            ),
            comfy_io.DynamicCombo.Option(
                "lip_sync_external_audio",
                [
                    comfy_io.String.Input(
                        "dubbing_audio_url",
                        default="",
                        optional=True,
                        tooltip="Connect one AUDIO clip or provide HTTP(S)/TOS audio, not both.",
                    ),
                    comfy_io.Combo.Input(
                        "dubbing_language", options=["auto", *INPUT_LANGUAGES], default="auto", optional=True
                    ),
                    comfy_io.Boolean.Input(
                        "ignore_audio_video_duration_mismatch",
                        default=False,
                        optional=True,
                        tooltip="Normally durations must differ by at most 1 second. Override loops or trims video to audio and can affect billing.",
                    ),
                ],
            ),
            comfy_io.DynamicCombo.Option("lip_sync_source_audio", [language()]),
        ],
        tooltip="Translation/TTS or lip sync to specified audio. Specified-audio modes do not produce translated captions.",
    )


def _field_input(name, spec):
    options = {"optional": True, "default": spec["default"]}
    kind = spec["type"]
    if kind == "bool":
        return comfy_io.Boolean.Input(name, **options)
    if kind in ("int", "float"):
        for key in ("min", "max"):
            if key in spec:
                options[key] = spec[key]
        if spec.get("max_exclusive"):
            options["max"] = 0.999
        return (comfy_io.Int if kind == "int" else comfy_io.Float).Input(name, **options)
    if kind == "enum":
        values = list(spec["options"])
        if spec["default"] is None:
            values.insert(0, SERVER_DEFAULT)
            options["default"] = SERVER_DEFAULT
        return comfy_io.Combo.Input(name, options=values, **options)
    options["default"] = spec["default"] or ""
    tooltip = {
        "fonts": "JSON array of supported font IDs in target-language order. null entries use automatic fonts.",
        "boxes": "JSON array [[left, top, right, bottom]] in integer 0-1000 coordinates. Empty uses detection.",
        "list": "Comma-separated target types: watermark, subtitle, scrolling_subtitle, drama_info.",
        "tos": "Empty inherits the translation output TOS directory.",
        "color": "#RRGGBB or #RRGGBBAA, where FF is opaque and 00 transparent.",
    }.get(kind)
    return comfy_io.String.Input(name, tooltip=tooltip, **options)


def _style_input():
    return comfy_io.DynamicCombo.Input(
        "subtitle_style",
        options=[
            comfy_io.DynamicCombo.Option("automatic", []),
            comfy_io.DynamicCombo.Option("custom", [_field_input(name, spec) for name, spec in STYLE_FIELDS.items()]),
        ],
        optional=True,
        tooltip="Styling affects ASS files and burned-in subtitles, not plain SRT text.",
    )


def _restoration_input():
    return comfy_io.DynamicCombo.Input(
        "restoration",
        options=[
            comfy_io.DynamicCombo.Option("off", []),
            comfy_io.DynamicCombo.Option(
                "on",
                [_field_input(name, spec) for name, spec in RESTORATION_FIELDS.items() if spec.get("ui_enabled", True)],
            ),
        ],
        optional=True,
        tooltip="Remove subtitles or watermarks before translation; additional restoration charges apply. Custom bitrate is withheld pending contract verification.",
    )


def _node_id(cls):
    value = getattr(cls.hidden, "unique_id", None)
    if value is None or not str(value):
        invalid("A ComfyUI node ID is required for task recovery.")
    return str(value)


def _generation_fingerprint(cls):
    node_id = getattr(cls.hidden, "unique_id", None)
    if node_id is not None and any(key[0] == str(node_id) for key in LAS_TASK_HANDLES):
        return float("nan")
    key = credentials.get_setting(LAS_API_KEY_ENV) or ""
    return hashlib.sha256(key.encode()).hexdigest()


def _mode_values(mode):
    if not isinstance(mode, dict):
        invalid("mode must be a DynamicCombo object.")
    values = dict(mode)
    audio = values.pop("dubbing_audio", None)
    if values.get("dubbing_language") == "auto":
        values["dubbing_language"] = None
    return values, audio


def _restoration_values(restoration):
    if not isinstance(restoration, dict):
        return restoration
    values = dict(restoration)
    if values.get("output_codec") == SERVER_DEFAULT:
        values["output_codec"] = None
    return values


async def _metadata(video):
    try:
        return await wait_interruptible(asyncio.to_thread(video_metadata, video))
    except Exception as error:
        invalid(f"Could not inspect connected video ({type(error).__name__}).")


class BytePlusLASGenjutsu(comfy_io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return comfy_io.Schema(
            node_id="BytePlusLASGenjutsu",
            display_name="BytePlus LAS Genjutsu",
            category=LAS_CATEGORY,
            description="Edit a video through LAS Video Edit Enhance 2.0. Uses a separate LAS key in Johor. Real-person references require rights and LAS access; interruption does not cancel remote work.",
            inputs=[
                comfy_io.String.Input("user_prompt", default="", multiline=True),
                comfy_io.DynamicCombo.Input(
                    "template",
                    options=[comfy_io.DynamicCombo.Option(name, []) for name in GENJUTSU_TEMPLATES],
                    tooltip="Five documented template IDs. Template-specific style enums are withheld pending a complete official contract.",
                ),
                *_video_inputs(),
                comfy_io.Combo.Input(
                    "model", options=[SERVER_DEFAULT, *GENJUTSU_MODEL_MAP], default=SERVER_DEFAULT, optional=True
                ),
                comfy_io.Image.Input("reference_images", optional=True),
                comfy_io.String.Input(
                    "image_urls",
                    default="",
                    multiline=True,
                    optional=True,
                    tooltip="HTTP(S), TOS or LAS asset references, one per line. Connected images precede these references; repetitions preserve positions.",
                ),
                comfy_io.Audio.Input("reference_audio", optional=True),
                comfy_io.String.Input(
                    "audio_urls",
                    default="",
                    multiline=True,
                    optional=True,
                    tooltip="One reference per line. Connected audio batch clips precede these references.",
                ),
                comfy_io.Boolean.Input("allow_fallback", default=False, optional=True),
                comfy_io.Combo.Input(
                    "resolution",
                    options=[SERVER_DEFAULT, "480p", "720p", "1080p"],
                    default=SERVER_DEFAULT,
                    optional=True,
                    tooltip="1080p requires the explicit Seedance 2.0 model. No 4K option until the contract conflict is resolved.",
                ),
                comfy_io.DynamicCombo.Input(
                    "duration",
                    options=[
                        comfy_io.DynamicCombo.Option(SERVER_DEFAULT, []),
                        comfy_io.DynamicCombo.Option("auto", []),
                        comfy_io.DynamicCombo.Option(
                            "explicit", [comfy_io.Int.Input("duration_seconds", default=5, min=1)]
                        ),
                    ],
                    optional=True,
                    tooltip="Seedance 2.5 and server-default routing only support auto duration when specified.",
                ),
                comfy_io.Combo.Input("ratio", options=list(GENJUTSU_RATIOS), default="adaptive", optional=True),
                comfy_io.Boolean.Input("generate_audio", default=True, optional=True),
                comfy_io.Boolean.Input("watermark", default=False, optional=True),
                comfy_io.Boolean.Input("return_last_frame", default=False, optional=True),
                comfy_io.Int.Input("priority", default=0, optional=True, advanced=True),
                comfy_io.Int.Input(
                    "execution_expires_after", default=172800, min=3600, max=259200, optional=True, advanced=True
                ),
                comfy_io.Boolean.Input(
                    "use_asset_api",
                    default=False,
                    optional=True,
                    advanced=True,
                    tooltip="Opt in to LAS media-to-asset conversion. Requires appropriate rights and LAS allowlist access.",
                ),
                comfy_io.String.Input("asset_group_id", default="", optional=True, advanced=True),
                comfy_io.String.Input("safety_identifier", default="", optional=True, advanced=True),
                comfy_io.String.Input("callback_url", default="", optional=True, advanced=True),
                _seed(),
            ],
            outputs=[
                comfy_io.Video.Output("video"),
                comfy_io.Image.Output("last_frame"),
                comfy_io.String.Output("task_id"),
                comfy_io.String.Output("response"),
            ],
            hidden=_hidden(),
            not_idempotent=True,
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return _generation_fingerprint(cls)

    @classmethod
    @with_client("las_client", get_las_client)
    async def execute(
        cls,
        las_client,
        user_prompt,
        template,
        video=None,
        video_url="",
        reference_images=None,
        reference_audio=None,
        seed=0,
        **options,
    ):
        source = check_source(video, video_url, "video")
        model = options.get("model")
        options["model"] = GENJUTSU_MODEL_MAP.get(model, model)
        image_urls = options.get("image_urls") or ""
        audio_urls = options.get("audio_urls") or ""
        placeholder_images = ["https://example.com/connected-image"] if reference_images is not None else []
        if reference_images is not None:
            import torch

            if (
                not isinstance(reference_images, torch.Tensor)
                or reference_images.ndim != 4
                or min(reference_images.shape) < 1
                or reference_images.shape[-1] not in (3, 4)
                or not torch.isfinite(reference_images).all()
            ):
                invalid("reference_images must be a finite nonempty IMAGE batch.")
        if reference_audio is not None:
            split_audio(reference_audio)
        images = parse_reference_list(image_urls, "image_urls")
        audios = parse_reference_list(audio_urls, "audio_urls")
        build_genjutsu_request(
            source or "https://example.com/connected-video",
            template,
            user_prompt,
            **{**options, "image_urls": placeholder_images + images, "audio_urls": audios},
        )
        if video is not None:
            source = await upload_video(cls, video)
        images = await upload_images(cls, reference_images) + images
        if reference_audio is not None:
            audios = await upload_audio(cls, reference_audio) + audios
        envelope = build_genjutsu_request(
            source, template, user_prompt, **{**options, "image_urls": images, "audio_urls": audios}
        )
        task = await submit_and_wait(las_client, envelope, _node_id(cls), rerun_seed=seed or 0)
        result = await collect_genjutsu(
            task, return_last_frame=envelope["data"]["return_last_frame"], api_key=las_client.api_key
        )
        if result.complete:
            clear_task_handle(las_client, envelope, _node_id(cls), rerun_seed=seed or 0)
        return comfy_io.NodeOutput(*result.values)


class BytePlusLASVideoTranslation(comfy_io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return comfy_io.Schema(
            node_id="BytePlusLASVideoTranslation",
            display_name="BytePlus LAS Video Translation",
            category=LAS_CATEGORY,
            description="Translate and dub video into ordered target languages, or synchronize lips to external/source audio. Optional subtitle styling, separate picture source and restoration. Requires LAS key and writable Johor TOS directory; each target and added service can incur charges.",
            inputs=[
                _mode_input(),
                comfy_io.String.Input(
                    "output_tos_path",
                    default="",
                    tooltip="Required writable TOS output directory in the same account and Johor region, for example tos://your-bucket/output/.",
                ),
                *_video_inputs(),
                comfy_io.Audio.Input(
                    "dubbing_audio",
                    optional=True,
                    tooltip="External-audio lip sync only. Connect exactly one clip, or use the mode's dubbing_audio_url.",
                ),
                comfy_io.String.Input(
                    "caption_formats",
                    default=".srt",
                    optional=True,
                    tooltip="Comma-separated .ass, .txt, .srt, .xml, .vtt.",
                ),
                comfy_io.Combo.Input(
                    "primary_caption_format", options=list(CAPTION_FORMATS), default=".srt", optional=True
                ),
                comfy_io.Boolean.Input(
                    "output_dubbing_audio",
                    default=False,
                    optional=True,
                    tooltip="Return clean speech as WAV/AUDIO. Long AUDIO tensors can use substantial memory.",
                ),
                comfy_io.Boolean.Input("burn_translated_subtitles", default=False, optional=True),
                comfy_io.Video.Input("subtitle_video", optional=True),
                comfy_io.String.Input(
                    "subtitle_video_url",
                    default="",
                    optional=True,
                    tooltip="Optional separate picture source, timeline matched within one second. Requires burn-in and cannot combine with restoration.",
                ),
                _restoration_input(),
                _style_input(),
                comfy_io.String.Input("callback_url", default="", optional=True, advanced=True),
                _seed(),
            ],
            outputs=_output_contract(),
            hidden=_hidden(),
            not_idempotent=True,
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return _generation_fingerprint(cls)

    @classmethod
    @with_client("las_client", get_las_client)
    async def execute(
        cls,
        las_client,
        mode,
        output_tos_path,
        video=None,
        video_url="",
        dubbing_audio=None,
        subtitle_video=None,
        subtitle_video_url="",
        seed=0,
        **options,
    ):
        source = check_source(video, video_url, "video")
        picture = check_source(subtitle_video, subtitle_video_url, "subtitle_video", required=False)
        mode, nested_audio = _mode_values(mode)
        if nested_audio is not None:
            invalid("Connect external audio to the dubbing_audio socket, not inside mode.")
        audio = dubbing_audio
        if audio is not None:
            if mode.get("mode") != "lip_sync_external_audio":
                invalid("dubbing_audio only belongs to external-audio mode.")
            check_source(audio, mode.get("dubbing_audio_url"), "dubbing_audio")
            split_audio(audio, single=True)
        options["restoration"] = _restoration_values(options.get("restoration"))
        prepared_mode = (
            {**mode, "dubbing_audio_url": "https://example.com/connected-audio"} if audio is not None else mode
        )
        envelope = build_translation_request(
            source or "https://example.com/connected-video",
            prepared_mode,
            output_tos_path,
            **options,
            subtitle_video_url=picture
            or ("https://example.com/connected-picture" if subtitle_video is not None else ""),
        )
        metadata = await _metadata(video) if video is not None else None
        if metadata is not None:
            data = envelope["data"]
            validate_translation_metadata(
                metadata,
                lip_sync=data.get("lip_translate", False),
                external_audio=mode.get("mode") == "lip_sync_external_audio",
                restoration=data.get("enable_video_inpaint", False),
            )
            if subtitle_video is not None:
                validate_alignment(metadata, await _metadata(subtitle_video), "Separate picture source")
            if audio is not None and not mode.get("ignore_audio_video_duration_mismatch"):
                clip = split_audio(audio, single=True)[0]
                validate_alignment(
                    metadata, {"duration": clip["waveform"].shape[-1] / clip["sample_rate"]}, "Dubbed audio"
                )
        if video is not None:
            source = await upload_video(cls, video)
        if subtitle_video is not None:
            picture = await upload_video(cls, subtitle_video)
        if audio is not None:
            mode["dubbing_audio_url"] = (await upload_audio(cls, audio, single=True))[0]
        envelope = build_translation_request(source, mode, output_tos_path, **options, subtitle_video_url=picture)
        task = await submit_and_wait(las_client, envelope, _node_id(cls), rerun_seed=seed or 0)
        data = envelope["data"]
        result = await collect_translation(
            task,
            languages=data.get("output_languages"),
            primary_caption_format=options.get("primary_caption_format") or ".srt",
            download_audio=data.get("output_dubbing_audio", False),
            download_captions=mode.get("mode") == "translate",
            download_repaired_video=data.get("enable_video_inpaint", False),
            caption_formats=data.get("caption_formats"),
            api_key=las_client.api_key,
            require_audio=data.get("output_dubbing_audio", False),
            require_repaired_video=data.get("enable_video_inpaint", False),
        )
        if result.complete:
            clear_task_handle(las_client, envelope, _node_id(cls), rerun_seed=seed or 0)
        return comfy_io.NodeOutput(*result.values)


class BytePlusLASTaskResult(comfy_io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return comfy_io.Schema(
            node_id="BytePlusLASTaskResult",
            display_name="BytePlus LAS Task Result",
            category=LAS_CATEGORY,
            description="Poll and collect an existing LAS task without starting generation. Task IDs expire after three days; result links also expire. Requeue to check a pending task again.",
            inputs=[
                comfy_io.Combo.Input("operator", options=list(OPERATORS), default="Video Translation"),
                comfy_io.String.Input("task_id", default=""),
                comfy_io.Boolean.Input("wait_for_completion", default=True),
                comfy_io.Combo.Input("primary_caption_format", options=list(CAPTION_FORMATS), default=".srt"),
                comfy_io.Boolean.Input("download_audio", default=True, optional=True),
                comfy_io.Boolean.Input("download_captions", default=True, optional=True),
                comfy_io.Boolean.Input("download_repaired_video", default=True, optional=True),
                comfy_io.Boolean.Input("return_last_frame", default=False, optional=True),
            ],
            outputs=[*_output_contract(), comfy_io.Image.Output("last_frame")],
            hidden=[comfy_io.Hidden.unique_id],
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return float("nan")

    @classmethod
    @with_client("las_client", get_las_client)
    async def execute(
        cls,
        las_client,
        operator,
        task_id,
        wait_for_completion=True,
        primary_caption_format=".srt",
        download_audio=True,
        download_captions=True,
        download_repaired_video=True,
        return_last_frame=False,
    ):
        if operator not in OPERATORS:
            invalid("Unknown LAS operator.")
        operator_id = OPERATORS[operator]
        task = (
            await wait_for_las_task(las_client, operator_id, task_id, node_id=_node_id(cls))
            if wait_for_completion
            else await poll_las_task(las_client, operator_id, task_id)
        )
        if task["metadata"]["task_status"] != "COMPLETED":
            message = get_text("las_collection_pending", task_id=task_id, status=task["metadata"]["task_status"])
            send_node_text(_node_id(cls), message)
            blocked = ExecutionBlocker(None)
            response = json.dumps(redact_las_response(task, las_client.api_key), ensure_ascii=False)
            return comfy_io.NodeOutput([blocked], [blocked], [blocked], [""], blocked, "{}", task_id, response, blocked)
        if operator == "Genjutsu":
            result = await collect_genjutsu(task, return_last_frame=return_last_frame, api_key=las_client.api_key)
            video, frame, task_id, response = result.values
            blocked = ExecutionBlocker(None)
            return comfy_io.NodeOutput([video], [blocked], [blocked], [""], blocked, response, task_id, response, frame)
        result = await collect_translation(
            task,
            primary_caption_format=primary_caption_format,
            download_audio=download_audio,
            download_captions=download_captions,
            download_repaired_video=download_repaired_video,
            api_key=las_client.api_key,
        )
        return comfy_io.NodeOutput(*result.values, ExecutionBlocker(None))


NODES = [BytePlusLASGenjutsu, BytePlusLASVideoTranslation, BytePlusLASTaskResult]
