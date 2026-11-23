import asyncio
import base64
import binascii
import json
import math
import re
from urllib.parse import quote

from comfy_api.latest import io as comfy_io

from .audio_utils import build_srt, pcm16_to_audio, subtitle_segments
from .constants import SEED_TTS_SAMPLE_RATES
from .models_config import SEED_TTS_3_MODEL
from .nodes_shared import BytePlusException, get_text, with_client
from .nodes_speech import SPEECH_CATEGORY, SPEECH_MAX_SEED, get_speech_client
from .seed_tts3_config import (
    DEFAULT_TTS_3_VOICE,
    TTS_3_LANGUAGES,
    TTS_3_SUBTITLE_LANGUAGES,
    TTS_3_VOICE_IDS,
)
from .speech_api import speech_post


def _invalid(field, hint, **kwargs):
    return BytePlusException(get_text("tts3_invalid_input", field=field, hint=get_text(hint, **kwargs)))


def _text(value, field):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _invalid(field, "tts3_input_string")
    return value.strip()


def _number(value, field, minimum, maximum, *, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= maximum:
        raise _invalid(field, "tts3_input_range", minimum=minimum, maximum=maximum)
    if integer and int(value) != value:
        raise _invalid(field, "tts3_input_integer")
    return int(value) if integer else float(value)


def build_tts3_request(
    text,
    voice=DEFAULT_TTS_3_VOICE,
    custom_speaker_id="",
    language_code="auto",
    instructions="",
    speed=1.0,
    volume=1.0,
    pitch=0,
    sample_rate="24000",
    enable_subtitle=False,
    similarity_boost=False,
    text_normalization=True,
    enable_markdown_filter=False,
    silence_duration=0,
    session_id="",
    ssml="",
):
    text, ssml = _text(text, "text"), _text(ssml, "ssml")
    if bool(text) == bool(ssml) or len(text) > 10000:
        raise _invalid("text / ssml", "tts3_input_text")
    custom_speaker_id = _text(custom_speaker_id, "custom_speaker_id")
    voice_id = custom_speaker_id or _text(voice, "voice")
    if (
        not voice_id
        or voice_id in (".", "..")
        or any(ord(char) < 32 or ord(char) == 127 for char in voice_id)
        or (not custom_speaker_id and voice_id not in TTS_3_VOICE_IDS)
    ):
        raise _invalid("voice", "tts3_input_voice")
    if str(sample_rate) not in SEED_TTS_SAMPLE_RATES:
        raise _invalid("sample_rate", "tts3_input_option")
    if language_code not in TTS_3_LANGUAGES:
        raise _invalid("language_code", "tts3_input_option")
    booleans = {
        "enable_subtitle": enable_subtitle,
        "similarity_boost": similarity_boost,
        "text_normalization": text_normalization,
        "enable_markdown_filter": enable_markdown_filter,
    }
    for field, value in booleans.items():
        if not isinstance(value, bool):
            raise _invalid(field, "tts3_input_boolean")
    if enable_subtitle and language_code != "auto" and language_code not in TTS_3_SUBTITLE_LANGUAGES:
        raise _invalid("language_code", "tts3_input_subtitles")
    settings = {
        "speed": _number(speed, "speed", 0.5, 2.0),
        "volume": _number(volume, "volume", 0.5, 2.0),
        "pitch": _number(pitch, "pitch", -12, 12, integer=True),
    }
    instructions = _text(instructions, "instructions")
    if instructions:
        settings["instructions"] = [line.strip() for line in instructions.splitlines() if line.strip()]
    if similarity_boost:
        settings["similarity_boost"] = True
    body = {
        "text" if text else "ssml": text or ssml,
        "model_id": SEED_TTS_3_MODEL,
        "language_code": language_code,
        "output_format": {"format": "pcm", "sample_rate": int(sample_rate)},
        "voice_settings": settings,
        "enable_subtitle": enable_subtitle,
        "text_normalization": text_normalization,
        "enable_markdown_filter": enable_markdown_filter,
        "silence_duration": _number(silence_duration, "silence_duration", 0, 30000),
    }
    session_id = _text(session_id, "session_id")
    if session_id:
        body["session_id"] = session_id
    return f"/api/v3/text-to-speech/{quote(voice_id, safe='')}/streaming", body


def tts3_error(response, api_key, event=None):
    event = response.json() if event is None else event
    error = event.get("error") if isinstance(event, dict) else None
    error = error if isinstance(error, dict) else {}

    def safe(value):
        value = str(value or "-")
        if api_key:
            value = value.replace(api_key, "[redacted]")
        value = re.sub(r"https?://\S+", "[redacted URL]", value)
        return " ".join(value.split())[:500]

    return BytePlusException(
        get_text(
            "tts3_request_failed",
            status=response.status,
            code=safe(error.get("code")),
            error_type=safe(error.get("type")),
            message=safe(error.get("message") or get_text("tts3_error_unknown")),
            param=safe(error.get("param")),
            request_id=safe(error.get("request_id")),
            logid=safe(error.get("logid") or response.logid),
        )
    )


def _valid_alignment(event):
    if not isinstance(event.get("text"), str) or not isinstance(event.get("words"), list):
        return False
    for word in event["words"]:
        if not isinstance(word, dict) or not isinstance(word.get("word"), str):
            return False
        start, end = word.get("startTime"), word.get("endTime")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in (start, end)):
            return False
        if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start <= end:
            return False
    return True


def parse_tts3_stream(response, api_key=""):
    chunks, alignments = [], []
    ready, final = False, False
    session_id = ""
    try:
        for line in response.body.decode("utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if not isinstance(event, dict) or not isinstance(event.get("event"), str) or final:
                raise ValueError
            kind = event["event"]
            if kind == "error":
                raise tts3_error(response, api_key, event)
            if kind == "ready":
                if ready or not isinstance(event.get("session_id"), str):
                    raise ValueError
                ready, session_id = True, event["session_id"]
                continue
            if not ready:
                raise ValueError
            if kind == "audio":
                encoded = event.get("audio")
                if not isinstance(encoded, str) or not encoded:
                    raise ValueError
                chunks.append(base64.b64decode(encoded, validate=True))
            elif kind == "alignment":
                if not _valid_alignment(event):
                    raise ValueError
                alignments.append({"text": event["text"], "words": event["words"]})
            elif kind == "final":
                if event.get("is_final") is not True or event.get("reason") != "TTS_STREAM_FINAL_REASON_CLOSED":
                    raise ValueError
                final = True
        pcm = b"".join(chunks)
        if not ready or not final or not pcm or len(pcm) % 2:
            raise ValueError
    except (ValueError, TypeError, binascii.Error):
        raise BytePlusException(get_text("tts3_stream_invalid")) from None
    return pcm, alignments, session_id


class BytePlusSeedTTS3(comfy_io.ComfyNode):
    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return comfy_io.Schema(
            node_id="BytePlusSeedTTS3",
            display_name="BytePlus Seed Speech TTS 3",
            category=SPEECH_CATEGORY,
            description="Generate speech with dola-seed-tts-3.0, TTS 3 preset or authorized cloned voices, and optional subtitles.",
            inputs=[
                comfy_io.String.Input(
                    "text", multiline=True, default="", tooltip="Up to 10000 characters. Leave empty when using ssml."
                ),
                comfy_io.Combo.Input(
                    "voice",
                    options=TTS_3_VOICE_IDS,
                    default=DEFAULT_TTS_3_VOICE,
                    tooltip="A Seed TTS 3 preset voice. TTS 1/2 voices belong in the older TTS node.",
                ),
                comfy_io.String.Input(
                    "custom_speaker_id",
                    default="",
                    tooltip="Overrides voice with an authorized TTS 3-compatible voice ID. Older cloned voices may need upgrading in Seed Speech.",
                ),
                comfy_io.Combo.Input(
                    "language_code",
                    options=TTS_3_LANGUAGES,
                    default="auto",
                    tooltip="Output language or dialect. Explicit selection is recommended; auto detects the text language.",
                ),
                comfy_io.String.Input(
                    "instructions",
                    multiline=True,
                    default="",
                    tooltip="Delivery instructions, one per line, for compatible voices. For example: Warm and natural.",
                ),
                comfy_io.Float.Input("speed", default=1.0, min=0.5, max=2.0, step=0.05),
                comfy_io.Float.Input("volume", default=1.0, min=0.5, max=2.0, step=0.05),
                comfy_io.Int.Input("pitch", default=0, min=-12, max=12),
                comfy_io.Combo.Input(
                    "sample_rate",
                    options=SEED_TTS_SAMPLE_RATES,
                    default="24000",
                    tooltip="PCM sample rate in Hz. Use Save Audio to choose the saved file format.",
                ),
                comfy_io.Boolean.Input(
                    "enable_subtitle",
                    default=False,
                    tooltip="Return word timings for supported languages. With auto, unsupported detected languages cause an API error.",
                ),
                comfy_io.Boolean.Input(
                    "similarity_boost",
                    default=False,
                    optional=True,
                    advanced=True,
                    tooltip="Enable similarity boost for compatible cloned voices.",
                ),
                comfy_io.Boolean.Input(
                    "text_normalization",
                    default=True,
                    optional=True,
                    advanced=True,
                    tooltip="Normalize numbers, dates and similar text before synthesis.",
                ),
                comfy_io.Boolean.Input(
                    "enable_markdown_filter",
                    default=False,
                    optional=True,
                    advanced=True,
                    tooltip="Parse Markdown in the input text.",
                ),
                comfy_io.Int.Input(
                    "silence_duration",
                    default=0,
                    min=0,
                    max=30000,
                    optional=True,
                    advanced=True,
                    tooltip="Silence appended after the final sentence, in milliseconds.",
                ),
                comfy_io.String.Input(
                    "session_id",
                    default="",
                    optional=True,
                    advanced=True,
                    tooltip="Reuse one session ID for sequential requests in the same conversation. Leave empty for independent speech.",
                ),
                comfy_io.String.Input(
                    "ssml",
                    default="",
                    multiline=True,
                    optional=True,
                    advanced=True,
                    tooltip="Complete SSML instead of text, never both. Supported tags depend on the service version.",
                ),
                comfy_io.Int.Input(
                    "seed",
                    default=0,
                    min=0,
                    max=SPEECH_MAX_SEED,
                    control_after_generate=True,
                    tooltip="Local rerun control only; not sent to the API.",
                ),
            ],
            outputs=[
                comfy_io.Audio.Output(display_name="audio"),
                comfy_io.String.Output(display_name="subtitles_json"),
                comfy_io.String.Output(display_name="srt"),
                comfy_io.String.Output(display_name="session_id"),
            ],
        )

    @classmethod
    @with_client("speech_client", get_speech_client)
    async def execute(
        cls,
        speech_client,
        text,
        voice=DEFAULT_TTS_3_VOICE,
        custom_speaker_id="",
        language_code="auto",
        instructions="",
        speed=1.0,
        volume=1.0,
        pitch=0,
        sample_rate="24000",
        enable_subtitle=False,
        similarity_boost=False,
        text_normalization=True,
        enable_markdown_filter=False,
        silence_duration=0,
        session_id="",
        ssml="",
        seed=0,
    ) -> comfy_io.NodeOutput:
        path, body = build_tts3_request(
            text,
            voice,
            custom_speaker_id,
            language_code,
            instructions,
            speed,
            volume,
            pitch,
            sample_rate,
            enable_subtitle,
            similarity_boost,
            text_normalization,
            enable_markdown_filter,
            silence_duration,
            session_id,
            ssml,
        )
        response = await speech_post(
            speech_client,
            path,
            body,
            operation="TTS 3",
            request_id_header="X-Request-Id",
            error_handler=lambda response: tts3_error(response, speech_client.api_key),
        )
        pcm, alignments, session_id = parse_tts3_stream(response, speech_client.api_key)
        segments = subtitle_segments(alignments)
        return comfy_io.NodeOutput(
            await asyncio.to_thread(pcm16_to_audio, pcm, int(sample_rate)),
            json.dumps({"raw": alignments, "segments": segments}, ensure_ascii=False),
            build_srt(segments),
            session_id,
        )


NODES = [BytePlusSeedTTS3]
