"""Small shared parameter contract for the API and dependency-free host agent."""

LIMITS = {"context": (512, 131072), "batch": (32, 4096), "ubatch": (32, 2048)}


def validate_settings(settings, max_context=131072, max_batch=4096, max_ubatch=2048):
    if not isinstance(settings, dict) or settings.keys() != LIMITS.keys():
        raise ValueError("Expected context, batch and microbatch")
    for (field, (minimum, _)), maximum in zip(LIMITS.items(), (max_context, max_batch, max_ubatch)):
        value = settings[field]
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f"{field} must be an integer between {minimum} and {maximum}")
    if not settings["ubatch"] <= settings["batch"] <= settings["context"]:
        raise ValueError("Microbatch must be at most batch, and batch at most context")
    return settings
