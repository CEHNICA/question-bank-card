"""The services 题有据 reads with, in one place.

Pure data and the standard library only: the desktop launcher and the
credential store (outside Django) import this module too.  Adding a service
means adding it here, then a credential row in the settings page.

Free tiers checked 2026-10-01 (docs/免费方案调研-2026-10.md):
- 魔搭 API-Inference gives a few hundred free calls a day (Qwen3.5 reads
  images as accurately as MiniMax), but guarantees one concurrent request.
- 智谱's free GLM-4.6V-Flash was tried and dropped: at midday it turned away
  three calls in four as overloaded, and its other free models misread.
- 硅基流动's only free vision model is an OCR model that ignores our prompt;
  MiniMax and 硅基流动 Qwen3-VL are paid.
"""

from __future__ import annotations

# Vision services.  ``engine`` is the stable key stored in model preferences
# (older keys keep their historical names).  ``credential`` / ``credentials``
# are the singular and pool fields in the encrypted credential file;
# ``environment`` the singular and JSON-pool environment variables the worker
# receives.  ``concurrency`` is (start, ceiling) per key (MiniMax follows the
# membership plan instead, see account_pool.MINIMAX_PLANS).
VISION = {
    "minimax": {
        "label": "MiniMax", "engine": "minimax_m3", "default_model": "MiniMax-M3",
        "suggested": ["MiniMax-M3"], "free_models": [],
        "credential": ("minimax_key", "minimax_keys"),
        "environment": ("MINIMAX_API_KEY", "MINIMAX_API_KEYS_JSON"),
        "concurrency": (6, 6),
        "signup": "https://platform.minimaxi.com",
        "note": "付费（Token Plan 会员）；读得快、读得准",
    },
    "modelscope": {
        "label": "魔搭", "engine": "modelscope_qwen", "default_model": "Qwen/Qwen3.5-35B-A3B",
        "suggested": ["Qwen/Qwen3.5-35B-A3B", "Qwen/Qwen3.5-122B-A10B", "Qwen/Qwen3.5-397B-A17B",
                      "Qwen/Qwen3.5-27B"],
        "free_models": ["Qwen/Qwen3.5-35B-A3B", "Qwen/Qwen3.5-122B-A10B", "Qwen/Qwen3.5-397B-A17B",
                        "Qwen/Qwen3.5-27B"],
        "credential": ("modelscope_key", "modelscope_keys"),
        "environment": ("MODELSCOPE_API_KEY", "MODELSCOPE_API_KEYS_JSON"),
        "concurrency": (1, 2),
        "signup": "https://www.modelscope.cn/my/myaccesstoken",
        "note": "每天免费几百次（要绑定实名的阿里云账号）",
    },
    "siliconflow": {
        "label": "硅基流动", "engine": "siliconflow_qwen3", "default_model": "Qwen/Qwen3-VL-32B-Instruct",
        "suggested": ["Qwen/Qwen3-VL-30B-A3B-Instruct", "Qwen/Qwen3-VL-30B-A3B-Thinking",
                      "Qwen/Qwen3-VL-32B-Instruct"],
        "free_models": [],
        "credential": ("siliconflow_key", "siliconflow_keys"),
        "environment": ("SILICONFLOW_API_KEY", "SILICONFLOW_API_KEYS_JSON"),
        "concurrency": (2, 2),
        "signup": "https://cloud.siliconflow.cn/account/ak",
        "note": "按量付费（Qwen3-VL），可做复核和备用",
    },
}

# When the chosen reader has no key, the first service here that has one
# reads instead.  MiniMax stays first so paying users keep their reader; 魔搭
# is the free one (measured 2026-10-01: as accurate as MiniMax, ~3.5 s a call).
PRIMARY_ORDER = ("minimax", "modelscope", "siliconflow")
# The second, independent reader prefers another vendor in this order.
CHECKER_ORDER = ("modelscope", "siliconflow", "minimax")

# Not a vision service: the cards start as MinerU's own text and the user's AI
# assistant (豆包 …) checks them against the crop through tiyouju.
ASSISTANT_ENGINE = "assistant"

ENGINES = {spec["engine"]: key for key, spec in VISION.items()}
MINERU = {"credential": ("mineru_token", "mineru_tokens"), "environment": ("MINERU_TOKEN", "MINERU_TOKENS_JSON"),
          "signup": "https://mineru.net/apiManage/token", "note": "免费，每天 1000 页；Token 14 天过期一次"}

# Every credential service, MinerU first.
SERVICES = ("mineru", *VISION)


def credential_fields() -> dict[str, tuple[str, str]]:
    return {"mineru": MINERU["credential"], **{key: spec["credential"] for key, spec in VISION.items()}}


def environment_names() -> dict[str, tuple[str, str]]:
    """service -> (pool JSON variable, singular variable), as account_pool reads them."""
    fields = {"mineru": MINERU["environment"], **{key: spec["environment"] for key, spec in VISION.items()}}
    return {service: (pool, single) for service, (single, pool) in fields.items()}


def model_environment(provider: str) -> str:
    return f"QB_{provider.upper()}_MODEL"
