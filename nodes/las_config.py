"""LAS capabilities for PLAN_LAS_COMFYUI_INTEGRATION.md, researched 2026-11-10."""

TRANSLATION_SOURCE_URL = "https://docs.byteplus.com/docs/en/Byteplus_LAS/Video_Translation_operator.md"
INPAINT_SOURCE_URL = "https://docs.byteplus.com/docs/en/Byteplus_LAS/Video_Inpaint-Pro.md"
GENJUTSU_SOURCE_URL = "https://docs.byteplus.com/en/docs/Byteplus_LAS/Enhanced_video_editing_2"
LAS_OPERATOR_VERSION = "v1"
GENJUTSU_OPERATOR_ID = "las_video_edit_enhance"
TRANSLATION_OPERATOR_ID = "las_video_translate"
SERVER_DEFAULT = "server_default"

GENJUTSU_MODEL_MAP = {
    "Seedance 2.0 Mini": "dreamina-seedance-2-0-mini-260615",
    "Seedance 2.0 Fast": "dreamina-seedance-2-0-fast-260128",
    "Seedance 2.0": "dreamina-seedance-2-0-260128",
    "Seedance 2.5": "dreamina-seedance-2-5-260628",
}
GENJUTSU_MODELS = tuple(GENJUTSU_MODEL_MAP.values())
GENJUTSU_MODEL_RESOLUTIONS = {
    SERVER_DEFAULT: ("480p", "720p"),
    GENJUTSU_MODELS[0]: ("480p", "720p"),
    GENJUTSU_MODELS[1]: ("480p", "720p"),
    GENJUTSU_MODELS[2]: ("480p", "720p", "1080p"),
    GENJUTSU_MODELS[3]: ("480p", "720p"),
}
GENJUTSU_AUTO_DURATION_ONLY_MODELS = frozenset((SERVER_DEFAULT, GENJUTSU_MODELS[3]))
GENJUTSU_TEMPLATES = ("basic", "general", "replace/person_replace", "replace/subject_replace", "style_transfer/general")
GENJUTSU_IMAGE_REQUIRED_TEMPLATES = frozenset(("general", "replace/person_replace", "replace/subject_replace"))
GENJUTSU_RATIOS = ("16:9", "4:3", "1:1", "3:4", "9:16", "21:9", "adaptive")
GENJUTSU_DURATION_MODES = (SERVER_DEFAULT, "auto", "explicit")
GENJUTSU_GATED_FIELDS = frozenset(
    ("style_template", "moderation_options", "moderation_options_json", "output_format", "omni_reference_task_type")
)
GENJUTSU_DOCUMENTED_STYLE_EXAMPLES = ("2d_japanese",)

TRANSLATION_MODES = ("translate", "lip_sync_external_audio", "lip_sync_source_audio")
DUBBING_MODES = ("emotion", "timbre", "high_quality")
CAPTION_FORMATS = (".ass", ".txt", ".srt", ".xml", ".vtt")
SUBTITLE_STYLE_MODES = ("automatic", "custom")
RESTORATION_MODES = ("off", "on")

LANGUAGE_NAMES = {
    "ar-SA": "Arabic",
    "bn-BD": "Bengali",
    "da-DK": "Danish",
    "de-DE": "German",
    "el-GR": "Greek",
    "en-US": "English",
    "es-MX": "Spanish",
    "fi-FI": "Finnish",
    "fil-PH": "Filipino",
    "fr-FR": "French",
    "he-IL": "Hebrew",
    "hi-IN": "Hindi",
    "id-ID": "Indonesian",
    "it-IT": "Italian",
    "ja-JP": "Japanese",
    "km-KH": "Khmer",
    "ko-KR": "Korean",
    "lo-LA": "Lao",
    "ms-MY": "Malay",
    "my-MM": "Burmese",
    "ne-NP": "Nepali",
    "nl-NL": "Dutch",
    "no-NO": "Norwegian",
    "pl-PL": "Polish",
    "pt-BR": "Portuguese",
    "ro-RO": "Romanian",
    "ru-RU": "Russian",
    "sv-SE": "Swedish",
    "sw-KE": "Swahili",
    "th-TH": "Thai",
    "tr-TR": "Turkish",
    "uk-UA": "Ukrainian",
    "vi-VN": "Vietnamese",
    "yue-CN": "Cantonese",
    "zh-CN": "Chinese (Simplified)",
}
INPUT_LANGUAGES = (
    "ar-SA",
    "bn-BD",
    "de-DE",
    "el-GR",
    "en-US",
    "es-MX",
    "fil-PH",
    "fr-FR",
    "id-ID",
    "it-IT",
    "ja-JP",
    "ko-KR",
    "ms-MY",
    "ne-NP",
    "nl-NL",
    "pl-PL",
    "pt-BR",
    "ro-RO",
    "ru-RU",
    "th-TH",
    "tr-TR",
    "uk-UA",
    "vi-VN",
    "yue-CN",
    "zh-CN",
)
_LATIN_FONTS = ("NotoSans-Regular", "FiraGO-Regular")
OUTPUT_LANGUAGE_FONTS = {
    "ar-SA": ("NotoSansArabic-Regular", "Cairo-Regular", "FiraGO-Regular"),
    "da-DK": _LATIN_FONTS,
    "de-DE": _LATIN_FONTS,
    "el-GR": _LATIN_FONTS,
    "en-US": (*_LATIN_FONTS, "AtkinsonHyperlegible-Regular"),
    "es-MX": _LATIN_FONTS,
    "fi-FI": _LATIN_FONTS,
    "fil-PH": _LATIN_FONTS,
    "fr-FR": _LATIN_FONTS,
    "he-IL": ("NotoSansHebrew-Regular", "Heebo-Regular", "FiraGO-Regular"),
    "hi-IN": ("NotoSansDevanagari-Regular", "AnekDevanagari[wdth,wght]", "FiraGO-Regular"),
    "id-ID": _LATIN_FONTS,
    "it-IT": _LATIN_FONTS,
    "ja-JP": ("NotoSansJP-Regular", "MPLUS1-Regular"),
    "km-KH": ("NotoSansKhmer-Regular", "KantumruyPro[wght]"),
    "ko-KR": ("NotoSansKR-Regular", "Pretendard-Regular"),
    "lo-LA": ("NotoSansLao-Regular", "BoonBaan-Regular"),
    "ms-MY": _LATIN_FONTS,
    "my-MM": ("NotoSansMyanmar-Regular",),
    "nl-NL": _LATIN_FONTS,
    "no-NO": _LATIN_FONTS,
    "pl-PL": _LATIN_FONTS,
    "pt-BR": _LATIN_FONTS,
    "ru-RU": _LATIN_FONTS,
    "sv-SE": _LATIN_FONTS,
    "sw-KE": _LATIN_FONTS,
    "th-TH": ("NotoSansThai-Regular", "Sarabun-Regular", "BoonBaan-Regular", "FiraGO-Regular"),
    "tr-TR": _LATIN_FONTS,
    "vi-VN": ("NotoSans-Regular", "BeVietnamPro-Regular", "FiraGO-Regular"),
    "yue-CN": ("NotoSansHK-Regular", "NotoSansTC-Regular", "ChironHeiHK-Text-R"),
    "zh-CN": ("NotoSansSC-Regular",),
}
OUTPUT_LANGUAGES = tuple(OUTPUT_LANGUAGE_FONTS)

STYLE_FIELDS = {
    "subtitle_font_by_language": {"type": "fonts", "default": None},
    "subtitle_font_size": {"type": "int", "default": 40, "min": 8, "max": 128},
    "subtitle_font_color": {"type": "color", "default": "#FFFFFF"},
    "subtitle_bold": {"type": "bool", "default": False},
    "subtitle_outline_color": {"type": "color", "default": "#000000"},
    "subtitle_outline_width": {"type": "float", "default": 0.0, "min": 0, "max": 16},
    "subtitle_background_color": {"type": "color", "default": "#00000000"},
    "subtitle_background_padding_x": {"type": "float", "default": 0.0, "min": 0, "max": 128},
    "subtitle_background_padding_y": {"type": "float", "default": 0.0, "min": 0, "max": 128},
    "subtitle_shadow_color": {"type": "color", "default": "#00000000"},
    "subtitle_opacity": {"type": "float", "default": 1.0, "min": 0, "max": 1},
    "subtitle_shadow_offset_x": {"type": "float", "default": 0.0, "min": -32, "max": 32},
    "subtitle_shadow_offset_y": {"type": "float", "default": 0.0, "min": -32, "max": 32},
    "subtitle_shadow_blur": {"type": "float", "default": 0.0, "min": 0, "max": 10},
    "subtitle_line_spacing": {"type": "float", "default": 0.0, "min": 0, "max": 64},
    "subtitle_width_ratio": {"type": "float", "default": 0.88, "min": 0.1, "max": 1},
    "subtitle_top_margin_ratio": {"type": "float", "default": 0.77, "min": 0, "max": 1, "max_exclusive": True},
}
INPAINT_MODEL_LEVELS = ("lite", "pro")
INPAINT_DETECTION_MODES = ("standard_subtitle", "standard_watermark", "standard_subtitle_watermark", "precise")
INPAINT_TARGETS = ("watermark", "subtitle", "scrolling_subtitle", "drama_info")
INPAINT_RESOLUTIONS = ("720p", "1080p", "4k")
INPAINT_OUTPUT_VIDEO_MODES = ("economy", "balanced", "high", "source")
INPAINT_CODECS = ("h264", "h265")
INPAINT_BITRATE_UNIT = "bps"
INPAINT_BITRATE_UI_ENABLED = False
RESTORATION_FIELDS = {
    "output_tos_path": {"type": "tos", "default": ""},
    "model_level": {"type": "enum", "default": "pro", "options": INPAINT_MODEL_LEVELS},
    "detection_mode": {"type": "enum", "default": "standard_subtitle", "options": INPAINT_DETECTION_MODES},
    "targets": {"type": "list", "default": None, "options": INPAINT_TARGETS},
    "fixed_bboxes": {"type": "boxes", "default": None},
    "return_subtitle_bbox": {"type": "bool", "default": True},
    "max_process_frames": {"type": "int", "default": -1},
    "maximum_output_fps": {"type": "int", "default": 30, "min": 1},
    "maximum_resolution": {"type": "enum", "default": "1080p", "options": INPAINT_RESOLUTIONS},
    "output_video_mode": {"type": "enum", "default": "balanced", "options": INPAINT_OUTPUT_VIDEO_MODES},
    "output_codec": {"type": "enum", "default": None, "options": INPAINT_CODECS},
    "output_bitrate": {"type": "int", "default": None, "min": 1, "unit": INPAINT_BITRATE_UNIT, "ui_enabled": False},
    "subtitle_persistent": {"type": "bool", "default": False},
}
