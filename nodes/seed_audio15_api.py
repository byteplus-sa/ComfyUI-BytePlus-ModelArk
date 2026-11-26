"""Seed Audio 1.5's synchronous ModelArk contract, separate from Seed Speech."""

import json
import re

import comfy.model_management

from .constants import REGION_BASE_URLS
from .http_requests import await_request, send_http_request
from .models_config import SEED_AUDIO_15_MODEL_MAP
from .nodes_shared import BytePlusException, format_api_error, get_text

# Supplied API spec, retrieved 2026-11-26:
# https://bytedance.larkoffice.com/wiki/IaAtwbHcuiGEoekYIa8cunhRnAg
PATH = "/contents/generations"
# The supplied contract uses MB, not MiB.
MAX_REQUEST_BYTES = 64_000_000
SUPPORTED_REGION = "ap-southeast-1"
MODES = (
    "text to audio",
    "audio reference",
    "image reference",
    "video to audio",
    "video translation",
    "stem separation",
)
SAMPLE_RATES = ("8000", "16000", "24000", "32000", "44100", "48000")
# Raw PCM has no documented channel layout. Use self-describing containers for AUDIO.
OUTPUT_FORMATS = ("wav", "mp3", "ogg_opus")
TARGET_LANGUAGES = (
    "zh",
    "en",
    "ja",
    "ko",
    "de",
    "fr",
    "pt-BR",
    "th",
    "id",
    "vi",
    "ms",
    "fil",
    "it",
    "ru",
    "nl",
    "pl",
    "tr",
    "sv",
    "es-ES",
    "es-MX",
    "ar",
    "pt-PT",
    "fi",
    "da",
    "no",
    "cs",
    "hu",
    "el",
    "ro",
    "hi",
)
ENHANCEMENT_LEVELS = ("off", "low", "medium", "high")


def invalid(detail):
    raise BytePlusException(get_text("err_seed_audio15_input", detail=detail))


def require_supported_region(client):
    if client.region != SUPPORTED_REGION:
        raise BytePlusException(get_text("err_seed_audio15_region"))


def optional_value(value, default):
    """V3 passes None for omitted optional inputs; other values still need validation."""
    return default if value is None else value


def redact(value, api_key=""):
    """Keep result URLs for recovery, but never expose an echoed credential."""
    if isinstance(value, dict):
        return {
            key: "[redacted]"
            if key.lower() in ("authorization", "api_key", "apikey", "token")
            else redact(item, api_key)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item, api_key) for item in value]
    if isinstance(value, str):
        return value.replace(api_key, "[redacted]") if api_key else value
    return value


def safe_error(value, api_key=""):
    return re.sub(r"(?:https?://|data:|tos://)\S+", "[media reference]", str(redact(str(value), api_key)))[:1000]


def request_size(body):
    # Match aiohttp's default JSON serializer, including escaped non-ASCII text.
    if len(json.dumps(body).encode("utf-8")) > MAX_REQUEST_BYTES:
        invalid("Request exceeds 64 MB. Use public URLs instead of connected audio or images.")


def build_request(
    mode,
    content,
    model="dola-seed-audio-1-5",
    output_format="wav",
    sample_rate="44100",
    speech_rate=0,
    loudness_rate=0,
    pitch_rate=0,
    include_video=False,
    include_subtitles=False,
    include_source_subtitles=False,
    target_language=None,
    source_language="auto",
    custom_subtitle_url="",
    glossaries="",
    dialogue_enhancement="off",
):
    model = optional_value(model, "dola-seed-audio-1-5")
    output_format = optional_value(output_format, "wav")
    speech_rate = optional_value(speech_rate, 0)
    loudness_rate = optional_value(loudness_rate, 0)
    pitch_rate = optional_value(pitch_rate, 0)
    include_video = optional_value(include_video, False)
    include_subtitles = optional_value(include_subtitles, False)
    include_source_subtitles = optional_value(include_source_subtitles, False)
    source_language = optional_value(source_language, "auto")
    dialogue_enhancement = optional_value(dialogue_enhancement, "off")
    glossaries = optional_value(glossaries, "")
    custom_subtitle_url = optional_value(custom_subtitle_url, "")
    if mode not in MODES or not isinstance(model, str) or model not in SEED_AUDIO_15_MODEL_MAP:
        invalid("Select a supported Seed Audio 1.5 mode and model.")
    if output_format not in OUTPUT_FORMATS or str(sample_rate) not in SAMPLE_RATES:
        invalid("Select WAV, MP3 or Ogg Opus and a supported sample rate.")
    if output_format == "ogg_opus" and str(sample_rate) != "48000":
        invalid("Ogg Opus requires a 48000 Hz sample rate.")
    for name, value, low, high in (
        ("speech_rate", speech_rate, -50, 100),
        ("loudness_rate", loudness_rate, -50, 100),
        ("pitch_rate", pitch_rate, -12, 12),
    ):
        if type(value) is not int or not low <= value <= high:
            invalid(f"{name} must be an integer from {low} to {high}.")
    for flag in (include_video, include_subtitles, include_source_subtitles):
        if type(flag) is not bool:
            invalid("Output include settings must be booleans.")
    if include_video and mode not in ("video to audio", "video translation"):
        invalid("Video output requires a video input mode.")
    if include_source_subtitles and mode != "video translation":
        invalid("Source subtitles require video translation.")
    body = {
        "model": SEED_AUDIO_15_MODEL_MAP[model],
        "content": content,
        "output_format": output_format,
        "audio_config": {"sample_rate": int(sample_rate)},
    }
    if mode not in ("video translation", "stem separation"):
        body["audio_config"].update(speech_rate=speech_rate, loudness_rate=loudness_rate, pitch_rate=pitch_rate)
    elif any((speech_rate, loudness_rate, pitch_rate)):
        invalid("Speed, loudness and pitch controls apply only to audio generation.")
    if mode == "video translation":
        if target_language not in TARGET_LANGUAGES or source_language not in ("auto", "zh", "en"):
            invalid("Select a supported target language and Chinese, English or automatic source language.")
        if dialogue_enhancement not in ENHANCEMENT_LEVELS:
            invalid("Select a supported dialogue enhancement level.")
        config = {"target_language": target_language}
        if source_language != "auto":
            config["source_language"] = source_language
        if dialogue_enhancement != "off":
            config["dialogue_enhancement"] = dialogue_enhancement
        if not isinstance(glossaries, str):
            invalid("Glossaries must be a JSON array of source/target pairs.")
        if glossaries.strip():
            if custom_subtitle_url:
                invalid("Custom subtitles and glossaries are mutually exclusive.")
            try:
                entries = json.loads(glossaries)
            except ValueError:
                invalid("Glossaries must be a JSON array of source/target pairs.")
            if not isinstance(entries, list) or not all(
                isinstance(entry, dict)
                and set(entry) == {"source", "target"}
                and all(isinstance(value, str) and value.strip() for value in entry.values())
                for entry in entries
            ):
                invalid("Glossaries must be a JSON array of nonempty source/target pairs.")
            config["glossaries"] = entries
        if custom_subtitle_url:
            config["custom_subtitle"] = {"format": "srt", "url": custom_subtitle_url}
        body["dubbing_config"] = config
    includes = []
    if include_video:
        includes.append("video_url")
    if include_subtitles:
        includes.extend(("subtitles.srt", "subtitles.jsonl"))
    if include_source_subtitles:
        includes.extend(("subtitles_source.srt", "subtitles_source.jsonl"))
    if includes:
        body["include"] = includes
    request_size(body)
    return body


async def _send(url, headers, body):
    # Long synchronous processing, with no total deadline or automatic retries.
    comfy.model_management.throw_exception_if_processing_interrupted()
    return await send_http_request("POST", url, headers, body, None)


async def generate(client, body):
    require_supported_region(client)
    request_size(body)
    key = client.api_key
    comfy.model_management.throw_exception_if_processing_interrupted()
    status, headers, data = await await_request(
        _send(
            REGION_BASE_URLS[client.region].rstrip("/") + PATH,
            {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            body,
        ),
        get_text(
            "err_seed_audio15_request",
            detail="Request timed out. Accepted work may still be billed; check before retrying.",
        ),
        lambda error: get_text("err_seed_audio15_request", detail=safe_error(error, key)),
    )
    try:
        response = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        response = None
    if not 200 <= status < 300 or isinstance(response, dict) and response.get("error"):
        error = response.get("error", {}) if isinstance(response, dict) else {}
        detail = safe_error(format_api_error(redact(error, key)), key) if error else f"HTTP {status}"
        request_id = next(
            (value for name, value in headers.items() if name.lower() in ("x-request-id", "x-tt-logid")), ""
        )
        raise BytePlusException(
            get_text("err_seed_audio15_request", detail=f"{detail} Request ID: {safe_error(request_id, key)}")
        )
    if (
        not isinstance(response, dict)
        or not isinstance(response.get("content"), dict)
        or not isinstance(response.get("id"), str)
        or not response["id"]
    ):
        raise BytePlusException(get_text("err_seed_audio15_response"))
    tracks = response["content"].get("audios")
    if not isinstance(tracks, list) or not tracks:
        raise BytePlusException(get_text("err_seed_audio15_response"))
    return redact(response, key)
