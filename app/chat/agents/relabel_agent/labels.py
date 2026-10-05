"""Label validator sub-agent: is the label the reviewer gave one the model can actually output. The
authority is the inference service's own label set for that model, so a correction can only ever be
a class retraining could learn - never a free-text guess. Matching is case- and spacing-insensitive
("missing part" -> "MissingPart") and returns the model's own spelling."""

import re

from app.chat.agents.relabel_agent.errors import RelabelRefused
from app.shared import inference


def _key(label: str) -> str:
    return re.sub(r"[\s_\-]+", "", label).lower()


async def canonical_label(model: str, requested: str) -> str:
    """Fails closed: if the label set cannot be fetched, nothing is recorded rather than trusting
    an unchecked label."""

    try:
        models = await inference.list_models()
    except (inference.InferenceNotConfigured, inference.InferenceError) as exc:
        raise RelabelRefused(f"cannot check the label right now: {exc}") from exc

    served = next((m for m in models if m.name == model), None)
    if served is None:
        raise RelabelRefused(f"model {model!r} is not served, so its labels cannot be checked.")

    for label in served.labels:
        if _key(label) == _key(requested):
            return label
    raise RelabelRefused(
        f"{requested!r} is not a label {model} can output.", valid_labels=list(served.labels)
    )
