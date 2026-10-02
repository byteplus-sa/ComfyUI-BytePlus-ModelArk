import asyncio
import base64
import hashlib
import io
import json
import math
import os
import re
import uuid

import comfy.model_management
from comfy_api.latest import io as comfy_io

from .audio_utils import (
    audio_duration,
    audio_waveform,
    audio_to_wav_bytes,
    build_srt,
    decode_audio_bytes,
    pcm16_to_audio,
    subtitle_segments,
)
from .constants import (
    DEFAULT_SPEECH_REGION,
    SEED_ASR_AUDIO_FORMATS,
    SEED_ASR_CONTEXT_IMAGE_MAX_PIXELS,
    SEED_ASR_CONTEXT_LANGUAGES,
    SEED_ASR_EXTENSION_FORMATS,
    SEED_ASR_FAST_FORMATS,
    SEED_ASR_FAST_MAX_BYTES,
    SEED_ASR_FAST_PATH,
    SEED_ASR_LANGUAGES,
    SEED_ASR_MAX_WAIT_SECONDS,
    SEED_ASR_QUERY_PATH,
    SEED_ASR_SAMPLE_RATE,
    SEED_ASR_STANDARD_ONLY_LANGUAGES,
    SEED_ASR_SUBMIT_PATH,
    SEED_ASR_ZH_VARIANTS,
    SEED_AUDIO_DEFAULT_SAMPLE_RATE,
    SEED_AUDIO_FORMATS,
    SEED_AUDIO_IMAGE_MAX_PIXELS,
    SEED_AUDIO_IMAGE_MIN_PIXELS,
    SEED_AUDIO_MAX_GENERATION_COUNT,
    SEED_AUDIO_MAX_AUDIO_REFS,
    SEED_AUDIO_MAX_PROMPT_CHARS,
    SEED_AUDIO_PATH,
    SEED_AUDIO_REF_MAX_BYTES,
    SEED_AUDIO_REF_MAX_SECONDS,
    SEED_AUDIO_SAMPLE_RATES,
    SEED_CUSTOM_VOICE_ID_REJECT,
    SEED_TTS_2_SAMPLE_RATES,
    SEED_TTS_APP_KEY,
    SEED_TTS_CONTEXT_LANGUAGES,
    SEED_TTS_DEFAULT_UNSUPPORTED_CHAR_RATIO,
    SEED_TTS_LANGUAGES,
    SEED_TTS_PATH,
    SEED_TTS_SAMPLE_RATES,
    SEED_VOICE_CLONE_LANGUAGES,
    SEED_VOICE_CLONE_MAX_BYTES,
    SEED_VOICE_CLONE_PATH,
    SEED_VOICE_FAILED_STATUS,
    SEED_VOICE_NOT_FOUND_STATUS,
    SEED_VOICE_POLL_SECONDS,
    SEED_VOICE_READY_STATUSES,
    SEED_VOICE_STATUS_PATH,
    SEED_VOICE_TRAINING_TIMEOUT_SECONDS,
    SPEECH_API_KEY_ENV,
    SPEECH_ASR_PENDING_CODES,
    SPEECH_ASR_POLL_SECONDS,
    SPEECH_ASR_SILENT_AUDIO_CODE,
    SPEECH_REGION_BASE_URLS,
    SPEECH_UPLOAD_CACHE_MAX_ENTRIES,
    SPEECH_UPLOAD_CACHE_TTL_SECONDS,
)
from .models_config import (
    SEED_ASR_MODELS,
    SEED_ASR_UI_OPTIONS,
    SEED_AUDIO_MODELS,
    SEED_TTS_2_UI_MODEL,
    SEED_TTS_MODELS,
)
from . import credentials
from .nodes_shared import (
    with_default_client,
    GLOBAL_CATEGORY,
    BytePlusException,
    _notify_api_key_saved,
    _tensor2images,
    gather_cancelling,
    get_text,
    log_msg,
    sleep_interruptible,
    upload_bytes_to_comfy_storage,
)
from .core_style import core_search_aliases, seed_input
from .seed_speech_voices import DEFAULT_TTS_VOICE, TTS_2_VOICE_IDS, TTS_2_VOICES
from .speech_api import (
    SPEECH_API_KEY_STORE,
    BytePlusSpeechClientType,
    SeedSpeechClient,
    b64decode_audio,
    check_code,
    download_bytes,
    iter_json_objects,
    require_speech_client,
    speech_error,
    speech_poll,
    speech_post,
)

SPEECH_CATEGORY = f"{GLOBAL_CATEGORY}/Speech"
ENV_KEY_OPTION = f"Environment ({SPEECH_API_KEY_ENV})"
SPEECH_CLIENT_TOOLTIP = (
    'Optional. Without it the node uses the Seed Speech key from Settings > BytePlus (BYTEPLUS_SEED_SPEECH_API_KEY or user/.env). Connect a BytePlus Speech Client node to use another key.'
)


def build_default_speech_client():
    """SeedSpeechClient for the default Seed Speech key; raises when none is set."""
    api_key = credentials.get_setting(SPEECH_API_KEY_ENV)
    if not api_key:
        # speech_api_keys.json is only used when it leaves no doubt which key is meant.
        SPEECH_API_KEY_STORE.load()
        names = SPEECH_API_KEY_STORE.get_key_names()
        if len(names) == 1:
            api_key = SPEECH_API_KEY_STORE.find_api_key(names[0])
    if not api_key:
        raise BytePlusException(get_text("err_no_default_speech_key", path=credentials.env_file_path()))
    return SeedSpeechClient(api_key, DEFAULT_SPEECH_REGION)


USER_ID = "comfyui"
SPEECH_MAX_SEED = 0xffffffffffffffff


def _is_url(value, schemes=("http://", "https://")):
    return value.lower().startswith(schemes)


def _validated_url(value, field, schemes=("http://", "https://")):
    value = (value or "").strip()
    if value and not _is_url(value, schemes):
        raise BytePlusException(get_text("speech_bad_url", field=field))
    return value


def _size_mb(num_bytes):
    return f"{num_bytes / (1024 * 1024):.2f}"


def fit_image_pixels(image, min_pixels=SEED_AUDIO_IMAGE_MIN_PIXELS, max_pixels=SEED_AUDIO_IMAGE_MAX_PIXELS):
    """
    First image of a batch, scaled (aspect ratio kept) so that its pixel count is
    within [min_pixels, max_pixels]. Core upscales Seed Audio reference images to
    at least 160,000 px the same way.
    """
    import comfy.utils

    image = image[:1] if image.ndim == 4 else image[None]
    height, width = int(image.shape[1]), int(image.shape[2])
    pixels = width * height
    if min_pixels <= pixels <= max_pixels:
        return image
    scale = math.sqrt((min_pixels if pixels < min_pixels else max_pixels) / pixels)
    if pixels < min_pixels:
        new_width, new_height = math.ceil(width * scale), math.ceil(height * scale)
        method = "lanczos"
    else:
        new_width, new_height = max(1, int(width * scale)), max(1, int(height * scale))
        method = "area"
    samples = comfy.utils.common_upscale(image.movedim(-1, 1), new_width, new_height, method, "disabled")
    return samples.movedim(1, -1).clamp(0.0, 1.0)


def _image_to_jpeg_base64(image):
    with io.BytesIO() as buffer:
        _tensor2images(fit_image_pixels(image))[0].convert("RGB").save(buffer, format="JPEG", quality=95)
        data = buffer.getvalue()
    if len(data) > SEED_AUDIO_REF_MAX_BYTES:
        raise BytePlusException(get_text(
            "seed_audio_ref_too_large", kind="image", size_mb=_size_mb(len(data)),
            max_mb=_size_mb(SEED_AUDIO_REF_MAX_BYTES),
        ))
    return base64.b64encode(data).decode("utf-8")


def _segments_json(raw, segments):
    return json.dumps({"raw": raw, "segments": segments}, ensure_ascii=False)


# Connected media -> Comfy.org storage URL, for Seed Speech options that only
# take URLs (standard ASR audio, ASR context image). Needs a Comfy.org login;
# the helper is internal to ComfyUI and missing with --disable-api-nodes.
SPEECH_UPLOAD_CACHE = {}


async def upload_to_comfy_storage(node_cls, kind, data, filename, mime_type):
    return await upload_bytes_to_comfy_storage(
        node_cls,
        data,
        filename,
        mime_type,
        SPEECH_UPLOAD_CACHE,
        cache_key=(kind, hashlib.sha256(data).hexdigest()),
        ttl_seconds=SPEECH_UPLOAD_CACHE_TTL_SECONDS,
        max_entries=SPEECH_UPLOAD_CACHE_MAX_ENTRIES,
        unavailable_key="speech_upload_unavailable",
        failed_key="speech_upload_failed",
        done_key="speech_upload_done",
        kind=kind,
    )


def context_image_jpeg(image, max_bytes=500 * 1024):
    """JPEG within the ASR visual-context limit (500 KB), downscaled if needed."""
    picture = _tensor2images(image)[0].convert("RGB")
    scale = min(1.0, (SEED_ASR_CONTEXT_IMAGE_MAX_PIXELS / float(picture.width * picture.height)) ** 0.5)
    for quality in (90, 80, 70, 60, 50):
        size = (max(1, int(picture.width * scale)), max(1, int(picture.height * scale)))
        with io.BytesIO() as buffer:
            picture.resize(size).save(buffer, format="JPEG", quality=quality)
            data = buffer.getvalue()
        if len(data) <= max_bytes:
            return data
        scale *= 0.8
    return data


class BytePlusSpeechClient(comfy_io.ComfyNode):
    """
    Seed Speech API client: picks the Seed Speech API key (not a ModelArk key)
    for the Seed Audio, TTS and ASR nodes.
    """
    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        SPEECH_API_KEY_STORE.load()
        key_names = SPEECH_API_KEY_STORE.get_key_names() + [ENV_KEY_OPTION, "Custom"]
        return comfy_io.Schema(
            node_id="BytePlusSpeechClient",
            display_name="BytePlus Speech Client",
            category=SPEECH_CATEGORY,
            description=(
                "Seed Speech API key for Seed Audio, TTS and ASR. Create it in the Seed "
                "Speech console (Settings > API Keys); ModelArk keys do not work here."
            ),
            inputs=[
                comfy_io.String.Input("new_api_key", default=""),
                comfy_io.String.Input("new_key_name", default=""),
                comfy_io.Combo.Input(
                    "key_name",
                    options=key_names,
                    tooltip=(
                        "Saved Seed Speech key, the BYTEPLUS_SEED_SPEECH_API_KEY environment "
                        "variable, or Custom to paste a key (saved under new_key_name)."
                    ),
                ),
                comfy_io.Combo.Input(
                    "region",
                    options=list(SPEECH_REGION_BASE_URLS.keys()),
                    default=DEFAULT_SPEECH_REGION,
                    tooltip="Seed Speech region. Singapore is the only Seed Speech endpoint.",
                ),
            ],
            outputs=[BytePlusSpeechClientType.Output(display_name="speech_client")],
            hidden=[comfy_io.Hidden.unique_id],
        )

    @classmethod
    def execute(cls, key_name, new_api_key="", new_key_name="", region=DEFAULT_SPEECH_REGION) -> comfy_io.NodeOutput:
        if key_name == "Custom":
            api_key = (new_api_key or "").strip()
            if not api_key:
                raise BytePlusException(get_text("speech_key_empty"))
            name = (new_key_name or "").strip()
            if name:
                if SPEECH_API_KEY_STORE.upsert(name, api_key):
                    log_msg("speech_key_saved", name=name)
                    _notify_api_key_saved(cls.hidden.unique_id, name, api_key, store="speech")
                else:
                    # Keep the pasted key in the node so it is not lost.
                    log_msg("speech_key_save_failed", name=name)
        elif key_name == ENV_KEY_OPTION:
            api_key = credentials.get_setting(SPEECH_API_KEY_ENV)
            if not api_key:
                raise BytePlusException(get_text("speech_env_key_missing", env=SPEECH_API_KEY_ENV))
        else:
            SPEECH_API_KEY_STORE.load()
            api_key = SPEECH_API_KEY_STORE.find_api_key(key_name)
            if not api_key:
                raise BytePlusException(get_text("speech_key_not_found", key_name=key_name))
        return comfy_io.NodeOutput(SeedSpeechClient(api_key, region))


# Seed Audio 1.0, shaped like ComfyUI core's ByteDanceSeedAudioNode (nodes_bytedance.py).

SEED_AUDIO_MODE_TEXT = "text only"
SEED_AUDIO_MODE_AUDIO = "audio reference"
SEED_AUDIO_MODE_IMAGE = "image reference"
SEED_AUDIO_MODE_PRESET = "preset voice"

def _voice_name(speaker_id, name):
    """The listed name, or one derived from the ID when the official list has none."""
    if name:
        return name
    parts = speaker_id.split("_")
    return " ".join(parts[2:-2]).title() or speaker_id


# Preset voices: this pack's TTS 2.0 voice list (Seed Audio accepts TTS 2.0
# speaker IDs), labelled "Name (Gender, language)" like core's list.
SEED_AUDIO_PRESET_VOICES = [
    (speaker_id, f"{_voice_name(speaker_id, name)} ({gender}, {language})")
    for speaker_id, name, language, gender, _scenario in TTS_2_VOICES
]
SEED_AUDIO_VOICE_OPTIONS = [label for _, label in SEED_AUDIO_PRESET_VOICES]
SEED_AUDIO_VOICE_MAP = {label: speaker_id for speaker_id, label in SEED_AUDIO_PRESET_VOICES}

_AUDIO_TAG_RE = re.compile(r"@Audio(\d+)", re.IGNORECASE)


def max_audio_tag(prompt):
    """Highest N referenced as @AudioN in the prompt (0 if none)."""
    numbers = [int(n) for n in _AUDIO_TAG_RE.findall(prompt or "")]
    return max(numbers) if numbers else 0


def seed_audio_slots(reference_mode):
    """
    Filled audio reference slots of the 'audio reference' option:
    {N: ("audio", AUDIO) | ("source", speaker ID or URL)}. Slot N is @AudioN.
    """
    slots = {}
    for slot in range(1, SEED_AUDIO_MAX_AUDIO_REFS + 1):
        audio = reference_mode.get(f"reference_audio_{slot}")
        source = (reference_mode.get(f"ref_audio_{slot}_source") or "").strip()
        if audio is not None and source:
            raise BytePlusException(get_text("seed_audio_slot_conflict", slot=slot))
        if audio is not None:
            slots[slot] = ("audio", audio)
        elif source:
            slots[slot] = ("source", source)
    return slots


def validate_seed_audio_inputs(text_prompt, mode, audio_slots, has_image, preset_voice=None):
    """Core's validate_seed_audio_inputs; audio_slots are the filled slot numbers in order."""
    text = (text_prompt or "").strip()
    if not text:
        raise BytePlusException(get_text("seed_audio_prompt_empty"))
    if len(text) > SEED_AUDIO_MAX_PROMPT_CHARS:
        raise BytePlusException(get_text(
            "seed_audio_prompt_too_long", count=len(text), max=SEED_AUDIO_MAX_PROMPT_CHARS
        ))
    max_tag = max_audio_tag(text)
    if mode == SEED_AUDIO_MODE_TEXT:
        if max_tag:
            raise BytePlusException(get_text("seed_audio_tag_text_only", tag=max_tag))
    elif mode == SEED_AUDIO_MODE_AUDIO:
        if not audio_slots:
            raise BytePlusException(get_text("seed_audio_needs_reference"))
        expected = list(range(1, len(audio_slots) + 1))
        if list(audio_slots) != expected:
            missing = next(slot for slot in range(1, max(audio_slots) + 1) if slot not in audio_slots)
            raise BytePlusException(get_text("seed_audio_slot_gap", slot=max(audio_slots), missing=missing))
        if max_tag > len(audio_slots):
            raise BytePlusException(get_text("seed_audio_tag_out_of_range", tag=max_tag, count=len(audio_slots)))
    elif mode == SEED_AUDIO_MODE_IMAGE:
        if not has_image:
            raise BytePlusException(get_text("seed_audio_image_required"))
        if max_tag:
            raise BytePlusException(get_text("seed_audio_tag_image_mode"))
    elif mode == SEED_AUDIO_MODE_PRESET:
        if not preset_voice or preset_voice not in SEED_AUDIO_VOICE_MAP:
            raise BytePlusException(get_text("seed_audio_preset_required"))
        if max_tag > 1:
            raise BytePlusException(get_text("seed_audio_tag_preset_mode", tag=max_tag))
    else:
        raise BytePlusException(get_text("seed_audio_unknown_mode", mode=mode))


def _audio_reference(slot, audio):
    duration = audio_duration(audio)
    if duration > SEED_AUDIO_REF_MAX_SECONDS:
        raise BytePlusException(get_text(
            "seed_audio_ref_too_long", slot=slot, duration=f"{duration:.1f}",
            max=int(SEED_AUDIO_REF_MAX_SECONDS),
        ))
    wav = audio_to_wav_bytes(audio)
    if len(wav) > SEED_AUDIO_REF_MAX_BYTES:
        # 16 kHz mono keeps 30 s of speech far below the limit.
        wav = audio_to_wav_bytes(audio, sample_rate=16000, mono=True)
    if len(wav) > SEED_AUDIO_REF_MAX_BYTES:
        raise BytePlusException(get_text(
            "seed_audio_ref_too_large", kind=f"audio {slot}", size_mb=_size_mb(len(wav)),
            max_mb=_size_mb(SEED_AUDIO_REF_MAX_BYTES),
        ))
    return {"audio_data": base64.b64encode(wav).decode("utf-8")}


def build_seed_audio_references(mode, audio_slots=None, image=None, image_url="", preset_voice=None):
    """
    references[] for the selected reference mode (None for text only). Audio
    slot N is @AudioN: connected clips are sent inline, sources as a speaker ID
    or an audio URL. Call validate_seed_audio_inputs first.
    """
    if mode == SEED_AUDIO_MODE_AUDIO:
        references = []
        for slot, (kind, value) in sorted((audio_slots or {}).items()):
            if kind == "audio":
                references.append(_audio_reference(slot, value))
            elif "://" in value:
                references.append({"audio_url": _validated_url(value, f"ref_audio_{slot}_source")})
            else:
                references.append({"speaker": value})
        return references
    if mode == SEED_AUDIO_MODE_IMAGE:
        if image is not None:
            return [{"image_data": _image_to_jpeg_base64(image)}]
        return [{"image_url": _validated_url(image_url, "ref_image_url")}]
    if mode == SEED_AUDIO_MODE_PRESET:
        return [{"speaker": SEED_AUDIO_VOICE_MAP[preset_voice]}]
    return None


def build_seed_audio_request(model, text_prompt, references, audio_format="wav",
                             sample_rate=SEED_AUDIO_DEFAULT_SAMPLE_RATE, speech_rate=0, loudness_rate=0,
                             pitch_rate=0, enable_subtitle=False, aigc_watermark=False, aigc_metadata=False,
                             content_producer="", produce_id="", content_propagator="", propagate_id=""):
    text_prompt = (text_prompt or "").strip()
    if not text_prompt:
        raise BytePlusException(get_text("seed_audio_prompt_empty"))
    if len(text_prompt) > SEED_AUDIO_MAX_PROMPT_CHARS:
        raise BytePlusException(get_text(
            "seed_audio_prompt_too_long", count=len(text_prompt), max=SEED_AUDIO_MAX_PROMPT_CHARS
        ))
    audio_config = {
        "format": audio_format,
        "sample_rate": int(sample_rate),
        "speech_rate": int(speech_rate),
        "loudness_rate": int(loudness_rate),
        "pitch_rate": int(pitch_rate),
    }
    if enable_subtitle:
        audio_config["enable_subtitle"] = True
    body = {"model": model, "text_prompt": text_prompt}
    if references:
        body["references"] = references
    body["audio_config"] = audio_config
    watermark = {}
    if aigc_watermark:
        watermark["aigc_watermark"] = True
    if aigc_metadata:
        metadata = {"enable": True}
        for key, value in (
            ("content_producer", content_producer),
            ("produce_id", produce_id),
            ("content_propagator", content_propagator),
            ("propagate_id", propagate_id),
        ):
            if (value or "").strip():
                metadata[key] = value.strip()
        watermark["aigc_metadata"] = metadata
    if watermark:
        body["watermark"] = watermark
    return body


def _seed_audio_reference_options():
    """Core's reference_mode options; this pack's reference strings follow core's sockets."""
    source_tooltip = (
        "Instead of reference_audio_{n}: a TTS 2.0 or cloned speaker ID, or an audio URL "
        "(http(s)://). Tagged @Audio{n} in the prompt."
    )
    return [
        comfy_io.DynamicCombo.Option(SEED_AUDIO_MODE_TEXT, []),
        comfy_io.DynamicCombo.Option(
            SEED_AUDIO_MODE_AUDIO,
            [
                comfy_io.Audio.Input(
                    "reference_audio_1",
                    optional=True,
                    tooltip="Reference clip for voice cloning, tagged @Audio1 in the prompt. Up to 30s.",
                ),
                comfy_io.Audio.Input(
                    "reference_audio_2",
                    optional=True,
                    tooltip="Reference clip tagged @Audio2 in the prompt. Up to 30s.",
                ),
                comfy_io.Audio.Input(
                    "reference_audio_3",
                    optional=True,
                    tooltip="Reference clip tagged @Audio3 in the prompt. Up to 30s.",
                ),
                *[
                    comfy_io.String.Input(
                        f"ref_audio_{n}_source",
                        default="",
                        tooltip=source_tooltip.format(n=n),
                        optional=True,
                        advanced=True,
                    )
                    for n in range(1, SEED_AUDIO_MAX_AUDIO_REFS + 1)
                ],
            ],
        ),
        comfy_io.DynamicCombo.Option(
            SEED_AUDIO_MODE_IMAGE,
            [
                comfy_io.Image.Input(
                    "reference_image",
                    optional=True,
                    tooltip="A single character image; the model derives a voice from it. "
                    "Cannot be combined with reference audio.",
                ),
                comfy_io.String.Input(
                    "ref_image_url",
                    default="",
                    tooltip="Instead of reference_image: a character image URL (http(s)://).",
                    optional=True,
                    advanced=True,
                ),
            ],
        ),
        comfy_io.DynamicCombo.Option(
            SEED_AUDIO_MODE_PRESET,
            [
                comfy_io.Combo.Input(
                    "preset_voice",
                    options=SEED_AUDIO_VOICE_OPTIONS,
                    default=SEED_AUDIO_VOICE_OPTIONS[0],
                    tooltip="A built-in TTS 2.0 voice that reads the prompt. No reference "
                    "clip needed, and @AudioN tags are not used in this mode.",
                ),
            ],
        ),
    ]


class BytePlusSeedAudio(comfy_io.ComfyNode):
    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return comfy_io.Schema(
            node_id="BytePlusSeedAudio",
            display_name="BytePlus Seed Audio 1.0",
            search_aliases=core_search_aliases("BytePlusSeedAudio"),
            category=SPEECH_CATEGORY,
            description=(
                "Generate speech, music, sound effects and multi-speaker dialogue from a single prompt "
                "with BytePlus Seed Audio 1.0. Describe the voice(s), emotion, ambience, background music "
                "and sound effects in the prompt, and include the lines to speak. Optionally pick a built-in "
                "preset voice, clone voices from up to 3 reference clips (tagged @Audio1-3 in the prompt), "
                "or derive a voice from a character image. Up to 2 minutes of audio per run. "
                "Supports 20 languages and timestamp-based timing control."
            ),
            inputs=[
                BytePlusSpeechClientType.Input("speech_client", optional=True, tooltip=SPEECH_CLIENT_TOOLTIP),
                comfy_io.String.Input(
                    "text_prompt",
                    multiline=True,
                    default="",
                    tooltip=(
                        "Describe the voice(s), emotion, pacing, ambience, background music and sound "
                        "effects, and include the lines to speak (name characters inline for dialogue). "
                        "In 'audio reference' mode, refer to connected clips by order as @Audio1, @Audio2, "
                        "@Audio3. A quoted line can start with a timestamp range that controls when and "
                        'how long it is spoken, e.g. "[5.5s:8.0s] Wait for me!". Write the prompt in the '
                        "same language as the lines to speak. Maximum 3000 characters."
                    ),
                ),
                comfy_io.DynamicCombo.Input(
                    "reference_mode",
                    options=_seed_audio_reference_options(),
                    tooltip=(
                        "How to condition the voice: 'text only' (describe everything in the prompt), "
                        "'audio reference' (clone up to 3 voices, tagged @Audio1-3), 'image reference' "
                        "(derive a voice from one character image), or 'preset voice' (pick a built-in "
                        "named voice that reads the prompt)."
                    ),
                ),
                comfy_io.Combo.Input(
                    "sample_rate",
                    options=SEED_AUDIO_SAMPLE_RATES,
                    default=SEED_AUDIO_DEFAULT_SAMPLE_RATE,
                    tooltip="Output sample rate in Hz.",
                ),
                comfy_io.Int.Input(
                    "speech_rate",
                    default=0,
                    min=-50,
                    max=100,
                    tooltip="Speaking speed. 0 = normal, 100 = 2.0x, -50 = 0.5x.",
                ),
                comfy_io.Int.Input(
                    "loudness_rate",
                    default=0,
                    min=-50,
                    max=100,
                    tooltip="Loudness. 0 = normal, 100 = 2.0x, -50 = 0.5x.",
                ),
                comfy_io.Int.Input(
                    "pitch_rate",
                    default=0,
                    min=-12,
                    max=12,
                    tooltip="Pitch shift in semitones (-12 to 12).",
                ),
                seed_input(default=42),
                comfy_io.Combo.Input(
                    "model",
                    options=SEED_AUDIO_MODELS,
                    default=SEED_AUDIO_MODELS[0],
                    optional=True,
                    tooltip=(
                        "seed-audio-1.0: 20 languages (English, Chinese, Japanese, Korean, Mexican & "
                        "Castilian Spanish, Indonesian, German, Brazilian Portuguese, French, Thai, "
                        "Vietnamese, Malay, Filipino, Italian, Russian, Dutch, Polish, Turkish, Swedish) "
                        'plus per-sentence timing control via "[5.5s:8.0s] ..." timestamps.'
                    ),
                ),
                comfy_io.Combo.Input(
                    "audio_format",
                    options=SEED_AUDIO_FORMATS,
                    default="wav",
                    tooltip="Format requested from the API (the output is decoded to AUDIO either way).",
                    optional=True,
                    advanced=True,
                ),
                comfy_io.Boolean.Input(
                    "enable_subtitle",
                    default=False,
                    tooltip="Return sentence and word timestamps (subtitles_json and srt outputs).",
                    optional=True,
                    advanced=True,
                ),
                comfy_io.Boolean.Input(
                    "aigc_watermark",
                    default=False,
                    tooltip="Add the audible AI-generated marker at the end of the audio.",
                    optional=True,
                    advanced=True,
                ),
                comfy_io.Boolean.Input(
                    "aigc_metadata",
                    default=False,
                    tooltip="Implicit watermark: write AI-generation metadata into the audio header.",
                    optional=True,
                    advanced=True,
                ),
                comfy_io.String.Input("content_producer", default="", optional=True, advanced=True,
                                      tooltip="Implicit watermark: name or code of the synthesis provider."),
                comfy_io.String.Input("produce_id", default="", optional=True, advanced=True,
                                      tooltip="Implicit watermark: content production ID."),
                comfy_io.String.Input("content_propagator", default="", optional=True, advanced=True,
                                      tooltip="Implicit watermark: name or code of the distributor."),
                comfy_io.String.Input("propagate_id", default="", optional=True, advanced=True,
                                      tooltip="Implicit watermark: content distribution ID."),
                comfy_io.Int.Input(
                    "generation_count",
                    default=1,
                    min=1,
                    max=SEED_AUDIO_MAX_GENERATION_COUNT,
                    optional=True,
                    advanced=True,
                    tooltip=(
                        "Number of separate generations to run in parallel, each billed as its own request. "
                        "The API has no seed or variation setting, so every run is a fresh take of the same "
                        "prompt. All outputs are lists in the same order: the next node runs once per clip."
                    ),
                ),
            ],
            outputs=[
                comfy_io.Audio.Output(is_output_list=True),
                comfy_io.String.Output(display_name="subtitles_json", is_output_list=True),
                comfy_io.String.Output(display_name="srt", is_output_list=True),
                comfy_io.Float.Output(display_name="duration", is_output_list=True),
                comfy_io.String.Output(display_name="url", is_output_list=True),
            ],
            hidden=[comfy_io.Hidden.unique_id],
        )

    @classmethod
    @with_default_client("speech_client", build_default_speech_client)
    async def execute(cls, speech_client, text_prompt, reference_mode, sample_rate=SEED_AUDIO_DEFAULT_SAMPLE_RATE,
                      speech_rate=0, loudness_rate=0, pitch_rate=0, seed=42, model=SEED_AUDIO_MODELS[0],
                      audio_format="wav", enable_subtitle=False, aigc_watermark=False, aigc_metadata=False,
                      content_producer="", produce_id="", content_propagator="",
                      propagate_id="", generation_count=1) -> comfy_io.NodeOutput:
        require_speech_client(speech_client)
        reference_mode = reference_mode or {}
        mode = reference_mode.get("reference_mode")
        audio_slots = seed_audio_slots(reference_mode) if mode == SEED_AUDIO_MODE_AUDIO else {}
        image = image_url = preset_voice = None
        if mode == SEED_AUDIO_MODE_IMAGE:
            image = reference_mode.get("reference_image")
            image_url = (reference_mode.get("ref_image_url") or "").strip()
            if image is not None and image_url:
                raise BytePlusException(get_text("seed_audio_image_conflict"))
        elif mode == SEED_AUDIO_MODE_PRESET:
            preset_voice = reference_mode.get("preset_voice")
        validate_seed_audio_inputs(
            text_prompt, mode, sorted(audio_slots), image is not None or bool(image_url), preset_voice
        )
        references = build_seed_audio_references(mode, audio_slots, image, image_url, preset_voice)
        # seed only makes ComfyUI re-run the node; the API has no seed parameter.
        body = build_seed_audio_request(
            model or SEED_AUDIO_MODELS[0], text_prompt, references, audio_format, sample_rate, speech_rate,
            loudness_rate, pitch_rate, enable_subtitle, aigc_watermark, aigc_metadata,
            content_producer, produce_id, content_propagator, propagate_id,
        )
        count = max(1, int(generation_count or 1))
        if count > 1:
            log_msg("batch_submit_start", count=count, model=model or SEED_AUDIO_MODELS[0])
        # An interrupt cancels the other requests; other failures are collected below.
        results = await gather_cancelling(
            [_seed_audio_once(speech_client, body, audio_format, sample_rate) for _ in range(count)],
            return_exceptions=True,
        )
        clips = []
        errors = []
        for result in results:
            if isinstance(result, BaseException):
                errors.append(result)
            else:
                clips.append(result)
        if count > 1:
            log_msg("batch_finished_stats", success=len(clips), failed=len(errors))
            for error in errors:
                log_msg("batch_failed_reason", msg=str(error), count=1)
        if not clips:
            raise errors[0]
        # One list per output, index-aligned with the clips.
        return comfy_io.NodeOutput(*[list(column) for column in zip(*clips)])


async def _seed_audio_once(speech_client, body, audio_format, sample_rate):
    """One Seed Audio request: (audio, subtitles_json, srt, duration, url)."""
    operation = "Seed Audio"
    response = await speech_post(speech_client, SEED_AUDIO_PATH, body, operation=operation)
    result = response.json()
    if not isinstance(result, dict):
        raise speech_error(operation, response, message=response.text()[:300])
    check_code(operation, response, result.get("code"), result.get("message"))

    url = result.get("url") or ""
    if result.get("audio"):
        audio_bytes = b64decode_audio(result["audio"])
    elif url:
        audio_bytes = await download_bytes(url, operation)
    else:
        raise BytePlusException(get_text("speech_empty_audio", operation=operation))
    if audio_format == "pcm":
        audio = pcm16_to_audio(audio_bytes, int(sample_rate))
    else:
        audio = decode_audio_bytes(audio_bytes)

    subtitle = result.get("subtitle") if isinstance(result.get("subtitle"), dict) else {}
    # The docs name the sentence list "sentences"; accept "utterances" too.
    segments = subtitle_segments(subtitle.get("sentences") or subtitle.get("utterances"))
    duration = result.get("duration")
    if not isinstance(duration, (int, float)):
        duration = audio_duration(audio)
    log_msg(
        "seed_audio_done",
        duration=f"{float(duration):.2f}",
        billed=result.get("original_duration", "-"),
    )
    return audio, _segments_json(subtitle, segments), build_srt(segments), float(duration), url


# TTS

def build_tts_request(model, text, voice, custom_speaker_id="", context_text="", emotion="",
                      emotion_scale=4, speech_rate=0, loudness_rate=0, pitch=0, sample_rate="24000",
                      explicit_language="auto", silence_duration=0, filter_markdown=False,
                      enable_subtitle=False, detect_language=False, context_language="default",
                      read_emoji=False, read_latex=False, read_parentheses=False,
                      unsupported_char_ratio=SEED_TTS_DEFAULT_UNSUPPORTED_CHAR_RATIO, use_cache=False,
                      tone_fidelity=False):
    """
    (extra headers, body) for the unidirectional TTS HTTP API. Always asks for
    raw PCM: the node outputs decoded AUDIO, so the transfer format does not
    change the result (Save Audio nodes encode mp3/opus/flac).
    """
    text = (text or "").strip()
    if not text:
        raise BytePlusException(get_text("tts_text_empty"))
    speaker = (custom_speaker_id or "").strip()
    if not speaker:
        if model != SEED_TTS_2_UI_MODEL:
            raise BytePlusException(get_text("tts_voice_required", model=model))
        speaker = voice
    # TTS 2.0 doc: the uni-directional interface supports 24K, 16K and 8K only.
    if model == SEED_TTS_2_UI_MODEL and str(sample_rate) not in SEED_TTS_2_SAMPLE_RATES:
        raise BytePlusException(get_text("tts_sample_rate_unsupported"))
    if tone_fidelity and model != "seed-icl-2.0":
        raise BytePlusException(get_text("tts_tone_fidelity_icl2_only"))

    audio_params = {
        "format": "pcm",
        "sample_rate": int(sample_rate),
        "speech_rate": int(speech_rate),
        "loudness_rate": int(loudness_rate),
    }
    emotion = (emotion or "").strip()
    if emotion:
        audio_params["emotion"] = emotion
        audio_params["emotion_scale"] = int(emotion_scale)
    if enable_subtitle:
        # 2.0 voices return subtitles; 1.0 voices return word timestamps.
        timestamps_key = "enable_subtitle" if model.endswith("-2.0") else "enable_timestamp"
        audio_params[timestamps_key] = True

    additions = {}
    if pitch:
        additions["post_process"] = {"pitch": int(pitch)}
    context_text = (context_text or "").strip()
    if context_text:
        if model != SEED_TTS_2_UI_MODEL:
            raise BytePlusException(get_text("tts_context_text_2_only"))
        additions["context_texts"] = [context_text]
    if explicit_language and explicit_language != "auto":
        additions["explicit_language"] = explicit_language
    if silence_duration:
        additions["silence_duration"] = int(silence_duration)
    if filter_markdown or read_latex:
        # disable_markdown_filter=true turns markdown filtering ON (API naming);
        # LaTeX reading requires it.
        additions["disable_markdown_filter"] = True
    if read_latex:
        additions["enable_latex_tn"] = True
    if detect_language:
        additions["enable_language_detector"] = True
    if context_language and context_language != "default":
        additions["context_language"] = context_language
    if read_emoji:
        additions["disable_emoji_filter"] = True
    if read_parentheses:
        additions["max_length_to_filter_parenthesis"] = 0
    if abs(float(unsupported_char_ratio) - SEED_TTS_DEFAULT_UNSUPPORTED_CHAR_RATIO) > 1e-6:
        additions["unsupported_char_ratio_thresh"] = round(float(unsupported_char_ratio), 3)
    if use_cache:
        additions["cache_config"] = {"text_type": 1, "use_cache": True}
    if tone_fidelity:
        additions["tone_fidelity"] = True

    headers = {"X-Api-Resource-Id": model, "X-Api-App-Key": SEED_TTS_APP_KEY}
    body = {
        "user": {"uid": USER_ID},
        "req_params": {
            "text": text,
            "speaker": speaker,
            "audio_params": audio_params,
            "additions": json.dumps(additions, ensure_ascii=False),
        },
    }
    return headers, body


def parse_tts_stream(operation, response):
    """Concatenate the base64 PCM chunks of a TTS stream; collect subtitle sentences."""
    chunks = []
    sentences = []
    for item in iter_json_objects(response.text()):
        check_code(operation, response, item.get("code"), item.get("message"))
        data = item.get("data")
        if isinstance(data, str) and data:
            chunks.append(b64decode_audio(data))
        for key in ("sentence", "subtitle"):
            value = item.get(key)
            if isinstance(value, dict):
                sentences.append(value)
            elif isinstance(value, list):
                sentences.extend(v for v in value if isinstance(v, dict))
    return b"".join(chunks), sentences


class BytePlusSeedTTS(comfy_io.ComfyNode):
    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return comfy_io.Schema(
            node_id="BytePlusSeedTTS",
            display_name="BytePlus Seed Speech TTS",
            category=SPEECH_CATEGORY,
            description="Text to speech with Seed Speech TTS 2.0 voices, TTS 1.0 or cloned voices.",
            inputs=[
                BytePlusSpeechClientType.Input("speech_client", optional=True, tooltip=SPEECH_CLIENT_TOOLTIP),
                comfy_io.Combo.Input(
                    "model",
                    options=SEED_TTS_MODELS,
                    default=SEED_TTS_2_UI_MODEL,
                    tooltip="seed-tts-2.0 for the voice list; seed-icl-* for cloned voices (custom_speaker_id).",
                ),
                comfy_io.String.Input("text", multiline=True, default=""),
                comfy_io.Combo.Input(
                    "voice",
                    options=TTS_2_VOICE_IDS,
                    default=DEFAULT_TTS_VOICE,
                    tooltip="TTS 2.0 voice. Each voice supports specific languages; see the Seed Speech voice list.",
                ),
                comfy_io.String.Input(
                    "custom_speaker_id",
                    default="",
                    tooltip="Overrides voice: a cloned voice (connect Seed Voice Clone's speaker_id, model seed-icl-2.0) or a TTS 1.0 speaker ID.",
                ),
                comfy_io.String.Input(
                    "context_text",
                    default="",
                    tooltip="TTS 2.0 only: style instruction or preceding dialogue, e.g. 'Speak slowly, sounding heartbroken.' Not billed.",
                ),
                comfy_io.String.Input(
                    "emotion",
                    default="",
                    tooltip="Emotion for voices that support it, e.g. happy, sad, angry. Empty = neutral.",
                ),
                comfy_io.Int.Input("emotion_scale", default=4, min=1, max=5),
                comfy_io.Int.Input("speech_rate", default=0, min=-50, max=100, tooltip="100 = 2x speed, -50 = 0.5x."),
                comfy_io.Int.Input("loudness_rate", default=0, min=-50, max=100, tooltip="100 = 2x volume, -50 = 0.5x."),
                comfy_io.Int.Input("pitch", default=0, min=-12, max=12, tooltip="Pitch shift in semitones."),
                comfy_io.Combo.Input(
                    "sample_rate",
                    options=SEED_TTS_SAMPLE_RATES,
                    default="24000",
                    tooltip="Hz. seed-tts-2.0 supports 24000, 16000 and 8000.",
                ),
                comfy_io.Combo.Input(
                    "explicit_language",
                    options=SEED_TTS_LANGUAGES,
                    default="auto",
                    tooltip="Read only text in this language. auto handles mixed Chinese and English.",
                ),
                comfy_io.Int.Input(
                    "silence_duration",
                    default=0,
                    min=0,
                    max=30000,
                    step=100,
                    tooltip="Silence added after the last sentence, in ms.",
                ),
                comfy_io.Boolean.Input(
                    "filter_markdown",
                    default=False,
                    tooltip="Strip Markdown syntax so **bold** is read as 'bold'.",
                ),
                comfy_io.Boolean.Input(
                    "enable_subtitle",
                    default=False,
                    tooltip="Return timestamps (Chinese and English): subtitles for 2.0 voices, word timestamps for 1.0 voices.",
                ),
                comfy_io.Boolean.Input("detect_language", default=False, advanced=True,
                                       tooltip="Detect the text language automatically."),
                comfy_io.Combo.Input(
                    "context_language",
                    options=SEED_TTS_CONTEXT_LANGUAGES,
                    default="default",
                    advanced=True,
                    tooltip="Reference language for Western European text: default English, id Indonesian, es Mexican Spanish, pt Brazilian Portuguese.",
                ),
                comfy_io.Boolean.Input("read_emoji", default=False, advanced=True,
                                       tooltip="Keep emoji in the text instead of filtering them out."),
                comfy_io.Boolean.Input("read_latex", default=False, advanced=True,
                                       tooltip="Read LaTeX formulas aloud (also enables filter_markdown)."),
                comfy_io.Boolean.Input("read_parentheses", default=False, advanced=True,
                                       tooltip="Read text inside parentheses (filtered out by default)."),
                comfy_io.Float.Input(
                    "unsupported_char_ratio",
                    default=SEED_TTS_DEFAULT_UNSUPPORTED_CHAR_RATIO,
                    min=0.0,
                    max=1.0,
                    step=0.05,
                    advanced=True,
                    tooltip="Fail when more than this share of the text is in an unsupported language.",
                ),
                comfy_io.Boolean.Input("use_cache", default=False, advanced=True,
                                       tooltip="Reuse audio synthesized for identical text in the last hour (no timestamps from cache)."),
                comfy_io.Boolean.Input("tone_fidelity", default=False, advanced=True,
                                       tooltip="seed-icl-2.0 only: stay as close as possible to the training audio's voice, emotion and accent (same language only)."),
                comfy_io.Int.Input(
                    "seed",
                    default=0,
                    min=0,
                    max=SPEECH_MAX_SEED,
                    control_after_generate=True,
                    tooltip="Not sent to the API; change it to synthesize again.",
                ),
            ],
            outputs=[
                comfy_io.Audio.Output(display_name="audio"),
                comfy_io.String.Output(display_name="subtitles_json"),
                comfy_io.String.Output(display_name="srt"),
            ],
        )

    @classmethod
    @with_default_client("speech_client", build_default_speech_client)
    async def execute(cls, speech_client, model, text, voice, custom_speaker_id="", context_text="",
                      emotion="", emotion_scale=4, speech_rate=0, loudness_rate=0, pitch=0,
                      sample_rate="24000", explicit_language="auto", silence_duration=0,
                      filter_markdown=False, enable_subtitle=False, detect_language=False,
                      context_language="default", read_emoji=False, read_latex=False,
                      read_parentheses=False, unsupported_char_ratio=SEED_TTS_DEFAULT_UNSUPPORTED_CHAR_RATIO,
                      use_cache=False, tone_fidelity=False, seed=0) -> comfy_io.NodeOutput:
        require_speech_client(speech_client)
        headers, body = build_tts_request(
            model, text, voice, custom_speaker_id, context_text, emotion, emotion_scale,
            speech_rate, loudness_rate, pitch, sample_rate, explicit_language, silence_duration,
            filter_markdown, enable_subtitle, detect_language, context_language, read_emoji,
            read_latex, read_parentheses, unsupported_char_ratio, use_cache, tone_fidelity,
        )
        operation = "TTS"
        response = await speech_post(speech_client, SEED_TTS_PATH, body, operation=operation, headers=headers)
        pcm, sentences = parse_tts_stream(operation, response)
        if not pcm:
            raise BytePlusException(get_text("speech_empty_audio", operation=operation))
        segments = subtitle_segments(sentences)
        return comfy_io.NodeOutput(
            pcm16_to_audio(pcm, int(sample_rate)),
            _segments_json(sentences, segments),
            build_srt(segments),
        )


# ASR

def _word_list(text):
    words = []
    for line in (text or "").replace(",", "\n").splitlines():
        word = line.strip()
        if word and word not in words:
            words.append(word)
    return words


def _context_entry(line):
    """'user: ...' / 'bot: ...' lines carry the optional speaker of a dialogue turn."""
    speaker, sep, text = line.partition(":")
    if sep and speaker.strip().lower() in ("user", "bot") and text.strip():
        return {"speaker": speaker.strip().lower(), "text": text.strip()}
    return {"text": line.strip()}


def _asr_context(hotwords="", context_text="", context_image_url=""):
    """corpus.context JSON string: hotwords plus dialogue/scene context (text lines, one image)."""
    context = {}
    words = _word_list(hotwords)
    if words:
        context["hotwords"] = [{"word": w} for w in words]
    data = [_context_entry(line) for line in (context_text or "").splitlines() if line.strip()]
    if context_image_url:
        data.append({"image_url": context_image_url})
    if data:
        context["context_type"] = "dialog_ctx"
        context["context_data"] = data
    return json.dumps(context, ensure_ascii=False) if context else None


def _sensitive_words_filter(system_filter=False, remove_words="", mask_words="", wrap_marks=False):
    config = {}
    if system_filter:
        config["system_reserved_filter"] = True
    if _word_list(remove_words):
        config["filter_with_empty"] = _word_list(remove_words)
    if _word_list(mask_words):
        config["filter_with_signed"] = _word_list(mask_words)
    if config and wrap_marks:
        config["wrap_with_marks"] = True
    return json.dumps(config, ensure_ascii=False) if config else None


def _url_audio_format(model, mode, audio_url, audio_format):
    """audio.format for a URL: explicit choice, else from the extension."""
    if audio_format and audio_format != "auto":
        if mode == "fast" and audio_format not in SEED_ASR_FAST_FORMATS:
            raise BytePlusException(get_text("asr_format_fast_unsupported", format=audio_format))
        return audio_format
    extension = audio_url.split("?", 1)[0].split("#", 1)[0].rsplit("/", 1)[-1].rpartition(".")[2].lower()
    inferred = SEED_ASR_EXTENSION_FORMATS.get(extension)
    if mode == "fast":
        return inferred if inferred in SEED_ASR_FAST_FORMATS else None
    if not inferred:
        raise BytePlusException(get_text("asr_format_unknown", model=model))
    return inferred


def build_asr_request(model, audio=None, audio_url="", language="auto", enable_punc=True,
                      enable_itn=True, enable_ddc=False, enable_speaker_info=False, hotwords="",
                      context_text="", context_image_url="", enable_auto_lang=False,
                      enable_lid=False, enable_channel_split=False, vad_segment=False,
                      end_window_size=0, output_zh_variant="none",
                      filter_system_sensitive_words=False, remove_words="", mask_words="",
                      wrap_sensitive_words=False, audio_format="auto"):
    """(mode, extra headers, body) for Seed Speech ASR. URL-only options take URLs here;
    the node uploads connected media first."""
    mode, resource_id = SEED_ASR_MODELS[model]
    audio_url = _validated_url(audio_url, "audio_url")
    context_image_url = _validated_url(context_image_url, "context_image_url")
    if audio is None and not audio_url:
        raise BytePlusException(get_text("asr_no_input"))
    if audio is not None and audio_url:
        raise BytePlusException(get_text("asr_both_inputs"))
    if enable_lid and mode == "fast":
        raise BytePlusException(get_text("asr_lid_standard_only"))
    if mode == "fast" and language in SEED_ASR_STANDARD_ONLY_LANGUAGES:
        raise BytePlusException(get_text("asr_language_standard_only", language=language))
    if context_image_url and resource_id == "volc.bigasr.auc":
        raise BytePlusException(get_text("asr_context_image_2_only"))
    end_window_size = int(end_window_size or 0)
    if end_window_size and not 300 <= end_window_size <= 5000:
        raise BytePlusException(get_text("asr_end_window_out_of_range"))
    context = _asr_context(hotwords, context_text, context_image_url)
    if context and (language not in SEED_ASR_CONTEXT_LANGUAGES or enable_auto_lang):
        raise BytePlusException(get_text("asr_context_language"))

    if audio is not None:
        if mode != "fast":
            raise BytePlusException(get_text("asr_standard_needs_url", model=model))
        channels = asr_channel_count(audio, enable_channel_split)
        wav = audio_to_wav_bytes(audio, sample_rate=SEED_ASR_SAMPLE_RATE, mono=channels == 1)
        if len(wav) > SEED_ASR_FAST_MAX_BYTES:
            raise BytePlusException(get_text(
                "asr_audio_too_large", size_mb=_size_mb(len(wav)), max_mb=_size_mb(SEED_ASR_FAST_MAX_BYTES)
            ))
        audio_config = {
            "data": base64.b64encode(wav).decode("utf-8"),
            "format": "wav",
            "codec": "raw",
            "rate": SEED_ASR_SAMPLE_RATE,
            "bits": 16,
            "channel": channels,
        }
    else:
        audio_config = {"url": audio_url}
        url_format = _url_audio_format(model, mode, audio_url, audio_format)
        if url_format:
            audio_config["format"] = url_format
            if url_format == "ogg":
                audio_config["codec"] = "opus"
        if enable_channel_split:
            audio_config["channel"] = 2
    if language and language != "auto":
        audio_config["language"] = language

    request = {
        "model_name": "bigmodel",
        "enable_itn": bool(enable_itn),
        "enable_punc": bool(enable_punc),
        "enable_ddc": bool(enable_ddc),
        "enable_speaker_info": bool(enable_speaker_info),
        "show_utterances": True,
    }
    if enable_auto_lang:
        request["enable_auto_lang"] = True
    if enable_lid:
        request["enable_lid"] = True
    if enable_channel_split:
        request["enable_channel_split"] = True
    if vad_segment:
        request["vad_segment"] = True
    if end_window_size:
        request["end_window_size"] = end_window_size
    if output_zh_variant and output_zh_variant != "none":
        request["output_zh_variant"] = output_zh_variant
    sensitive = _sensitive_words_filter(
        filter_system_sensitive_words, remove_words, mask_words, wrap_sensitive_words
    )
    if sensitive:
        request["sensitive_words_filter"] = sensitive
    if context:
        request["corpus"] = {"context": context}

    headers = {"X-Api-Resource-Id": resource_id, "X-Api-Sequence": "-1"}
    body = {"user": {"uid": USER_ID}, "audio": audio_config, "request": request}
    return mode, headers, body


def asr_channel_count(audio, enable_channel_split):
    """1, or 2 when channel split is on (then the audio must be stereo)."""
    if not enable_channel_split:
        return 1
    channels = int(audio_waveform(audio)[0].shape[0])
    if channels != 2:
        raise BytePlusException(get_text("asr_channel_split_needs_stereo"))
    return 2


def _first_dict(value):
    if isinstance(value, list):
        value = value[0] if value else {}
    return value if isinstance(value, dict) else {}


def _asr_outputs(operation, response, with_labels):
    result_body = response.json()
    if not isinstance(result_body, dict):
        raise BytePlusException(get_text(
            "speech_unexpected_response", operation=operation, body=response.text()[:200]
        ))
    if "result" not in result_body:
        raise BytePlusException(get_text(
            "speech_unexpected_response", operation=operation, body=response.text()[:200] or "(empty)"
        ))
    result = _first_dict(result_body.get("result"))
    utterances = result.get("utterances") or []
    segments = subtitle_segments(utterances)
    duration_ms = (
        _first_dict(result_body.get("audio_info")).get("duration")
        or (result.get("additions") or {}).get("duration")
        or 0
    )
    try:
        duration = float(duration_ms) / 1000.0
    except (TypeError, ValueError):
        duration = 0.0
    return comfy_io.NodeOutput(
        str(result.get("text") or ""),
        json.dumps(utterances, ensure_ascii=False),
        build_srt(segments, with_speaker=with_labels),
        duration,
    )


def _silent_outputs():
    log_msg("asr_silent_audio")
    return comfy_io.NodeOutput("", "[]", "", 0.0)


class BytePlusSeedASR(comfy_io.ComfyNode):
    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return comfy_io.Schema(
            node_id="BytePlusSeedASR",
            display_name="BytePlus Seed Speech ASR",
            category=SPEECH_CATEGORY,
            description="Speech to text with Seed Speech ASR: transcript, utterance timings and SRT subtitles.",
            inputs=[
                BytePlusSpeechClientType.Input("speech_client", optional=True, tooltip=SPEECH_CLIENT_TOOLTIP),
                comfy_io.Combo.Input(
                    "model",
                    options=SEED_ASR_UI_OPTIONS,
                    default=SEED_ASR_UI_OPTIONS[0],
                    tooltip=(
                        "seed-asr-fast: one request, audio up to 2 h / 100 MB. seed-asr-2.0 / 1.0: submit and "
                        "poll, up to 5 h, more languages; connected audio is uploaded to Comfy.org storage."
                    ),
                ),
                comfy_io.String.Input(
                    "audio_url",
                    default="",
                    tooltip="Public http(s) audio URL. Use this or the audio input (e.g. Load Audio).",
                ),
                comfy_io.Combo.Input(
                    "language",
                    options=SEED_ASR_LANGUAGES,
                    default="auto",
                    tooltip=(
                        "auto recognizes Chinese, English and Chinese dialects; pick a language for others. "
                        "The last 16 languages need seed-asr-2.0 or 1.0."
                    ),
                ),
                comfy_io.Boolean.Input("enable_punc", default=True, tooltip="Add punctuation."),
                comfy_io.Boolean.Input("enable_itn", default=True, tooltip="Write numbers in digits (inverse text normalization)."),
                comfy_io.Boolean.Input("enable_ddc", default=False, tooltip="Remove filler words and repetitions."),
                comfy_io.Boolean.Input(
                    "enable_speaker_info",
                    default=False,
                    tooltip="Label speakers (best with 10 or fewer).",
                ),
                comfy_io.String.Input(
                    "hotwords",
                    multiline=True,
                    default="",
                    tooltip=(
                        "Names or terms to favour, one per line or comma-separated (up to 5000). "
                        "Chinese-English model only: language auto or zh-CN."
                    ),
                ),
                comfy_io.String.Input(
                    "context_text",
                    multiline=True,
                    default="",
                    advanced=True,
                    tooltip=(
                        "Dialogue history or scene description, one entry per line, newest first (up to 20 "
                        "entries / 800 tokens). Prefix a line with 'user:' or 'bot:' to mark the speaker. "
                        "Chinese-English model only."
                    ),
                ),
                comfy_io.String.Input(
                    "context_image_url",
                    default="",
                    advanced=True,
                    tooltip="ASR 2.0 visual context: public JPEG/PNG URL (up to 500 KB). Or connect context_image.",
                ),
                comfy_io.Boolean.Input("enable_auto_lang", default=False, advanced=True,
                                       tooltip="Detect the spoken language automatically (overrides language)."),
                comfy_io.Boolean.Input("enable_lid", default=False, advanced=True,
                                       tooltip="seed-asr-2.0 / 1.0: add a detected-language label (lid_lang) to each utterance."),
                comfy_io.Boolean.Input("enable_channel_split", default=False, advanced=True,
                                       tooltip="Recognize the left and right channels of stereo audio separately (channel_id 1 / 2)."),
                comfy_io.Boolean.Input("vad_segment", default=False, advanced=True,
                                       tooltip="Split sentences on silence (VAD) instead of meaning."),
                comfy_io.Int.Input("end_window_size", default=0, min=0, max=5000, step=100, advanced=True,
                                   tooltip="Silence in ms that ends a sentence (300-5000; 0 = semantic segmentation)."),
                comfy_io.Combo.Input("output_zh_variant", options=SEED_ASR_ZH_VARIANTS, default="none", advanced=True,
                                     tooltip="Convert Chinese output to Traditional Chinese: traditional, tw (Taiwan) or hk (Hong Kong)."),
                comfy_io.Boolean.Input("filter_system_sensitive_words", default=False, advanced=True,
                                       tooltip="Mask words from the built-in sensitive word list with *."),
                comfy_io.String.Input("remove_words", default="", advanced=True,
                                      tooltip="Words to delete from the transcript, comma-separated."),
                comfy_io.String.Input("mask_words", default="", advanced=True,
                                      tooltip="Words to replace with * in the transcript, comma-separated."),
                comfy_io.Boolean.Input("wrap_sensitive_words", default=False, advanced=True,
                                       tooltip="Wrap filtered words in backticks instead of hiding them silently."),
                comfy_io.Combo.Input(
                    "audio_format",
                    options=SEED_ASR_AUDIO_FORMATS,
                    default="auto",
                    advanced=True,
                    tooltip="Container of audio_url. auto reads the file extension; set it for URLs without one.",
                ),
                comfy_io.Audio.Input("audio", optional=True, tooltip="Audio to transcribe, e.g. from Load Audio."),
                comfy_io.Image.Input(
                    "context_image",
                    optional=True,
                    tooltip="ASR 2.0 visual context image (uploaded to Comfy.org storage, resized to fit 500 KB).",
                ),
            ],
            outputs=[
                comfy_io.String.Output(display_name="text"),
                comfy_io.String.Output(display_name="utterances_json"),
                comfy_io.String.Output(display_name="srt"),
                comfy_io.Float.Output(display_name="duration"),
            ],
            hidden=[comfy_io.Hidden.auth_token_comfy_org, comfy_io.Hidden.api_key_comfy_org],
        )

    @classmethod
    @with_default_client("speech_client", build_default_speech_client)
    async def execute(cls, speech_client, model, audio=None, context_image=None, **options) -> comfy_io.NodeOutput:
        require_speech_client(speech_client)
        mode = SEED_ASR_MODELS[model][0]
        uploaded_audio = False
        if audio is not None and mode != "fast":
            if (options.get("audio_url") or "").strip():
                raise BytePlusException(get_text("asr_both_inputs"))
            channels = asr_channel_count(audio, options.get("enable_channel_split"))
            wav = audio_to_wav_bytes(audio, sample_rate=SEED_ASR_SAMPLE_RATE, mono=channels == 1)
            options["audio_url"] = await upload_to_comfy_storage(cls, "audio", wav, "asr.wav", "audio/wav")
            options["audio_format"] = "wav"
            audio = None
            uploaded_audio = True
        if context_image is not None:
            if (options.get("context_image_url") or "").strip():
                raise BytePlusException(get_text("asr_context_image_conflict"))
            if SEED_ASR_MODELS[model][1] == "volc.bigasr.auc":
                raise BytePlusException(get_text("asr_context_image_2_only"))
            options["context_image_url"] = await upload_to_comfy_storage(
                cls, "image", context_image_jpeg(context_image), "context.jpg", "image/jpeg"
            )
        mode, headers, body = build_asr_request(model, audio, **options)
        if uploaded_audio:
            body["audio"].update({"codec": "raw", "rate": SEED_ASR_SAMPLE_RATE, "bits": 16})
        labels = bool(options.get("enable_speaker_info") or options.get("enable_channel_split"))
        operation = "ASR"
        if mode == "fast":
            response = await speech_post(speech_client, SEED_ASR_FAST_PATH, body, operation=operation, headers=headers)
            if response.status_code == SPEECH_ASR_SILENT_AUDIO_CODE:
                return _silent_outputs()
            check_code(operation, response, response.status_code)
            return _asr_outputs(operation, response, labels)

        task_id = str(uuid.uuid4())
        headers = {**headers, "X-Api-Request-Id": task_id}
        response = await speech_post(speech_client, SEED_ASR_SUBMIT_PATH, body, operation=operation, headers=headers)
        check_code(operation, response, response.status_code)
        log_msg("asr_task_submitted", task_id=task_id)
        waited = 0
        while True:
            if waited >= SEED_ASR_MAX_WAIT_SECONDS:
                raise BytePlusException(get_text(
                    "asr_wait_timeout", task_id=task_id, seconds=SEED_ASR_MAX_WAIT_SECONDS
                ))
            await sleep_interruptible(SPEECH_ASR_POLL_SECONDS)
            waited += max(SPEECH_ASR_POLL_SECONDS, 1)
            response = await speech_poll(
                speech_client, SEED_ASR_QUERY_PATH, {}, operation=operation, headers=headers,
                poll_seconds=SPEECH_ASR_POLL_SECONDS,
            )
            status = response.status_code
            if status in SPEECH_ASR_PENDING_CODES:
                continue
            if status == SPEECH_ASR_SILENT_AUDIO_CODE:
                return _silent_outputs()
            check_code(operation, response, status)
            if status is None and not isinstance((response.json() or {}).get("result"), (dict, list)):
                raise speech_error(operation, response)
            return _asr_outputs(operation, response, labels)


# Voice cloning (Voice Replication 2.0)

def _voice_ids(speaker_id):
    """Request IDs: voice slot (S_...) as speaker_id, or a postpaid custom voice ID."""
    speaker = (speaker_id or "").strip()
    if not speaker:
        raise BytePlusException(get_text("voice_clone_speaker_empty"))
    if speaker.upper().startswith("S_"):
        return speaker, {"speaker_id": speaker}
    if re.match(SEED_CUSTOM_VOICE_ID_REJECT, speaker):
        raise BytePlusException(get_text("voice_clone_custom_id_invalid", value=speaker))
    return speaker, {"speaker_id": "custom_speaker_id", "custom_speaker_id": speaker}


def build_voice_clone_request(speaker_id, audio, language="en", reference_text="", demo_text="",
                              disable_volume_normalization=False):
    """(voice ID, clone body, status-query body) for voice_clone / get_voice."""
    speaker, ids = _voice_ids(speaker_id)
    wav = audio_to_wav_bytes(audio)
    if len(wav) > SEED_VOICE_CLONE_MAX_BYTES:
        wav = audio_to_wav_bytes(audio, sample_rate=24000, mono=True)
    if len(wav) > SEED_VOICE_CLONE_MAX_BYTES:
        raise BytePlusException(get_text(
            "voice_clone_audio_too_large", size_mb=_size_mb(len(wav)), max_mb=_size_mb(SEED_VOICE_CLONE_MAX_BYTES)
        ))
    body = {
        **ids,
        "audio": {"data": base64.b64encode(wav).decode("utf-8"), "format": "wav"},
        "language": SEED_VOICE_CLONE_LANGUAGES[language],
    }
    reference_text = (reference_text or "").strip()
    if reference_text:
        body["text"] = reference_text
    extra = {}
    demo_text = (demo_text or "").strip()
    if demo_text:
        if not 4 <= len(demo_text) <= 80:
            raise BytePlusException(get_text("voice_clone_demo_text_length"))
        extra["demo_text"] = demo_text
    if disable_volume_normalization:
        extra["disable_volume_normalization"] = True
    if extra:
        body["extra_params"] = extra
    return speaker, body, ids


def _voice_demo_url(result):
    if result.get("demo_audio"):
        return result["demo_audio"]
    for item in result.get("speaker_status") or []:
        if isinstance(item, dict) and item.get("demo_audio"):
            return item["demo_audio"]
    return ""


def _silence(seconds=0.5, sample_rate=24000):
    return pcm16_to_audio(b"\x00\x00" * int(seconds * sample_rate), sample_rate)


class BytePlusSeedVoiceClone(comfy_io.ComfyNode):
    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return comfy_io.Schema(
            node_id="BytePlusSeedVoiceClone",
            display_name="BytePlus Seed Voice Clone",
            category=SPEECH_CATEGORY,
            description=(
                "Clone a voice from a reference clip (Voice Replication 2.0) and output its speaker ID for "
                "Seed Speech TTS (model seed-icl-2.0) or Seed Audio references."
            ),
            inputs=[
                BytePlusSpeechClientType.Input("speech_client", optional=True, tooltip=SPEECH_CLIENT_TOOLTIP),
                comfy_io.String.Input(
                    "speaker_id",
                    default="",
                    tooltip=(
                        "Voice slot ID (S_...) bought in the Seed Speech console, or your own postpaid custom "
                        "voice ID. Each slot can be trained 15 times; every run with changed inputs trains again."
                    ),
                ),
                comfy_io.Combo.Input(
                    "language",
                    options=list(SEED_VOICE_CLONE_LANGUAGES.keys()),
                    default="en",
                    tooltip="Language spoken in the reference clip. Cross-language cloning is not supported.",
                ),
                comfy_io.String.Input(
                    "reference_text",
                    multiline=True,
                    default="",
                    tooltip="Optional: the text read in the clip. Training fails if the audio differs too much.",
                ),
                comfy_io.String.Input(
                    "demo_text",
                    default="",
                    tooltip="Optional: text for the demo clip (4-80 characters, same language).",
                ),
                comfy_io.Boolean.Input(
                    "disable_volume_normalization",
                    default=False,
                    advanced=True,
                    tooltip="Keep the reference clip's loudness instead of normalizing it (closer similarity).",
                ),
                comfy_io.Audio.Input(
                    "audio",
                    tooltip="Reference voice clip, e.g. from Load Audio: 10-15 s of clear speech, up to 10 MB.",
                ),
            ],
            outputs=[
                comfy_io.String.Output(display_name="speaker_id"),
                comfy_io.Audio.Output(display_name="demo_audio"),
                comfy_io.String.Output(display_name="status_json"),
            ],
        )

    @classmethod
    @with_default_client("speech_client", build_default_speech_client)
    async def execute(cls, speech_client, speaker_id, audio, language="en", reference_text="", demo_text="",
                      disable_volume_normalization=False) -> comfy_io.NodeOutput:
        require_speech_client(speech_client)
        speaker, body, ids = build_voice_clone_request(
            speaker_id, audio, language, reference_text, demo_text, disable_volume_normalization
        )
        operation = "Voice clone"
        response = await speech_post(speech_client, SEED_VOICE_CLONE_PATH, body, operation=operation)
        result = response.json() or {}
        check_code(operation, response, result.get("code"), result.get("message"))
        log_msg("voice_clone_started", speaker=speaker)
        waited = 0
        while result.get("status") not in SEED_VOICE_READY_STATUSES:
            status = result.get("status")
            if status == SEED_VOICE_FAILED_STATUS:
                raise BytePlusException(get_text(
                    "voice_clone_failed", speaker=speaker, message=result.get("message") or "training failed"
                ))
            if status == SEED_VOICE_NOT_FOUND_STATUS and waited:
                raise BytePlusException(get_text("voice_clone_not_found", speaker=speaker))
            if waited >= SEED_VOICE_TRAINING_TIMEOUT_SECONDS:
                raise BytePlusException(get_text(
                    "voice_clone_timeout", speaker=speaker, seconds=SEED_VOICE_TRAINING_TIMEOUT_SECONDS
                ))
            await sleep_interruptible(SEED_VOICE_POLL_SECONDS)
            waited += max(SEED_VOICE_POLL_SECONDS, 1)
            response = await speech_poll(
                speech_client, SEED_VOICE_STATUS_PATH, ids, operation=operation, poll_seconds=SEED_VOICE_POLL_SECONDS
            )
            result = response.json() or {}
            check_code(operation, response, result.get("code"), result.get("message"))
        log_msg("voice_clone_ready", speaker=speaker, status=result.get("status"))

        demo_url = _voice_demo_url(result)
        if demo_url:
            demo = decode_audio_bytes(await download_bytes(demo_url, operation))
        else:
            log_msg("voice_clone_no_demo", speaker=speaker)
            demo = _silence()
        status_json = {k: v for k, v in result.items() if k != "demo_audio"}
        return comfy_io.NodeOutput(speaker, demo, json.dumps(status_json, ensure_ascii=False))
