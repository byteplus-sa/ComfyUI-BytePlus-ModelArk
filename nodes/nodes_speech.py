import base64
import io
import json
import os
import uuid

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
    SEED_ASR_FAST_MAX_BYTES,
    SEED_ASR_FAST_PATH,
    SEED_ASR_LANGUAGES,
    SEED_ASR_QUERY_PATH,
    SEED_ASR_SAMPLE_RATE,
    SEED_ASR_SUBMIT_PATH,
    SEED_ASR_ZH_VARIANTS,
    SEED_AUDIO_FORMATS,
    SEED_AUDIO_MAX_AUDIO_REFS,
    SEED_AUDIO_MAX_PROMPT_CHARS,
    SEED_AUDIO_PATH,
    SEED_AUDIO_PCM_DEFAULT_RATE,
    SEED_AUDIO_REF_MAX_BYTES,
    SEED_AUDIO_REF_MAX_SECONDS,
    SEED_AUDIO_SAMPLE_RATES,
    SEED_TTS_2_SAMPLE_RATES,
    SEED_TTS_APP_KEY,
    SEED_TTS_CONTEXT_LANGUAGES,
    SEED_TTS_DEFAULT_UNSUPPORTED_CHAR_RATIO,
    SEED_TTS_LANGUAGES,
    SEED_TTS_PATH,
    SEED_TTS_SAMPLE_RATES,
    SPEECH_API_KEY_ENV,
    SPEECH_ASR_PENDING_CODES,
    SPEECH_ASR_POLL_SECONDS,
    SPEECH_ASR_SILENT_AUDIO_CODE,
    SPEECH_REGION_BASE_URLS,
)
from .models_config import (
    SEED_ASR_MODELS,
    SEED_ASR_UI_OPTIONS,
    SEED_AUDIO_MODELS,
    SEED_TTS_2_UI_MODEL,
    SEED_TTS_MODELS,
)
from .nodes_shared import (
    GLOBAL_CATEGORY,
    BytePlusException,
    _notify_api_key_saved,
    _tensor2images,
    get_text,
    log_msg,
)
from .seed_speech_voices import DEFAULT_TTS_VOICE, TTS_2_VOICE_IDS
from .speech_api import (
    SPEECH_API_KEY_STORE,
    BytePlusSpeechClientType,
    SeedSpeechClient,
    check_code,
    download_bytes,
    iter_json_objects,
    require_speech_client,
    sleep_interruptible,
    speech_error,
    speech_post,
)

SPEECH_CATEGORY = f"{GLOBAL_CATEGORY}/Speech"
ENV_KEY_OPTION = f"Environment ({SPEECH_API_KEY_ENV})"
USER_ID = "comfyui"
MAX_SEED = 0xffffffffffffffff


def _is_url(value, schemes=("http://", "https://", "asset://")):
    return value.lower().startswith(schemes)


def _validated_url(value, field, schemes=("http://", "https://", "asset://")):
    value = (value or "").strip()
    if value and not _is_url(value, schemes):
        raise BytePlusException(get_text("speech_bad_url", field=field))
    return value


def _size_mb(num_bytes):
    return f"{num_bytes / (1024 * 1024):.2f}"


def _image_to_jpeg_base64(image):
    with io.BytesIO() as buffer:
        _tensor2images(image)[0].convert("RGB").save(buffer, format="JPEG", quality=95)
        data = buffer.getvalue()
    if len(data) > SEED_AUDIO_REF_MAX_BYTES:
        raise BytePlusException(get_text(
            "seed_audio_ref_too_large", kind="image", size_mb=_size_mb(len(data)),
            max_mb=_size_mb(SEED_AUDIO_REF_MAX_BYTES),
        ))
    return base64.b64encode(data).decode("utf-8")


def _segments_json(raw, segments):
    return json.dumps({"raw": raw, "segments": segments}, ensure_ascii=False)


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
                SPEECH_API_KEY_STORE.upsert(name, api_key)
                log_msg("speech_key_saved", name=name)
                _notify_api_key_saved(cls.hidden.unique_id, name, api_key, store="speech")
        elif key_name == ENV_KEY_OPTION:
            api_key = os.environ.get(SPEECH_API_KEY_ENV, "").strip()
            if not api_key:
                raise BytePlusException(get_text("speech_env_key_missing", env=SPEECH_API_KEY_ENV))
        else:
            SPEECH_API_KEY_STORE.load()
            api_key = SPEECH_API_KEY_STORE.find_api_key(key_name)
            if not api_key:
                raise BytePlusException(get_text("speech_key_not_found", key_name=key_name))
        return comfy_io.NodeOutput(SeedSpeechClient(api_key, region))


# Seed Audio 1.0

def build_seed_audio_references(sources, audios, image=None, image_url=""):
    """
    references[] for Seed Audio. Slot N (connected audio, or a speaker ID / URL
    text) is always @AudioN, so the slots must be filled in order.
    """
    slots = []
    for index in range(SEED_AUDIO_MAX_AUDIO_REFS):
        slot = index + 1
        source = (sources[index] or "").strip()
        audio = audios[index]
        if source and audio is not None:
            raise BytePlusException(get_text("seed_audio_slot_conflict", slot=slot))
        if audio is not None:
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
            slots.append({"audio_data": base64.b64encode(wav).decode("utf-8")})
        elif source:
            if "://" in source:
                slots.append({"audio_url": _validated_url(source, f"ref_audio_{slot}_source")})
            else:
                slots.append({"speaker": source})
        else:
            slots.append(None)

    used = [i for i, item in enumerate(slots) if item is not None]
    if used:
        missing = next((i for i in range(used[-1]) if slots[i] is None), None)
        if missing is not None:
            raise BytePlusException(get_text("seed_audio_slot_gap", slot=used[-1] + 1, missing=missing + 1))
    references = [item for item in slots if item is not None]

    image_url = _validated_url(image_url, "ref_image_url")
    if image is not None and image_url:
        raise BytePlusException(get_text("seed_audio_image_conflict"))
    if (image is not None or image_url) and references:
        raise BytePlusException(get_text("seed_audio_image_and_audio"))
    if image is not None:
        references.append({"image_data": _image_to_jpeg_base64(image)})
    elif image_url:
        references.append({"image_url": image_url})
    return references


def build_seed_audio_request(model, text_prompt, references, audio_format="wav", sample_rate="default",
                             speech_rate=0, loudness_rate=0, pitch_rate=0, enable_subtitle=False,
                             aigc_watermark=False, aigc_metadata=False, content_producer="",
                             produce_id="", content_propagator="", propagate_id=""):
    text_prompt = (text_prompt or "").strip()
    if not text_prompt:
        raise BytePlusException(get_text("seed_audio_prompt_empty"))
    if len(text_prompt) > SEED_AUDIO_MAX_PROMPT_CHARS:
        raise BytePlusException(get_text(
            "seed_audio_prompt_too_long", count=len(text_prompt), max=SEED_AUDIO_MAX_PROMPT_CHARS
        ))
    audio_config = {"format": audio_format}
    if sample_rate and sample_rate != "default":
        audio_config["sample_rate"] = int(sample_rate)
    if speech_rate:
        audio_config["speech_rate"] = int(speech_rate)
    if loudness_rate:
        audio_config["loudness_rate"] = int(loudness_rate)
    if pitch_rate:
        audio_config["pitch_rate"] = int(pitch_rate)
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


class BytePlusSeedAudio(comfy_io.ComfyNode):
    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        source_tooltip = (
            "Reference @Audio{n}: a TTS 2.0 or cloned speaker ID, or an audio URL "
            "(http(s):// or asset://). Leave empty to use ref_audio_{n}, or no reference."
        )
        return comfy_io.Schema(
            node_id="BytePlusSeedAudio",
            display_name="BytePlus Seed Audio 1.0",
            category=SPEECH_CATEGORY,
            description=(
                "Generate speech, sound effects and voiceovers up to 120 s from a prompt, "
                "with up to three reference voices (@Audio1-3) or one reference image."
            ),
            inputs=[
                BytePlusSpeechClientType.Input("speech_client"),
                comfy_io.Combo.Input("model", options=SEED_AUDIO_MODELS, default=SEED_AUDIO_MODELS[0]),
                comfy_io.String.Input(
                    "text_prompt",
                    multiline=True,
                    default="",
                    tooltip=(
                        "Prompt or text to speak (up to 3000 characters). Refer to reference "
                        "voices as @Audio1, @Audio2, @Audio3. With an image reference, enter "
                        "only the text to speak."
                    ),
                ),
                comfy_io.String.Input("ref_audio_1_source", default="", tooltip=source_tooltip.format(n=1)),
                comfy_io.String.Input("ref_audio_2_source", default="", tooltip=source_tooltip.format(n=2)),
                comfy_io.String.Input("ref_audio_3_source", default="", tooltip=source_tooltip.format(n=3)),
                comfy_io.String.Input(
                    "ref_image_url",
                    default="",
                    tooltip="Reference image URL (http(s):// or asset://). Cannot be combined with audio references.",
                ),
                comfy_io.Combo.Input("audio_format", options=SEED_AUDIO_FORMATS, default="wav"),
                comfy_io.Combo.Input(
                    "sample_rate",
                    options=SEED_AUDIO_SAMPLE_RATES,
                    default="default",
                    tooltip="Output sample rate in Hz. default: 40000 for wav and pcm, 44100 for mp3.",
                ),
                comfy_io.Int.Input("speech_rate", default=0, min=-50, max=100, tooltip="100 = 2x speed, -50 = 0.5x."),
                comfy_io.Int.Input("loudness_rate", default=0, min=-50, max=100, tooltip="100 = 2x volume, -50 = 0.5x."),
                comfy_io.Int.Input("pitch_rate", default=0, min=-12, max=12, tooltip="Pitch shift in semitones."),
                comfy_io.Boolean.Input("enable_subtitle", default=False, tooltip="Return sentence and word timestamps."),
                comfy_io.Boolean.Input(
                    "aigc_watermark",
                    default=False,
                    tooltip="Add the audible AI-generated marker at the end of the audio.",
                ),
                comfy_io.Boolean.Input(
                    "aigc_metadata",
                    default=False,
                    advanced=True,
                    tooltip="Implicit watermark: write AI-generation metadata into the audio header.",
                ),
                comfy_io.String.Input("content_producer", default="", advanced=True,
                                      tooltip="Implicit watermark: name or code of the synthesis provider."),
                comfy_io.String.Input("produce_id", default="", advanced=True,
                                      tooltip="Implicit watermark: content production ID."),
                comfy_io.String.Input("content_propagator", default="", advanced=True,
                                      tooltip="Implicit watermark: name or code of the distributor."),
                comfy_io.String.Input("propagate_id", default="", advanced=True,
                                      tooltip="Implicit watermark: content distribution ID."),
                comfy_io.Int.Input(
                    "seed",
                    default=0,
                    min=0,
                    max=MAX_SEED,
                    control_after_generate=True,
                    tooltip="Not sent to the API; change it to generate a new take.",
                ),
                comfy_io.Audio.Input("ref_audio_1", optional=True, tooltip="Reference voice @Audio1 (up to 30 s)."),
                comfy_io.Audio.Input("ref_audio_2", optional=True, tooltip="Reference voice @Audio2 (up to 30 s)."),
                comfy_io.Audio.Input("ref_audio_3", optional=True, tooltip="Reference voice @Audio3 (up to 30 s)."),
                comfy_io.Image.Input("ref_image", optional=True, tooltip="Reference image (only one; no audio references)."),
            ],
            outputs=[
                comfy_io.Audio.Output(display_name="audio"),
                comfy_io.String.Output(display_name="subtitles_json"),
                comfy_io.String.Output(display_name="srt"),
                comfy_io.Float.Output(display_name="duration"),
                comfy_io.String.Output(display_name="url"),
            ],
        )

    @classmethod
    async def execute(cls, speech_client, model, text_prompt, ref_audio_1_source="", ref_audio_2_source="",
                      ref_audio_3_source="", ref_image_url="", audio_format="wav", sample_rate="default",
                      speech_rate=0, loudness_rate=0, pitch_rate=0, enable_subtitle=False,
                      aigc_watermark=False, aigc_metadata=False, content_producer="", produce_id="",
                      content_propagator="", propagate_id="", seed=0, ref_audio_1=None, ref_audio_2=None,
                      ref_audio_3=None, ref_image=None) -> comfy_io.NodeOutput:
        require_speech_client(speech_client)
        references = build_seed_audio_references(
            [ref_audio_1_source, ref_audio_2_source, ref_audio_3_source],
            [ref_audio_1, ref_audio_2, ref_audio_3],
            ref_image,
            ref_image_url,
        )
        body = build_seed_audio_request(
            model, text_prompt, references, audio_format, sample_rate, speech_rate,
            loudness_rate, pitch_rate, enable_subtitle, aigc_watermark, aigc_metadata,
            content_producer, produce_id, content_propagator, propagate_id,
        )
        operation = "Seed Audio"
        response = await speech_post(speech_client, SEED_AUDIO_PATH, body, operation=operation)
        result = response.json()
        if not isinstance(result, dict):
            raise speech_error(operation, response, message=response.text()[:300])
        check_code(operation, response, result.get("code"), result.get("message"))

        url = result.get("url") or ""
        if result.get("audio"):
            audio_bytes = base64.b64decode(result["audio"])
        elif url:
            audio_bytes = await download_bytes(url, operation)
        else:
            raise BytePlusException(get_text("speech_empty_audio", operation=operation))
        if audio_format == "pcm":
            pcm_rate = SEED_AUDIO_PCM_DEFAULT_RATE if sample_rate == "default" else int(sample_rate)
            audio = pcm16_to_audio(audio_bytes, pcm_rate)
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
        return comfy_io.NodeOutput(
            audio,
            _segments_json(subtitle, segments),
            build_srt(segments),
            float(duration),
            url,
        )


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
            chunks.append(base64.b64decode(data))
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
                BytePlusSpeechClientType.Input("speech_client"),
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
                    tooltip="Overrides voice: a cloned voice (S_...) or a TTS 1.0 speaker ID.",
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
                    max=MAX_SEED,
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


def _asr_context(hotwords="", context_text="", context_image_url=""):
    """corpus.context JSON string: hotwords plus dialogue/scene context (text lines, one image)."""
    context = {}
    words = _word_list(hotwords)
    if words:
        context["hotwords"] = [{"word": w} for w in words]
    data = [{"text": line.strip()} for line in (context_text or "").splitlines() if line.strip()]
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


def build_asr_request(model, audio=None, audio_url="", language="auto", enable_punc=True,
                      enable_itn=True, enable_ddc=False, enable_speaker_info=False, hotwords="",
                      enable_auto_lang=False, enable_lid=False, enable_channel_split=False,
                      vad_segment=False, end_window_size=0, output_zh_variant="none",
                      filter_system_sensitive_words=False, remove_words="", mask_words="",
                      wrap_sensitive_words=False, context_text="", context_image_url=""):
    """(mode, extra headers, body) for Seed Speech ASR."""
    mode, resource_id = SEED_ASR_MODELS[model]
    audio_url = _validated_url(audio_url, "audio_url", schemes=("http://", "https://"))
    context_image_url = _validated_url(context_image_url, "context_image_url", schemes=("http://", "https://"))
    if audio is None and not audio_url:
        raise BytePlusException(get_text("asr_no_input"))
    if audio is not None and audio_url:
        raise BytePlusException(get_text("asr_both_inputs"))
    if enable_lid and mode == "fast":
        raise BytePlusException(get_text("asr_lid_standard_only"))
    end_window_size = int(end_window_size or 0)
    if end_window_size and not 300 <= end_window_size <= 5000:
        raise BytePlusException(get_text("asr_end_window_out_of_range"))

    if audio is not None:
        if mode != "fast":
            raise BytePlusException(get_text("asr_standard_needs_url", model=model))
        channels = 1
        if enable_channel_split:
            channels = int(audio_waveform(audio)[0].shape[0])
            if channels != 2:
                raise BytePlusException(get_text("asr_channel_split_needs_stereo"))
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
        extension = audio_url.split("?", 1)[0].rsplit(".", 1)[-1].lower()
        if extension in ("wav", "mp3", "ogg"):
            audio_config["format"] = extension
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
    context = _asr_context(hotwords, context_text, context_image_url)
    if context:
        request["corpus"] = {"context": context}

    headers = {"X-Api-Resource-Id": resource_id, "X-Api-Sequence": "-1"}
    body = {"user": {"uid": USER_ID}, "audio": audio_config, "request": request}
    return mode, headers, body


def _asr_outputs(result_body, with_speaker):
    result = result_body.get("result") if isinstance(result_body, dict) else None
    if isinstance(result, list):
        result = result[0] if result else {}
    result = result if isinstance(result, dict) else {}
    utterances = result.get("utterances") or []
    segments = subtitle_segments(utterances)
    audio_info = result_body.get("audio_info") if isinstance(result_body, dict) else None
    duration_ms = (audio_info or {}).get("duration") or (result.get("additions") or {}).get("duration") or 0
    try:
        duration = float(duration_ms) / 1000.0
    except (TypeError, ValueError):
        duration = 0.0
    return comfy_io.NodeOutput(
        str(result.get("text") or ""),
        json.dumps(utterances, ensure_ascii=False),
        build_srt(segments, with_speaker=with_speaker),
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
                BytePlusSpeechClientType.Input("speech_client"),
                comfy_io.Combo.Input(
                    "model",
                    options=SEED_ASR_UI_OPTIONS,
                    default=SEED_ASR_UI_OPTIONS[0],
                    tooltip=(
                        "seed-asr-fast: one request, audio up to 2 h / 100 MB. seed-asr-2.0 / 1.0: "
                        "submit and poll, public audio_url only, up to 5 h."
                    ),
                ),
                comfy_io.String.Input(
                    "audio_url",
                    default="",
                    tooltip="Public http(s) audio URL. Use this or the audio input.",
                ),
                comfy_io.Combo.Input(
                    "language",
                    options=SEED_ASR_LANGUAGES,
                    default="auto",
                    tooltip="auto recognizes Chinese, English and Chinese dialects; pick a language for others.",
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
                    tooltip="Names or terms to favour, one per line or comma-separated (up to 5000).",
                ),
                comfy_io.String.Input(
                    "context_text",
                    multiline=True,
                    default="",
                    advanced=True,
                    tooltip="Dialogue history or scene description, one entry per line, newest first (up to 20 entries / 800 tokens).",
                ),
                comfy_io.String.Input(
                    "context_image_url",
                    default="",
                    advanced=True,
                    tooltip="ASR 2.0 visual context: public JPEG/PNG URL (up to 500 KB) showing what is being talked about.",
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
                comfy_io.Audio.Input("audio", optional=True),
            ],
            outputs=[
                comfy_io.String.Output(display_name="text"),
                comfy_io.String.Output(display_name="utterances_json"),
                comfy_io.String.Output(display_name="srt"),
                comfy_io.Float.Output(display_name="duration"),
            ],
        )

    @classmethod
    async def execute(cls, speech_client, model, audio=None, **options) -> comfy_io.NodeOutput:
        require_speech_client(speech_client)
        mode, headers, body = build_asr_request(model, audio, **options)
        labels = bool(options.get("enable_speaker_info") or options.get("enable_channel_split"))
        operation = "ASR"
        if mode == "fast":
            response = await speech_post(speech_client, SEED_ASR_FAST_PATH, body, operation=operation, headers=headers)
            if response.status_code == SPEECH_ASR_SILENT_AUDIO_CODE:
                return _silent_outputs()
            check_code(operation, response, response.status_code)
            return _asr_outputs(response.json(), labels)

        task_id = str(uuid.uuid4())
        headers = {**headers, "X-Api-Request-Id": task_id}
        response = await speech_post(speech_client, SEED_ASR_SUBMIT_PATH, body, operation=operation, headers=headers)
        check_code(operation, response, response.status_code)
        log_msg("asr_task_submitted", task_id=task_id)
        while True:
            await sleep_interruptible(SPEECH_ASR_POLL_SECONDS)
            response = await speech_post(speech_client, SEED_ASR_QUERY_PATH, {}, operation=operation, headers=headers)
            status = response.status_code
            if status in SPEECH_ASR_PENDING_CODES:
                continue
            if status == SPEECH_ASR_SILENT_AUDIO_CODE:
                return _silent_outputs()
            check_code(operation, response, status)
            if status is None and not (response.json() or {}).get("result"):
                raise speech_error(operation, response)
            return _asr_outputs(response.json(), labels)
