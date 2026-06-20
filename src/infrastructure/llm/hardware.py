"""Donanım profili algılama (design/09/19) — doctor için. torch lazy/opsiyonel."""

from __future__ import annotations


def detect_profile() -> tuple[str, str]:
    """(profile, detay) → auto seçim: gpu24|gpu48|multi_gpu|cpu (cloud config kararı ayrı)."""
    try:
        import torch  # type: ignore
    except ImportError:
        return "cpu", "torch yok → CPU/cloud (embedding lokal istenirse pip install '.[local]')"

    if not torch.cuda.is_available():
        return "cpu", "CUDA yok → CPU"

    count = torch.cuda.device_count()
    total_gb = 0.0
    names = []
    for i in range(count):
        props = torch.cuda.get_device_properties(i)
        total_gb += props.total_memory / (1024**3)
        names.append(props.name)
    detail = f"{count} GPU, ~{total_gb:.0f} GB toplam ({', '.join(names)})"

    if count >= 2:
        return "multi_gpu", detail
    if total_gb >= 44:
        return "gpu48", detail
    if total_gb >= 22:
        return "gpu24", detail
    return "cpu", detail + " (<24 GB → 7B fallback önerilir)"
