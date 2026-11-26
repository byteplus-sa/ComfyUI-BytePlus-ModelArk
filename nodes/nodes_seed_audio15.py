import asyncio
import json

from comfy_api.latest import io as comfy_io

from .core_style import seed_input
from .models_config import SEED_AUDIO_15_MODEL_MAP
from .nodes_shared import GLOBAL_CATEGORY, get_client, get_text, send_node_text, wait_interruptible, with_client
from .seed_audio15_api import (
    ENHANCEMENT_LEVELS,
    OUTPUT_FORMATS,
    SAMPLE_RATES,
    TARGET_LANGUAGES,
    build_request,
    generate,
    request_size,
    require_supported_region,
)
from .seed_audio15_media import collect_outputs, prepare_content, upload_video, url_value


def prompt_input(default=""):
    return comfy_io.String.Input(
        "text_prompt",
        multiline=True,
        default=default,
        tooltip="Describe voices, language, delivery, music and effects, including the exact lines to speak. "
        "Use @audio1 through @audio6 for ordered references and [0.5s:3.0s] for timing.",
    )


def audio_references():
    return [
        item
        for slot in range(1, 7)
        for item in (
            comfy_io.Audio.Input(
                f"reference_audio_{slot}",
                optional=True,
                tooltip=f"One 2-30 second voice clip, tagged @audio{slot}. Fill slots without gaps.",
            ),
            comfy_io.String.Input(
                f"ref_audio_{slot}_url",
                default="",
                optional=True,
                advanced=True,
                tooltip=f"Public WAV/MP3 URL instead of reference_audio_{slot}. Up to 15 MB.",
            ),
        )
    ]


def video_inputs():
    return [
        comfy_io.Video.Input("video", optional=True, tooltip="One 4-360 second video, uploaded through Comfy.org."),
        comfy_io.String.Input(
            "video_url",
            default="",
            optional=True,
            tooltip="Public MP4/MOV URL (up to 200 MB) or an authorized tos://bucket/object URI instead of video.",
        ),
    ]


def mode_options():
    return [
        comfy_io.DynamicCombo.Option("text to audio", [prompt_input()]),
        comfy_io.DynamicCombo.Option("audio reference", [prompt_input(), *audio_references()]),
        comfy_io.DynamicCombo.Option(
            "image reference",
            [
                prompt_input(),
                comfy_io.Image.Input("reference_image", optional=True),
                comfy_io.String.Input(
                    "ref_image_url",
                    default="",
                    optional=True,
                    advanced=True,
                    tooltip="Public character image URL instead of reference_image.",
                ),
            ],
        ),
        comfy_io.DynamicCombo.Option("video to audio", [prompt_input(), *video_inputs(), *audio_references()]),
        comfy_io.DynamicCombo.Option(
            "video translation",
            [
                *video_inputs(),
                comfy_io.Combo.Input("target_language", options=list(TARGET_LANGUAGES), default="en"),
                comfy_io.Combo.Input(
                    "source_language", options=["auto", "zh", "en"], default="auto", optional=True, advanced=True
                ),
                comfy_io.String.Input(
                    "custom_subtitle_url",
                    default="",
                    optional=True,
                    advanced=True,
                    tooltip="Public UTF-8 target-language SRT URL; skips translation. Each segment must be at most 15 seconds. Cannot combine with glossaries.",
                ),
                comfy_io.String.Input(
                    "glossaries",
                    default="",
                    multiline=True,
                    optional=True,
                    advanced=True,
                    tooltip='JSON array of translation pairs, e.g. [{"source":"brand","target":"BytePlus"}]. Cannot combine with custom subtitles.',
                ),
                comfy_io.Combo.Input(
                    "dialogue_enhancement",
                    options=list(ENHANCEMENT_LEVELS),
                    default="off",
                    optional=True,
                    advanced=True,
                ),
            ],
        ),
        comfy_io.DynamicCombo.Option(
            "stem separation",
            [
                prompt_input("Separate this audio into three tracks: all vocals, background music, and sound effects."),
                comfy_io.Audio.Input(
                    "audio", optional=True, tooltip="One 2-360 second audio clip, up to 90 MB before Base64 encoding."
                ),
                comfy_io.String.Input(
                    "audio_url",
                    default="",
                    optional=True,
                    tooltip="Public WAV/MP3 URL instead of audio. Recommended for large sources.",
                ),
            ],
        ),
    ]


class BytePlusSeedAudio15(comfy_io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return comfy_io.Schema(
            node_id="BytePlusSeedAudio15",
            display_name="BytePlus Seed Audio 1.5",
            category=f"{GLOBAL_CATEGORY}/Audio",
            description="Generate full-scene audio, clone up to six reference voices, create audio for a video, "
            "translate Chinese or English video into 30 target languages, or separate audio tracks. "
            "Uses the ModelArk API key, not the Seed Speech key. Each run submits one billed synchronous request. "
            "Cancel stops local waiting only; accepted work may continue and be billed. Use only authorized media.",
            search_aliases=["Seed Audio 1.5", "audio generation", "video dubbing", "stem separation"],
            inputs=[
                comfy_io.DynamicCombo.Input("mode", options=mode_options()),
                comfy_io.Combo.Input("sample_rate", options=list(SAMPLE_RATES), default="44100"),
                seed_input(
                    default=42,
                    tooltip="Local ComfyUI rerun control only. Not sent to Seed Audio 1.5; it does not make audio deterministic.",
                ),
                comfy_io.Combo.Input(
                    "model",
                    options=list(SEED_AUDIO_15_MODEL_MAP),
                    default="dola-seed-audio-1-5",
                    optional=True,
                    advanced=True,
                ),
                comfy_io.Combo.Input(
                    "output_format",
                    options=list(OUTPUT_FORMATS),
                    default="wav",
                    optional=True,
                    advanced=True,
                    tooltip="Self-describing audio format for decoding. Ogg Opus requires 48000 Hz. Raw PCM is not exposed because its channel layout is undocumented.",
                ),
                comfy_io.Int.Input(
                    "speech_rate",
                    default=0,
                    min=-50,
                    max=100,
                    optional=True,
                    advanced=True,
                    tooltip="Audio generation only. Keep 0 for translation or separation.",
                ),
                comfy_io.Int.Input(
                    "loudness_rate",
                    default=0,
                    min=-50,
                    max=100,
                    optional=True,
                    advanced=True,
                    tooltip="Audio generation only. Keep 0 for translation or separation.",
                ),
                comfy_io.Int.Input(
                    "pitch_rate",
                    default=0,
                    min=-12,
                    max=12,
                    optional=True,
                    advanced=True,
                    tooltip="Audio generation only. Keep 0 for translation or separation.",
                ),
                comfy_io.Boolean.Input(
                    "include_video",
                    default=False,
                    optional=True,
                    advanced=True,
                    tooltip="Request the output video in video-to-audio or translation mode.",
                ),
                comfy_io.Boolean.Input(
                    "include_subtitles",
                    default=False,
                    optional=True,
                    advanced=True,
                    tooltip="Request SRT and JSONL subtitle files.",
                ),
                comfy_io.Boolean.Input(
                    "include_source_subtitles",
                    default=False,
                    optional=True,
                    advanced=True,
                    tooltip="Translation only: also request the source-language SRT and JSONL.",
                ),
            ],
            outputs=[
                comfy_io.Audio.Output(display_name="audio", is_output_list=True),
                comfy_io.Video.Output(display_name="video"),
                comfy_io.String.Output(display_name="srt"),
                comfy_io.String.Output(display_name="subtitles_jsonl"),
                comfy_io.String.Output(display_name="source_srt"),
                comfy_io.String.Output(display_name="source_subtitles_jsonl"),
                comfy_io.String.Output(display_name="track_type", is_output_list=True),
                comfy_io.String.Output(display_name="track_description", is_output_list=True),
                comfy_io.Float.Output(display_name="duration", is_output_list=True),
                comfy_io.String.Output(display_name="audio_url", is_output_list=True),
                comfy_io.String.Output(display_name="generation_id"),
                comfy_io.String.Output(display_name="response"),
            ],
            hidden=[comfy_io.Hidden.unique_id, comfy_io.Hidden.auth_token_comfy_org, comfy_io.Hidden.api_key_comfy_org],
        )

    @classmethod
    @with_client("client", get_client)
    async def execute(
        cls,
        client,
        mode,
        sample_rate="44100",
        seed=42,
        model="dola-seed-audio-1-5",
        output_format="wav",
        speech_rate=0,
        loudness_rate=0,
        pitch_rate=0,
        include_video=False,
        include_subtitles=False,
        include_source_subtitles=False,
    ):
        # The seed is only a ComfyUI rerun control, not a provider parameter.
        require_supported_region(client)
        options = mode if isinstance(mode, dict) else {}
        content, video = await wait_interruptible(asyncio.to_thread(prepare_content, options))
        body = build_request(
            options.get("mode"),
            content,
            model,
            output_format,
            sample_rate,
            speech_rate,
            loudness_rate,
            pitch_rate,
            include_video,
            include_subtitles,
            include_source_subtitles,
            options.get("target_language"),
            options.get("source_language"),
            url_value(options.get("custom_subtitle_url")),
            options.get("glossaries"),
            options.get("dialogue_enhancement"),
        )
        if video is not None:
            url = await upload_video(cls, video)
            next(item for item in body["content"] if item["type"] == "video_url")["video_url"]["url"] = url
        request_size(body)
        send_node_text(cls.hidden.unique_id, get_text("seed_audio15_generating"))
        response = await generate(client, body)
        send_node_text(cls.hidden.unique_id, get_text("seed_audio15_downloading"))
        values = await collect_outputs(response, body.get("include", []))
        return comfy_io.NodeOutput(*values, response["id"], json.dumps(response, ensure_ascii=False))


NODES = [BytePlusSeedAudio15]
