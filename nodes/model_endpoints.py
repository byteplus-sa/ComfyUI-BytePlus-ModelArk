"""Local endpoint routing; model capabilities always use the catalog identity."""

from . import credentials
from .models_config import (
    SEEDANCE2_CORE_MODEL_OPTIONS,
    SEEDREAM_5_MODEL_MAP,
    SEEDREAM_5_PRO_UI_MODEL,
    SEEDREAM_MODELS,
    VIDEO_MODEL_MAP,
)
from .nodes_shared import BytePlusException, get_text

# Checked live 2026-10-28 (Singapore): all three accept ep- IDs as request.model;
# successful image/video responses report the canonical dated model IDs below.
ENDPOINT_MODELS = {
    "seedance_2_0": VIDEO_MODEL_MAP["dreamina-seedance-2-0"],
    "seedance_2_5": VIDEO_MODEL_MAP["dreamina-seedance-2-5"],
    "seedream_5_0_pro": SEEDREAM_5_MODEL_MAP[SEEDREAM_5_PRO_UI_MODEL],
}


def _endpoint(name):
    variable = credentials.ENDPOINT_VARS[name]
    value = credentials.get_setting(variable)
    if value and not credentials.valid_endpoint_id(value):
        raise BytePlusException(get_text("err_endpoint_invalid", variable=variable))
    if value and any(
        credentials.get_setting(other) == value for other in credentials.ENDPOINT_VARS.values() if other != variable
    ):
        raise BytePlusException(get_text("err_endpoint_duplicate"))
    return value


def request_model_id(model_id):
    """Replace only an exact current catalog model with its configured endpoint."""
    for name, canonical in ENDPOINT_MODELS.items():
        if model_id == canonical:
            return _endpoint(name) or model_id
    return model_id


def canonical_model_id(model_id):
    """Recover a known endpoint's capability identity (including draft metadata)."""
    if str(model_id).startswith("ep-"):
        for name, canonical in ENDPOINT_MODELS.items():
            if credentials.get_setting(credentials.ENDPOINT_VARS[name]) == model_id:
                _endpoint(name)
                return canonical
    return model_id


class EndpointDefaultsRerun:
    """Invalidate ComfyUI output caching when the selected model's route changes."""

    @classmethod
    def fingerprint_inputs(cls, model=None, **kwargs):
        if isinstance(model, dict):
            model = model.get("model")
        if isinstance(model, str):
            key = SEEDANCE2_CORE_MODEL_OPTIONS.get(model, model)
            model_id = SEEDREAM_MODELS.get(key, VIDEO_MODEL_MAP.get(key, key))
            if model_id in ENDPOINT_MODELS.values():
                return (credentials.get_default_region(), request_model_id(model_id))
        if kwargs.get("draft_task_id") is not None:
            # The draft's model is only known at execution time.
            return (credentials.get_default_region(), _endpoint("seedance_2_5"))
        return 0
