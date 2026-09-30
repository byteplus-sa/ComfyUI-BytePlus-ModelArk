import io
import wave

import numpy
import torch
import torch.nn.functional as F

from .nodes_shared import BytePlusException, get_text


def audio_waveform(audio):
    """
    First clip of a ComfyUI AUDIO as ([channels, samples] float tensor on CPU,
    sample_rate). Raises BytePlusException for anything else.
    """
    if not isinstance(audio, dict):
        raise BytePlusException(get_text("speech_audio_invalid"))
    waveform = audio.get("waveform")
    sample_rate = int(audio.get("sample_rate", audio.get("sampler_rate", 0)) or 0)
    if not isinstance(waveform, torch.Tensor) or sample_rate <= 0:
        raise BytePlusException(get_text("speech_audio_invalid"))
    if waveform.ndim == 2:
        waveform = waveform[None]
    if waveform.ndim != 3 or waveform.shape[0] < 1 or waveform.shape[-1] < 1:
        raise BytePlusException(get_text("speech_audio_invalid"))
    return waveform[0].detach().cpu().float(), sample_rate


def audio_duration(audio):
    waveform, sample_rate = audio_waveform(audio)
    return float(waveform.shape[-1]) / float(sample_rate)


def _resample(waveform, source_rate, target_rate):
    if source_rate == target_rate:
        return waveform
    try:
        import torchaudio.functional as audio_functional

        return audio_functional.resample(waveform, source_rate, target_rate)
    except ImportError:
        length = max(1, round(waveform.shape[-1] * target_rate / source_rate))
        return F.interpolate(waveform[None], size=length, mode="linear", align_corners=False)[0]


def audio_to_wav_bytes(audio, sample_rate=None, mono=False):
    """
    Encode a ComfyUI AUDIO as 16-bit PCM WAV bytes, optionally downmixed to
    mono and resampled.
    """
    waveform, source_rate = audio_waveform(audio)
    if mono and waveform.shape[0] > 1:
        waveform = waveform.mean(dim=0, keepdim=True)
    target_rate = int(sample_rate or source_rate)
    waveform = _resample(waveform, source_rate, target_rate)
    samples = (torch.clamp(waveform, -1.0, 1.0).numpy() * 32767.0).astype(numpy.int16)
    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(int(samples.shape[0]))
            wav_file.setsampwidth(2)
            wav_file.setframerate(target_rate)
            wav_file.writeframes(numpy.ascontiguousarray(samples.T).tobytes())
        return buffer.getvalue()


def pcm16_to_audio(data, sample_rate, channels=1):
    """Raw little-endian 16-bit PCM -> ComfyUI AUDIO."""
    frame_bytes = 2 * channels
    usable = len(data) - len(data) % frame_bytes
    if usable <= 0:
        raise BytePlusException(get_text("speech_audio_decode_failed", e="no audio samples"))
    samples = numpy.frombuffer(data[:usable], dtype="<i2").astype(numpy.float32) / 32768.0
    waveform = torch.from_numpy(samples.reshape(-1, channels).T.copy())
    return {"waveform": waveform[None], "sample_rate": int(sample_rate)}


_SAMPLE_SCALES = {"u8": None, "s16": 32768.0, "s32": 2147483648.0, "flt": 1.0, "dbl": 1.0}


def decode_audio_bytes(data):
    """Decode encoded audio (wav, mp3, ogg/opus, ...) with PyAV -> ComfyUI AUDIO."""
    try:
        import av

        chunks = []
        sample_rate = None
        with av.open(io.BytesIO(data)) as container:
            stream = container.streams.audio[0]
            for frame in container.decode(stream):
                array = frame.to_ndarray()
                channels = len(frame.layout.channels)
                if not frame.format.is_planar:
                    array = array.reshape(-1, channels).T
                kind = frame.format.name.rstrip("p")
                if kind == "u8":
                    array = (array.astype(numpy.float32) - 128.0) / 128.0
                else:
                    array = array.astype(numpy.float32) / _SAMPLE_SCALES.get(kind, 1.0)
                chunks.append(array)
                sample_rate = frame.sample_rate
        if not chunks or not sample_rate:
            raise ValueError("no audio frames")
        waveform = torch.from_numpy(numpy.concatenate(chunks, axis=1).copy())
        return {"waveform": waveform[None], "sample_rate": int(sample_rate)}
    except BytePlusException:
        raise
    except Exception as e:
        raise BytePlusException(get_text("speech_audio_decode_failed", e=e))


def _time_ms(item, ms_key, seconds_key):
    value = item.get(ms_key)
    if isinstance(value, (int, float)) and value >= 0:
        return int(value)
    value = item.get(seconds_key)
    if isinstance(value, (int, float)) and value >= 0:
        return int(round(value * 1000))
    return None


def subtitle_segments(items):
    """
    Normalize utterance/sentence lists from Seed Speech responses into
    [{"start_ms", "end_ms", "text", "speaker", "channel"}]. Accepts millisecond fields
    (start_time/end_time) and second fields (startTime/endTime), and falls back
    to the first/last timed word.
    """
    segments = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        words = [w for w in item.get("words") or [] if isinstance(w, dict)]
        text = item.get("text")
        if not text:
            text = "".join(str(w.get("word") or w.get("text") or "") for w in words)
        start = _time_ms(item, "start_time", "startTime")
        end = _time_ms(item, "end_time", "endTime")
        timed = [
            (_time_ms(w, "start_time", "startTime"), _time_ms(w, "end_time", "endTime"))
            for w in words
        ]
        timed = [t for t in timed if t[0] is not None and t[1] is not None]
        if start is None and timed:
            start = timed[0][0]
        if end is None and timed:
            end = timed[-1][1]
        text = str(text or "").strip()
        if start is None or end is None or not text:
            continue
        additions = item.get("additions") if isinstance(item.get("additions"), dict) else {}
        segments.append({
            "start_ms": start,
            "end_ms": max(end, start),
            "text": text,
            "speaker": str(additions.get("speaker") or item.get("speaker") or ""),
            "channel": str(additions.get("channel_id") or ""),
        })
    return segments


def _srt_time(ms):
    hours, rest = divmod(int(ms), 3600000)
    minutes, rest = divmod(rest, 60000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def build_srt(segments, with_speaker=False):
    """SRT text; with_speaker prefixes channel and speaker labels when present."""
    blocks = []
    for index, segment in enumerate(segments, start=1):
        text = segment["text"]
        if with_speaker:
            labels = []
            if segment.get("channel"):
                labels.append(f"Channel {segment['channel']}")
            if segment.get("speaker"):
                labels.append(f"Speaker {segment['speaker']}")
            if labels:
                text = f"{' / '.join(labels)}: {text}"
        blocks.append(
            f"{index}\n{_srt_time(segment['start_ms'])} --> {_srt_time(segment['end_ms'])}\n{text}\n"
        )
    return "\n".join(blocks)
