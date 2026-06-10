"""在 OASIS/CAMEL 模拟中使用 CLI 后端 LLM 的辅助模块。

provider=claude-cli / codex-cli 时，返回一个把请求代理到本机 CLI 的
``CLIModel``（伪造成 OpenAI ChatCompletion 喂给模拟引擎）。

provider=openai 时，回退到原有的 ``ModelFactory`` OpenAI 路径，并保留
双 LLM（boost）加速配置。
"""

import asyncio
import json
import math
import os
import time
import uuid
from typing import Any, Dict, List

from camel.models.openai_model import OpenAIModel
from openai.types.chat.chat_completion import ChatCompletion

from ..config import Config
from .llm_client import LLMClient, CLI_PROVIDERS
from .logger import get_logger

logger = get_logger('mirofish.oasis_llm')

# CLI 提供方(claude-cli/codex-cli)的并发上限。从 3 提到 8 以显著缩短每轮墙钟时间
# （每个 CLI 调用会 spawn 子进程，8 是吞吐与系统负载的稳妥平衡）。可用 OASIS_CLI_SEMAPHORE 覆盖。
DEFAULT_CLI_SEMAPHORE = 8
DEFAULT_OPENAI_SEMAPHORE = 30


class CLIModel(OpenAIModel):
    """把请求代理到 Claude/Codex CLI 的 CAMEL 模型后端。"""

    def __init__(
        self,
        model_type: str,
        provider: str,
        model_config_dict: Dict[str, Any] | None = None,
        api_key: str | None = None,
        url: str | None = None,
        timeout: float | None = None,
        max_retries: int = 3,
    ) -> None:
        self.provider = (provider or '').lower()
        self._llm = LLMClient(provider=self.provider)
        super().__init__(
            model_type=model_type,
            model_config_dict=model_config_dict,
            api_key=api_key or 'cli-bridge',
            url=url,
            timeout=timeout,
            max_retries=max_retries,
        )

    def _estimate_tokens(self, value: Any) -> int:
        if value is None:
            return 0
        if isinstance(value, str):
            return max(1, math.ceil(len(value) / 4)) if value else 0
        if isinstance(value, list):
            return sum(self._estimate_tokens(item) for item in value)
        if isinstance(value, dict):
            return self._estimate_tokens(json.dumps(value, ensure_ascii=False))
        return self._estimate_tokens(str(value))

    def _build_completion(self, messages: List[Dict[str, Any]], content: str) -> ChatCompletion:
        prompt_tokens = sum(self._estimate_tokens(message.get('content')) for message in messages)
        completion_tokens = self._estimate_tokens(content)

        return ChatCompletion.model_validate(
            {
                'id': f'chatcmpl-cli-{uuid.uuid4().hex[:24]}',
                'object': 'chat.completion',
                'created': int(time.time()),
                'model': self.provider,
                'choices': [
                    {
                        'index': 0,
                        'message': {
                            'role': 'assistant',
                            'content': content,
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {
                    'prompt_tokens': prompt_tokens,
                    'completion_tokens': completion_tokens,
                    'total_tokens': prompt_tokens + completion_tokens,
                },
            }
        )

    def _request_chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] | None = None,
    ) -> ChatCompletion:
        if tools:
            logger.warning('CLIModel 忽略 tool schema；OASIS CLI 模式不支持工具调用')

        temperature = float((self.model_config_dict or {}).get('temperature', 0.7) or 0.7)
        max_tokens = int((self.model_config_dict or {}).get('max_tokens', 4096) or 4096)
        content = self._llm.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return self._build_completion(messages, content)

    async def _arequest_chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] | None = None,
    ) -> ChatCompletion:
        return await asyncio.to_thread(self._request_chat_completion, messages, tools)

    def _request_parse(
        self,
        messages: List[Dict[str, Any]],
        response_format,
        tools: List[Dict[str, Any]] | None = None,
    ) -> ChatCompletion:
        if tools:
            logger.warning('CLIModel 在结构化输出请求中忽略 tool schema')

        temperature = float((self.model_config_dict or {}).get('temperature', 0.3) or 0.3)
        max_tokens = int((self.model_config_dict or {}).get('max_tokens', 4096) or 4096)
        payload = self._llm.chat_json(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return self._build_completion(messages, json.dumps(payload, ensure_ascii=False))

    async def _arequest_parse(
        self,
        messages: List[Dict[str, Any]],
        response_format,
        tools: List[Dict[str, Any]] | None = None,
    ) -> ChatCompletion:
        return await asyncio.to_thread(self._request_parse, messages, response_format, tools)


def _resolve_provider(config: Dict[str, Any]) -> str:
    return (
        os.environ.get('LLM_PROVIDER')
        or config.get('llm_provider')
        or Config.LLM_PROVIDER
        or 'claude-cli'
    ).lower()


def _create_openai_model(config: Dict[str, Any], use_boost: bool = False):
    """provider=openai 时的原有 ModelFactory 路径（保留 boost 加速配置）。"""
    from camel.models import ModelFactory
    from camel.types import ModelPlatformType

    boost_api_key = os.environ.get("LLM_BOOST_API_KEY", "")
    boost_base_url = os.environ.get("LLM_BOOST_BASE_URL", "")
    boost_model = os.environ.get("LLM_BOOST_MODEL_NAME", "")
    has_boost_config = bool(boost_api_key)

    if use_boost and has_boost_config:
        llm_api_key = boost_api_key
        llm_base_url = boost_base_url
        llm_model = boost_model or os.environ.get("LLM_MODEL_NAME", "")
        config_label = "[加速LLM]"
    else:
        # 回退到 Config（其中已按 provider 编码了 kimi/minimax 的默认 base_url/model），
        # 这样仅在 .env 里设 LLM_PROVIDER=minimax + LLM_API_KEY、不显式给 base_url/model，
        # 也能正确指向 MiniMax 端点与 MiniMax-M3 模型（修复 OASIS 直读 os.environ 的盲点）。
        llm_api_key = os.environ.get("LLM_API_KEY", "") or (Config.LLM_API_KEY or "")
        llm_base_url = os.environ.get("LLM_BASE_URL", "") or (Config.LLM_BASE_URL or "")
        llm_model = os.environ.get("LLM_MODEL_NAME", "") or (Config.LLM_MODEL_NAME or "")
        config_label = "[通用LLM]"

    if not llm_model:
        llm_model = config.get("llm_model", "gpt-4o-mini")

    if llm_api_key:
        os.environ["OPENAI_API_KEY"] = llm_api_key

    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError("缺少 API Key 配置，请在项目根目录 .env 文件中设置 LLM_API_KEY")

    if llm_base_url:
        os.environ["OPENAI_API_BASE_URL"] = llm_base_url

    logger.info(f"OASIS model: {config_label} model={llm_model}, mode=openai")
    return ModelFactory.create(
        model_platform=ModelPlatformType.OPENAI,
        model_type=llm_model,
    )


def _inject_coding_agent_ua(model) -> None:
    """为 CAMEL OpenAIModel 注入 coding-agent User-Agent，以通过 Kimi-for-coding 网关。

    CAMEL 的 ``ModelFactory.create`` / ``OpenAIModel`` 不会把 ``default_headers``
    透传到底层 ``openai`` 客户端，因此在模型创建后直接用带 UA 头的客户端替换
    ``_client`` / ``_async_client``（同步与异步路径都要替换）。
    """
    from openai import OpenAI, AsyncOpenAI

    ua = Config.LLM_USER_AGENT
    common: Dict[str, Any] = dict(
        timeout=model._timeout,
        max_retries=model._max_retries,
        base_url=model._url,
        api_key=model._api_key,
        default_headers={"User-Agent": ua},
    )
    model._client = OpenAI(**common)
    model._async_client = AsyncOpenAI(**common)
    logger.info(f"OASIS kimi: 已为 OpenAI 客户端注入 coding-agent UA='{ua}'")


def create_oasis_model(config: Dict[str, Any], use_boost: bool = False):
    """创建 OASIS 模拟所用的 CAMEL 模型。

    provider=claude-cli/codex-cli -> CLIModel（CLI 桥接）
    provider=openai               -> ModelFactory OpenAI 路径（含 boost）
    provider=kimi                 -> ModelFactory OpenAI 路径 + 注入 coding-agent UA + 关闭推理
    provider=minimax              -> ModelFactory OpenAI 路径 + 关闭推理（无需 UA）
    """
    provider = _resolve_provider(config)

    if provider in CLI_PROVIDERS:
        model = config.get('llm_model') or provider
        logger.info(f"OASIS model: provider={provider}, model={model}, mode=cli-bridge")
        return CLIModel(
            model_type=model,
            provider=provider,
            model_config_dict={},
            api_key='cli-bridge',
        )

    model = _create_openai_model(config, use_boost=use_boost)
    if provider == 'kimi':
        # 仅 Kimi-for-coding 网关按 UA 校验 coding-agent 身份；MiniMax 不需要。
        _inject_coding_agent_ua(model)
    # 推理模型(kimi/minimax/deepseek/qwen/glm)默认关闭推理，避免 reasoning 吃光 token 预算
    # 导致 content 为空。reasoning_extra_body() 对非推理提供方返回 None，故可统一调用。
    # CAMEL 会把 model_config_dict 透传为 create() 关键字参数，故注入 extra_body。
    extra_body = Config.reasoning_extra_body()
    if extra_body is not None:
        try:
            model.model_config_dict["extra_body"] = extra_body
            logger.info(f"OASIS {provider}: 已关闭推理 via model_config_dict.extra_body={extra_body}")
        except Exception as e:
            logger.warning(f"OASIS {provider}: 注入 extra_body 失败（继续，但可能触发空 content）: {e}")
    return model


def get_oasis_semaphore(config: Dict[str, Any], use_boost: bool = False, platforms: int = 1) -> int:
    """根据提供方返回合适的 OASIS 并发上限。

    Args:
        platforms: 同进程内并发运行的平台数。双平台并行时各平台拿到 cap/2，
            使全局在飞 LLM 请求数符合 OASIS_*SEMAPHORE 的文档语义（此前
            Twitter、Reddit 各拿一整份，实际并发是配置值的 2 倍）。
    """
    provider = _resolve_provider(config)
    if provider in CLI_PROVIDERS:
        cap = int(os.environ.get('OASIS_CLI_SEMAPHORE', str(DEFAULT_CLI_SEMAPHORE)))
    else:
        cap = int(os.environ.get('OASIS_SEMAPHORE', str(DEFAULT_OPENAI_SEMAPHORE)))
    return max(1, cap // max(1, platforms))
