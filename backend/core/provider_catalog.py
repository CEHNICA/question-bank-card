"""题有据全平台唯一的服务商与模型目录（纯数据与标准库，不依赖 Django）。

桌面启动器、加密凭据存储（``credential_store.py``）、读题模型路由（``preferences.py`` /
``readers.py``）、答题补充服务（``library_ai_settings.py``）以及前端统一配置页均从这里读取
服务商定义，消除多处重复硬编码。
"""

from __future__ import annotations

import re

ARK_BASE = "https://ark.cn-beijing.volces.com/api/v3"
MINIMAX_API_BASE = "https://api.minimax.cn/v1"
MINIMAX_ANSWER_MODEL = "MiniMax-M3.1-Flash-Preview"
MODELSCOPE_BASE = "https://api-inference.modelscope.cn/v1"
NVIDIA_BASE = "https://integrate.api.nvidia.com/v1"
NVIDIA_ANSWER_MODEL = "nvidia/nemotron-3.5-super-vl-120b-a12b"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
GEMINI_DEFAULT_MODEL = "gemini-2.5-flash"
ZHIPU_BASE = "https://open.bigmodel.cn/api/paas/v4"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
SILICONFLOW_BASE = "https://api.siliconflow.cn/v1"
MINIMAX_TEXT_MODELS = {
    "MiniMax-M2",
    *(f"MiniMax-M2.{version}{speed}" for version in (1, 5, 7) for speed in ("", "-highspeed")),
}

# 视觉读题服务定义（保留原有 engine 键以兼容既有的模型偏好文件）。
VISION = {
    "minimax": {
        "label": "MiniMax",
        "engine": "minimax_m3",
        "default_model": "MiniMax-M3",
        "suggested": ["MiniMax-M3"],
        "free_models": [],
        "credential": ("minimax_key", "minimax_keys"),
        "environment": ("MINIMAX_API_KEY", "MINIMAX_API_KEYS_JSON"),
        "concurrency": (6, 6),
        "signup": "https://platform.minimaxi.com",
        "note": "付费（Token Plan 会员）；读得快、读得准",
    },
    "modelscope": {
        "label": "魔搭",
        "engine": "modelscope_qwen",
        "default_model": "Qwen/Qwen3.5-35B-A3B",
        "suggested": ["Qwen/Qwen3.5-35B-A3B", "Qwen/Qwen3.5-122B-A10B", "Qwen/Qwen3.5-397B-A17B", "Qwen/Qwen3.5-27B"],
        "free_models": ["Qwen/Qwen3.5-35B-A3B", "Qwen/Qwen3.5-122B-A10B", "Qwen/Qwen3.5-397B-A17B", "Qwen/Qwen3.5-27B"],
        "credential": ("modelscope_key", "modelscope_keys"),
        "environment": ("MODELSCOPE_API_KEY", "MODELSCOPE_API_KEYS_JSON"),
        "concurrency": (1, 2),
        "signup": "https://www.modelscope.cn/my/myaccesstoken",
        "note": "每天免费几百次（需完成邮箱验证并绑定实名阿里云账号）",
    },
    "siliconflow": {
        "label": "硅基流动",
        "engine": "siliconflow_qwen3",
        "default_model": "Qwen/Qwen3-VL-32B-Instruct",
        "suggested": ["Qwen/Qwen3-VL-30B-A3B-Instruct", "Qwen/Qwen3-VL-30B-A3B-Thinking", "Qwen/Qwen3-VL-32B-Instruct"],
        "free_models": [],
        "credential": ("siliconflow_key", "siliconflow_keys"),
        "environment": ("SILICONFLOW_API_KEY", "SILICONFLOW_API_KEYS_JSON"),
        "concurrency": (2, 2),
        "signup": "https://cloud.siliconflow.cn/account/ak",
        "note": "按量付费（Qwen3-VL），可做复核和备用",
    },
    "nvidia": {
        "label": "英伟达 NIM",
        "engine": "nvidia_nemotron",
        "default_model": NVIDIA_ANSWER_MODEL,
        "suggested": ["nvidia/nemotron-3.5-super-vl-120b-a12b", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"],
        "free_models": ["nvidia/nemotron-3.5-super-vl-120b-a12b", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"],
        "credential": ("nvidia_key", "nvidia_keys"),
        "environment": ("NVIDIA_API_KEY", "NVIDIA_API_KEYS_JSON"),
        "concurrency": (1, 2),
        "signup": "https://build.nvidia.com",
        "note": "免费版有并发/速率限制（自动从 1 并发起步）；支持多模态图文读题与解题",
    },
    "gemini": {
        "label": "Google Gemini",
        "engine": "gemini_flash",
        "default_model": GEMINI_DEFAULT_MODEL,
        "suggested": ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro"],
        "free_models": ["gemini-2.5-flash", "gemini-2.5-flash-lite"],
        "credential": ("gemini_key", "gemini_keys"),
        "environment": ("GEMINI_API_KEY", "GEMINI_API_KEYS_JSON"),
        "concurrency": (1, 2),
        "signup": "https://aistudio.google.com/apikey",
        "note": "提供长期免费层，数学推理与看图识题均强（需网络可访问 Google API）",
    },
    "zhipu": {
        "label": "智谱 BigModel",
        "engine": "zhipu_glm",
        "default_model": "glm-4.6v-flash",
        "suggested": ["glm-4.6v-flash", "glm-4.1v-thinking-flash"],
        "free_models": ["glm-4.6v-flash", "glm-4.1v-thinking-flash"],
        "credential": ("zhipu_key", "zhipu_keys"),
        "environment": ("ZHIPU_API_KEY", "ZHIPU_API_KEYS_JSON"),
        "concurrency": (1, 2),
        "signup": "https://open.bigmodel.cn/usercenter/apikeys",
        "note": "免费不限总量；高峰期偶发 1305 拥堵，建议配合「跨服务自动路由」作免费兜底",
    },
}

# 主读未配置密钥时的兜底优先级（MiniMax 优先保证付费用户默认体验，魔搭为免费主力）。
PRIMARY_ORDER = ("minimax", "modelscope", "siliconflow", "nvidia", "gemini", "zhipu")
CHECKER_ORDER = ("modelscope", "siliconflow", "nvidia", "gemini", "zhipu", "minimax")
ASSISTANT_ENGINE = "assistant"
ENGINES = {spec["engine"]: key for key, spec in VISION.items()}

MINERU = {
    "label": "MinerU",
    "credential": ("mineru_token", "mineru_tokens"),
    "environment": ("MINERU_TOKEN", "MINERU_TOKENS_JSON"),
    "signup": "https://mineru.net/apiManage/token",
    "note": "每天 1000 页最高优先级解析额度，超出部分降低优先级；额度和 Token 有效期以 API 管理页为准",
}

# 传统“切题 + 读题”4 家服务（保留该精确元组以兼容历史凭据接口与环境变量契约）。
SERVICES = ("mineru", "minimax", "modelscope", "siliconflow")

# 仅用于正式题库补充标签/参考答案的服务商。
ANSWER_ONLY_SERVICES = {
    "deepseek": {
        "label": "DeepSeek",
        "credential": ("deepseek_key", "deepseek_keys"),
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-v4-pro",
        "supports_images": False,
        "thinking": True,
        "signup": "https://platform.deepseek.com/api_keys",
        "note": "独立答题服务（纯文本推理；含配图题请选用支持图文的模型）",
    },
    "openrouter": {
        "label": "OpenRouter 免费池",
        "credential": ("openrouter_key", "openrouter_keys"),
        "base_url": OPENROUTER_BASE,
        "default_model": "openrouter/free",
        "supports_images": True,
        "thinking": False,
        "signup": "https://openrouter.ai/keys",
        "note": "openrouter/free 自动路由免费模型池；免费账号 50 次/天，历史充值 ≥$10 解锁 1000 次/天",
    },
    "doubao": {
        "label": "豆包",
        "credential": ("doubao_key", "doubao_keys"),
        "base_url": ARK_BASE,
        "default_model": "",
        "supports_images": False,
        "thinking": True,
        "signup": "https://console.volcengine.com/ark",
        "note": "火山方舟 Endpoint ID（以 ep- 开头）",
    },
    "custom": {
        "label": "其他兼容服务",
        "credential": ("custom_key", "custom_keys"),
        "base_url": "",
        "default_model": "",
        "supports_images": False,
        "thinking": True,
        "signup": "",
        "note": "填写兼容 OpenAI Chat Completions 协议的地址与模型名",
    },
}

# 答题与标签 API 各服务商默认配置（与视觉读题共用同一服务商目录，不各自维护两份）。
ANSWER_DEFAULTS = {
    "deepseek": {
        "base_url": ANSWER_ONLY_SERVICES["deepseek"]["base_url"],
        "model": ANSWER_ONLY_SERVICES["deepseek"]["default_model"],
        "supports_images": False,
        "thinking": True,
    },
    "nvidia": {"base_url": NVIDIA_BASE, "model": NVIDIA_ANSWER_MODEL, "supports_images": True, "thinking": True},
    "gemini": {"base_url": GEMINI_BASE, "model": GEMINI_DEFAULT_MODEL, "supports_images": True, "thinking": True},
    "zhipu": {"base_url": ZHIPU_BASE, "model": "glm-4.5-flash", "supports_images": False, "thinking": True},
    "openrouter": {"base_url": OPENROUTER_BASE, "model": "openrouter/free", "supports_images": True, "thinking": False},
    "doubao": {"base_url": ARK_BASE, "model": "", "supports_images": False, "thinking": True},
    "minimax": {"base_url": MINIMAX_API_BASE, "model": MINIMAX_ANSWER_MODEL, "supports_images": True, "thinking": True},
    "custom": {"base_url": "", "model": "", "supports_images": False, "thinking": True},
    "modelscope": {
        "base_url": MODELSCOPE_BASE,
        "model": VISION["modelscope"]["default_model"],
        "supports_images": True,
        "thinking": True,
    },
    "siliconflow": {
        "base_url": SILICONFLOW_BASE,
        "model": VISION["siliconflow"]["default_model"],
        "supports_images": True,
        "thinking": False,
    },
}

ANSWER_ORDER = (
    "deepseek",
    "minimax",
    "nvidia",
    "zhipu",
    "openrouter",
    "gemini",
    "doubao",
    "custom",
    "modelscope",
    "siliconflow",
)
VISION_ORDER = ("modelscope", "minimax", "siliconflow", "nvidia", "gemini", "zhipu")
ALL_SERVICES = ("mineru", *VISION_ORDER, "deepseek", "openrouter", "doubao", "custom")
PROVIDER_NAMES = {
    "mineru": MINERU["label"],
    **{key: spec["label"] for key, spec in VISION.items()},
    **{key: spec["label"] for key, spec in ANSWER_ONLY_SERVICES.items()},
}

# 模型路由与 ID 校验的唯一纯 Python 定义，供 credential_store 与 preferences 共用。
DEFAULT_MODEL_ROLES = {"primary_engine": "minimax_m3", "checker_engine": "auto", "arbiter_engine": "primary"}
MODEL_ROLE_CHOICES = {
    "primary_engine": frozenset((*ENGINES, ASSISTANT_ENGINE)),
    "checker_engine": frozenset(("auto", *ENGINES)),
    "arbiter_engine": frozenset(("primary", "checker", *ENGINES)),
}
DEFAULT_VISION_MODELS = {key: spec["default_model"] for key, spec in VISION.items()}
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+\-]{0,159}")


def is_valid_model_id(value: object) -> bool:
    """校验模型 ID（允许 1–160 位安全字符，禁止空格、网址或斜杠开头）。"""
    return isinstance(value, str) and MODEL_ID.fullmatch(value) is not None and "://" not in value


def credential_fields() -> dict[str, tuple[str, str]]:
    """仅返回切题 + 看图读题 4 家服务的凭据字段映射（保持既有契约）。"""
    return {"mineru": MINERU["credential"], **{key: spec["credential"] for key, spec in VISION.items()}}


def all_credential_fields() -> dict[str, tuple[str, str]]:
    """返回全平台 7 家服务的统一凭据字段映射（同一家在统一加密文件中只存一份池）。"""
    return {**credential_fields(), **{key: spec["credential"] for key, spec in ANSWER_ONLY_SERVICES.items()}}


def environment_names() -> dict[str, tuple[str, str]]:
    """service -> (账号池 JSON 环境变量, 单账号旧环境变量)，供 worker 读取。"""
    fields = {"mineru": MINERU["environment"], **{key: spec["environment"] for key, spec in VISION.items()}}
    return {service: (pool, single) for service, (single, pool) in fields.items()}


def model_environment(provider: str) -> str:
    return f"QB_{provider.upper()}_MODEL"


def catalog_payload() -> dict[str, object]:
    """下发给统一前端 API 配置界面的完整服务商目录。"""
    services = []
    services.append(
        {
            "key": "mineru",
            "label": MINERU["label"],
            "roles": ["cutting"],
            "recommended": False,
            "signup": MINERU["signup"],
            "note": MINERU["note"],
            "input_label": "新的 Token（留空就不改）",
            "placeholder": "粘贴 MinerU 官网里复制的 Token",
            "supports_test": False,
        }
    )
    for key in VISION_ORDER:
        spec = VISION[key]
        ans = ANSWER_DEFAULTS[key]
        services.append(
            {
                "key": key,
                "label": spec["label"],
                "engine": spec["engine"],
                "roles": ["reading", "answering"],
                "recommended": key == "modelscope",
                "free": bool(spec["free_models"]),
                "signup": spec["signup"],
                "note": spec["note"],
                "input_label": "新的访问令牌（留空就不改）" if key == "modelscope" else "新的 API Key（留空就不改）",
                "placeholder": "粘贴魔搭访问令牌（多账号用 ; 隔开）"
                if key == "modelscope"
                else "粘贴英伟达 NIM API Key（nvapi- 开头，多账号用 ; 隔开）"
                if key == "nvidia"
                else "粘贴 Google AI Studio API Key（AIza 开头，多账号用 ; 隔开）"
                if key == "gemini"
                else f"粘贴 {spec['label']} 的 API Key（多账号用 ; 隔开）",
                "reading_default_model": spec["default_model"],
                "reading_suggested_models": list(spec["suggested"]),
                "answer_defaults": dict(ans),
                "supports_test": True,
            }
        )
    for key in ("deepseek", "openrouter", "doubao", "custom"):
        spec = ANSWER_ONLY_SERVICES[key]
        services.append(
            {
                "key": key,
                "label": spec["label"],
                "roles": ["answering"],
                "recommended": False,
                "free": key == "openrouter",
                "signup": spec["signup"],
                "note": spec["note"],
                "input_label": "新的 API Key（留空就不改）",
                "placeholder": f"粘贴 {spec['label']} 的 API Key（多账号用 ; 隔开）",
                "answer_defaults": dict(ANSWER_DEFAULTS[key]),
                "supports_test": True,
            }
        )
    return {
        "services": services,
        "order": list(ALL_SERVICES),
        "vision_order": list(VISION_ORDER),
        "answer_order": list(ANSWER_ORDER),
    }
