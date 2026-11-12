"""Pure submit builders implementing PLAN_LAS_COMFYUI_INTEGRATION.md."""

import ipaddress
import json
import math
import re
from urllib.parse import unquote, urlsplit

from .las_config import (
    CAPTION_FORMATS,
    DUBBING_MODES,
    GENJUTSU_AUTO_DURATION_ONLY_MODELS,
    GENJUTSU_DURATION_MODES,
    GENJUTSU_GATED_FIELDS,
    GENJUTSU_IMAGE_REQUIRED_TEMPLATES,
    GENJUTSU_MODEL_RESOLUTIONS,
    GENJUTSU_MODELS,
    GENJUTSU_OPERATOR_ID,
    GENJUTSU_RATIOS,
    GENJUTSU_TEMPLATES,
    INPUT_LANGUAGES,
    LAS_OPERATOR_VERSION,
    OUTPUT_LANGUAGE_FONTS,
    OUTPUT_LANGUAGES,
    RESTORATION_FIELDS,
    RESTORATION_MODES,
    SERVER_DEFAULT,
    STYLE_FIELDS,
    SUBTITLE_STYLE_MODES,
    TRANSLATION_MODES,
    TRANSLATION_OPERATOR_ID,
)
from .nodes_shared import BytePlusException, get_text


def _fail(field, detail):
    raise BytePlusException(get_text("err_las_input", detail=f"{field}: {detail}"))


def _only(values, fields, group):
    if not isinstance(values, dict):
        _fail(group, "must be an object")
    if any(not isinstance(key, str) for key in values):
        _fail(group, "field names must be strings")
    unknown = values.keys() - set(fields)
    if unknown:
        _fail(group, f"undeclared fields: {', '.join(sorted(unknown))}")


def _default(values, field, default):
    value = values.get(field)
    return default if value is None else value


def _text(value, field, required=False):
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        _fail(field, "must be a string")
    value = value.strip()
    if required and not value:
        _fail(field, "must not be empty")
    return value


def _enum(value, choices, field):
    if not isinstance(value, str) or value not in choices:
        _fail(field, f"must be one of {', '.join(choices)}")
    return value


def _boolean(value, field):
    if type(value) is not bool:
        _fail(field, "must be a boolean")
    return value


def _integer(value, field, minimum=None, maximum=None):
    if type(value) is not int:
        _fail(field, "must be an integer, not a boolean or decimal")
    if minimum is not None and value < minimum:
        _fail(field, f"must be at least {minimum}")
    if maximum is not None and value > maximum:
        _fail(field, f"must be at most {maximum}")
    return value


def _number(value, field, spec):
    if spec["type"] == "int":
        return _integer(value, field, spec.get("min"), spec.get("max"))
    if type(value) not in (int, float):
        _fail(field, "must be a finite number, not a boolean")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        _fail(field, "must be finite")
    if value < spec["min"] or value > spec["max"] or (spec.get("max_exclusive") and value == spec["max"]):
        closing = ")" if spec.get("max_exclusive") else "]"
        _fail(field, f"must be in [{spec['min']}, {spec['max']}{closing}")
    return value


def _group(value, field, default, choices):
    if value is None:
        return default, {}
    if isinstance(value, str):
        return _enum(value, choices, field), {}
    if not isinstance(value, dict) or field not in value:
        _fail(field, f"must be a selector string or an object containing {field}")
    return _enum(_default(value, field, default), choices, field), {
        key: item for key, item in value.items() if key != field
    }


def validate_las_reference(value, field="video_url", *, allow_asset=False, allow_bare_asset=False, https_only=False):
    value = _text(value, field, required=True)
    if re.search(r"[\x00-\x20\x7f\\]", value):
        _fail(field, "must not contain whitespace, control characters or backslashes")
    if allow_bare_asset and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        return value
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port
    except ValueError:
        _fail(field, "has an invalid URL authority or port")
    if not host or parts.username is not None or parts.password is not None or parts.fragment:
        _fail(field, "must have a host and no userinfo or fragment")
    if parts.scheme == "asset" and allow_asset and not https_only:
        if (
            port is not None
            or parts.path
            or parts.query
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", parts.netloc)
        ):
            _fail(field, "must be an asset:// identifier")
        return value
    if parts.scheme == "tos" and not https_only:
        if (
            port is not None
            or parts.query
            or not parts.path.strip("/")
            or not re.fullmatch(r"[A-Za-z0-9_.-]+", parts.netloc)
        ):
            _fail(field, "must be a tos://bucket/key reference with a nonempty path")
        if any(segment in (".", "..") for segment in unquote(parts.path).split("/")):
            _fail(field, "must not contain traversal segments")
        return value
    schemes = ("https",) if https_only else ("http", "https")
    if parts.scheme not in schemes:
        _fail(field, "requires a public HTTPS URL" if https_only else "requires HTTP/HTTPS or a TOS reference")
    host = host.lower().rstrip(".")
    if "%" in host or not host:
        _fail(field, "has an invalid host")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host or host.endswith((".localhost", ".local", ".internal")):
            _fail(field, "must use a public host")
        try:
            ascii_host = host.encode("idna").decode("ascii")
        except UnicodeError:
            _fail(field, "has an invalid host")
        if any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label) for label in ascii_host.split(".")):
            _fail(field, "has an invalid host")
    else:
        if not address.is_global:
            _fail(field, "must not use a private, local or reserved address")
    return value


def _tos_output(value, field):
    value = validate_las_reference(value, field)
    if urlsplit(value).scheme != "tos":
        _fail(field, "must be a writable tos://bucket/prefix directory")
    return value


def _items(value, field):
    if isinstance(value, str):
        return re.split(r",|\r\n|\r|\n", value)
    if isinstance(value, (list, tuple)):
        return list(value)
    _fail(field, "must be a list of strings or a comma/newline-separated string")


def _codes(value, field, choices, *, required=False, strict_positions=False):
    result = []
    for item in _items(value, field):
        if not isinstance(item, str):
            _fail(field, "items must be strings")
        item = _text(item, field)
        if not item or item in result:
            if strict_positions:
                _fail(field, "must not contain blank or duplicate items when fonts are supplied")
            continue
        result.append(_enum(item, choices, field))
    if required and not result:
        _fail(field, "requires at least one item")
    return result


def parse_reference_list(value: str | list[str] | tuple[str, ...] | None, field: str = "image_urls") -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [line.strip() for line in value.splitlines() if line.strip()]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        _fail(field, "must be a reference list or one reference per line")
    return [validate_las_reference(item, field, allow_asset=True) for item in items]


def _reject_json_constant(value):
    raise ValueError("Nonfinite JSON constant")


def _json_array(value, field):
    if isinstance(value, str):
        if not value.strip():
            return None
        try:
            value = json.loads(value, parse_constant=_reject_json_constant)
        except (ValueError, RecursionError):
            _fail(field, "must be valid JSON without nonfinite values")
    if value is not None and not isinstance(value, list):
        _fail(field, "must be an array or null")
    return value


def _fonts(value, languages):
    value = _json_array(value, "subtitle_font_by_language")
    if value is None:
        return None
    if len(value) != len(languages):
        _fail("subtitle_font_by_language", "must have one selector per target language, in the same order")
    result = []
    for language, font in zip(languages, value, strict=True):
        if font is None or font == "":
            result.append(None)
        else:
            result.append(_enum(font, OUTPUT_LANGUAGE_FONTS[language], "subtitle_font_by_language"))
    return result


def _style_group(value):
    selected, fields = _group(value, "subtitle_style", "automatic", SUBTITLE_STYLE_MODES)
    _only(fields, STYLE_FIELDS, "subtitle_style")
    if selected == "automatic" and any(item is not None for item in fields.values()):
        _fail("subtitle_style", "automatic styling cannot contain custom controls")
    return selected, fields


def _style(fields, languages):
    result = {}
    for field, spec in STYLE_FIELDS.items():
        value = _default(fields, field, spec["default"])
        if spec["type"] == "fonts":
            fonts = _fonts(value, languages)
            if fonts is not None:
                result[field] = fonts
        elif spec["type"] == "bool":
            result[field] = _boolean(value, field)
        elif spec["type"] == "color":
            if not isinstance(value, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?", value):
                _fail(field, "must be #RRGGBB or #RRGGBBAA")
            result[field] = value
        else:
            result[field] = _number(value, field, spec)
    return result


def _boxes(value):
    value = _json_array(value, "fixed_bboxes")
    if value is None:
        return None
    if not value:
        _fail("fixed_bboxes", "must be nonempty when supplied")
    result = []
    for box in value:
        if not isinstance(box, list) or len(box) != 4:
            _fail("fixed_bboxes", "each region must contain four integer coordinates")
        coordinates = [_integer(coordinate, "fixed_bboxes", 0, 1000) for coordinate in box]
        if coordinates[0] >= coordinates[2] or coordinates[1] >= coordinates[3]:
            _fail("fixed_bboxes", "each region must have positive width and height")
        result.append(coordinates)
    return result


def _restoration(value, output_tos_path):
    selected, fields = _group(value, "restoration", "off", RESTORATION_MODES)
    _only(fields, RESTORATION_FIELDS, "restoration")
    if selected == "off":
        if any(item is not None for item in fields.values()):
            _fail("restoration", "disabled restoration cannot contain controls")
        return False, None
    result = {}
    for field, spec in RESTORATION_FIELDS.items():
        value = _default(fields, field, spec["default"])
        if field == "output_tos_path":
            path = _text(value, field)
            result[field] = _tos_output(path or output_tos_path, field)
        elif spec["type"] == "boxes":
            boxes = _boxes(value)
            if boxes is not None:
                result[field] = boxes
        elif field == "targets":
            if value is not None:
                targets = _codes(value, field, spec["options"])
                if targets:
                    result[field] = targets
        elif value is not None:
            if spec["type"] == "enum":
                result[field] = _enum(value, spec["options"], field)
            elif spec["type"] == "bool":
                result[field] = _boolean(value, field)
            else:
                result[field] = _integer(value, field, spec.get("min"))
                if field == "max_process_frames" and value != -1 and value <= 0:
                    _fail(field, "must be -1 or a positive frame count")
    if result["model_level"] == "lite" and result["detection_mode"] == "precise":
        _fail("model_level", "lite does not support precise detection")
    if result["detection_mode"] == "precise" and not result.get("targets") and not result.get("fixed_bboxes"):
        _fail("targets", "precise detection requires targets unless fixed_bboxes are supplied")
    return True, result


def _template(value):
    if isinstance(value, dict) and "id" in value:
        _only(value, ("id", "paras"), "template")
        selected = _enum(value["id"], GENJUTSU_TEMPLATES, "template")
        paras = value.get("paras")
    else:
        selected, fields = _group(value, "template", "basic", GENJUTSU_TEMPLATES)
        _only(fields, ("paras",), "template")
        paras = fields.get("paras")
    if paras is not None:
        _only(paras, (), "template.paras")
    return {"id": selected}


def _duration(value, model):
    if value is None:
        return None
    if isinstance(value, (str, dict)):
        selected, fields = _group(value, "duration", SERVER_DEFAULT, GENJUTSU_DURATION_MODES)
        _only(fields, ("seconds", "duration_seconds"), "duration")
        if len(fields) > 1:
            _fail("duration", "supply seconds or duration_seconds, not both")
        seconds = fields.get("duration_seconds", fields.get("seconds"))
        if selected != "explicit":
            if seconds is not None:
                _fail("duration", "seconds can only be supplied in explicit mode")
            return -1 if selected == "auto" else None
        value = seconds
    value = _integer(value, "duration", -1)
    if model in GENJUTSU_AUTO_DURATION_ONLY_MODELS and value != -1:
        _fail("duration", "only auto (-1) is documented for this model or server-default routing")
    return value


def build_genjutsu_request(video_url: str, template: dict | str, user_prompt: str, **options) -> dict:
    allowed = (
        "image_urls",
        "audio_urls",
        "allow_fallback",
        "model",
        "duration",
        "resolution",
        "ratio",
        "generate_audio",
        "watermark",
        "return_last_frame",
        "priority",
        "execution_expires_after",
        "callback_url",
        "safety_identifier",
        "use_asset_api",
        "asset_group_id",
        *GENJUTSU_GATED_FIELDS,
    )
    _only(options, allowed, "Genjutsu")
    for field in GENJUTSU_GATED_FIELDS:
        if options.get(field) is not None and options[field] != "":
            _fail(field, "is gated until the complete official schema or enum is verified")
    data = {
        "video_url": validate_las_reference(video_url, allow_asset=True, allow_bare_asset=True),
        "template": _template(template),
        "user_prompt": _text(user_prompt, "user_prompt", required=True),
    }
    for field in ("image_urls", "audio_urls"):
        references = parse_reference_list(options.get(field), field)
        if references:
            data[field] = references
    if data["template"]["id"] in GENJUTSU_IMAGE_REQUIRED_TEMPLATES and not data.get("image_urls"):
        _fail("image_urls", "this template requires at least one reference image")
    for field, default in (
        ("allow_fallback", False),
        ("generate_audio", True),
        ("watermark", False),
        ("return_last_frame", False),
        ("use_asset_api", False),
    ):
        data[field] = _boolean(_default(options, field, default), field)
    model = _enum(_default(options, "model", SERVER_DEFAULT), (SERVER_DEFAULT, *GENJUTSU_MODELS), "model")
    if model != SERVER_DEFAULT:
        data["model"] = model
    resolution = _default(options, "resolution", SERVER_DEFAULT)
    if resolution != SERVER_DEFAULT:
        data["resolution"] = _enum(resolution, GENJUTSU_MODEL_RESOLUTIONS[model], "resolution")
    duration = _duration(options.get("duration"), model)
    if duration is not None:
        data["duration"] = duration
    data["ratio"] = _enum(_default(options, "ratio", "adaptive"), GENJUTSU_RATIOS, "ratio")
    data["priority"] = _integer(_default(options, "priority", 0), "priority")
    data["execution_expires_after"] = _integer(
        _default(options, "execution_expires_after", 172800), "execution_expires_after", 3600, 259200
    )
    group = _text(options.get("asset_group_id"), "asset_group_id")
    if group:
        if not data["use_asset_api"]:
            _fail("asset_group_id", "requires use_asset_api=true")
        data["asset_group_id"] = group
    identifier = _text(options.get("safety_identifier"), "safety_identifier")
    if identifier:
        data["safety_identifier"] = identifier
    callback = _text(options.get("callback_url"), "callback_url")
    if callback:
        data["callback_url"] = validate_las_reference(callback, "callback_url", https_only=True)
    return {"operator_id": GENJUTSU_OPERATOR_ID, "operator_version": LAS_OPERATOR_VERSION, "data": data}


_TRANSLATE_FIELDS = (
    "audio_language",
    "output_languages",
    "dubbing_mode",
    "enable_fast_mode",
    "enable_dynamic_duration",
    "lip_translate",
    "lip_fuzzy_matching",
)
_EXTERNAL_FIELDS = ("audio_language", "dubbing_audio_url", "dubbing_language", "ignore_audio_video_duration_mismatch")
_COMMON_TRANSLATION_FIELDS = (
    "caption_formats",
    "primary_caption_format",
    "output_dubbing_audio",
    "burn_translated_subtitles",
    "subtitle_video_url",
    "restoration",
    "subtitle_style",
    "callback_url",
)


def _mode(value, options):
    selected, fields = _group(value, "mode", "translate", TRANSLATION_MODES)
    allowed = {
        "translate": _TRANSLATE_FIELDS,
        "lip_sync_external_audio": _EXTERNAL_FIELDS,
        "lip_sync_source_audio": ("audio_language",),
    }[selected]
    _only(fields, allowed, "mode")
    for field in dict.fromkeys((*_TRANSLATE_FIELDS, *_EXTERNAL_FIELDS)):
        if field not in options or options[field] is None:
            continue
        if field not in allowed:
            _fail(field, "does not belong to the selected mode")
        if field in fields and fields[field] is not None:
            _fail(field, "must be supplied only once, inside mode or as an option")
        fields[field] = options[field]
    return selected, fields


def _translate(fields, options, style_fields, custom_style):
    language = _enum(_default(fields, "audio_language", "en-US"), INPUT_LANGUAGES, "audio_language")
    fonts = _json_array(style_fields.get("subtitle_font_by_language"), "subtitle_font_by_language")
    targets = _codes(
        _default(fields, "output_languages", "es-MX"),
        "output_languages",
        OUTPUT_LANGUAGES,
        required=True,
        strict_positions=fonts is not None,
    )
    if language in targets:
        _fail("output_languages", "cannot include audio_language")
    formats = _codes(_default(options, "caption_formats", ".srt"), "caption_formats", CAPTION_FORMATS, required=True)
    primary = _enum(_default(options, "primary_caption_format", ".srt"), CAPTION_FORMATS, "primary_caption_format")
    if primary not in formats:
        _fail("primary_caption_format", "must be among caption_formats")
    data = {
        "audio_language": language,
        "output_languages": targets,
        "caption_formats": formats,
        "dubbing_mode": _enum(_default(fields, "dubbing_mode", "high_quality"), DUBBING_MODES, "dubbing_mode"),
    }
    for field in ("enable_fast_mode", "enable_dynamic_duration", "lip_translate", "lip_fuzzy_matching"):
        data[field] = _boolean(_default(fields, field, False), field)
    if data["enable_fast_mode"] and data["dubbing_mode"] != "high_quality":
        _fail("enable_fast_mode", "requires dubbing_mode=high_quality")
    if data["enable_dynamic_duration"] and data["lip_translate"]:
        _fail("enable_dynamic_duration", "cannot coexist with lip_translate=true")
    fuzzy = data.pop("lip_fuzzy_matching")
    if fuzzy and not data["lip_translate"]:
        _fail("lip_fuzzy_matching", "requires lip_translate=true")
    if data["lip_translate"]:
        data["lip_translate_param"] = {"lip_fuzzy_matching": fuzzy}
    if custom_style:
        data.update(_style(style_fields, targets))
    return data


def _specified_audio(selected, fields):
    data = {"lip_translate": True}
    if selected == "lip_sync_source_audio":
        data["audio_language"] = _enum(
            _text(fields.get("audio_language"), "audio_language", required=True), INPUT_LANGUAGES, "audio_language"
        )
        audio = {"source": "video"}
    else:
        audio = {
            "source": "url",
            "url": validate_las_reference(fields.get("dubbing_audio_url"), "dubbing_audio_url"),
            "ignore_audio_video_duration_mismatch": _boolean(
                _default(fields, "ignore_audio_video_duration_mismatch", False), "ignore_audio_video_duration_mismatch"
            ),
        }
        language = _text(fields.get("dubbing_language"), "dubbing_language")
        if language:
            audio["language"] = _enum(language, INPUT_LANGUAGES, "dubbing_language")
        fallback = _text(fields.get("audio_language"), "audio_language")
        if fallback:
            data["audio_language"] = _enum(fallback, INPUT_LANGUAGES, "audio_language")
    data["lip_translate_param"] = {"dubbing_audio": audio}
    return data


def build_translation_request(video_url: str, mode: dict | str, output_tos_path: str, **options) -> dict:
    _only(options, (*_COMMON_TRANSLATION_FIELDS, *_TRANSLATE_FIELDS, *_EXTERNAL_FIELDS), "Video Translation")
    selected, fields = _mode(mode, options)
    data = {
        "video_url": validate_las_reference(video_url),
        "output_tos_path": _tos_output(output_tos_path, "output_tos_path"),
    }
    style_selected, style_fields = _style_group(options.get("subtitle_style"))
    enabled, repair = _restoration(options.get("restoration"), data["output_tos_path"])
    burn = _boolean(_default(options, "burn_translated_subtitles", False), "burn_translated_subtitles")
    output_audio = _boolean(_default(options, "output_dubbing_audio", False), "output_dubbing_audio")
    picture = _text(options.get("subtitle_video_url"), "subtitle_video_url")
    if picture:
        picture = validate_las_reference(picture, "subtitle_video_url")
        if not burn:
            _fail("subtitle_video_url", "requires burn_translated_subtitles=true")
        if enabled:
            _fail("subtitle_video_url", "cannot coexist with restoration")
    if selected == "translate":
        data.update(_translate(fields, options, style_fields, style_selected == "custom"))
        data["output_dubbing_audio"] = output_audio
        data["burn_translated_subtitles"] = burn
        data["enable_video_inpaint"] = enabled
        if picture:
            data["subtitle_video_url"] = picture
        if repair is not None:
            data["video_inpaint_params"] = repair
    else:
        if enabled or picture or burn or output_audio or style_selected != "automatic":
            _fail(
                "mode",
                "specified-audio lip sync cannot use restoration, a picture source, captions, styling or clean TTS audio",
            )
        data.update(_specified_audio(selected, fields))
    envelope = {"operator_id": TRANSLATION_OPERATOR_ID, "operator_version": LAS_OPERATOR_VERSION, "data": data}
    callback = _text(options.get("callback_url"), "callback_url")
    if callback:
        envelope["callback"] = {"url": validate_las_reference(callback, "callback_url", https_only=True)}
    return envelope
