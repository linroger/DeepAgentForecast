"""
LLM客户端封装

支持三种提供方（由 Config.LLM_PROVIDER 决定，默认 claude-cli）：
  - claude-cli: 通过本机 `claude` CLI 调用（Claude Code 订阅，无需 API Key）
  - codex-cli:  通过本机 `codex` CLI 调用（Codex 订阅，无需 API Key）
  - openai:     OpenAI 兼容 API（需要 LLM_API_KEY），保留作为回退

对外接口统一为 chat() / chat_json()，调用方无需关心底层提供方。
"""

import json
import re
import subprocess
import time
from typing import Optional, Dict, Any, List

from ..config import Config
from .logger import get_logger

logger = get_logger('mirofish.llm_client')

CLI_PROVIDERS = ('claude-cli', 'codex-cli')
# OpenAI 兼容的 HTTP 提供方（openai 原生 + kimi-for-coding + minimax 代码计划）
OPENAI_COMPATIBLE_PROVIDERS = ('openai', 'kimi', 'minimax')

# CLI 调用的瞬时失败重试配置
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0  # 秒


class LLMClient:
    """LLM客户端 — 支持 claude-cli / codex-cli / openai"""

    def __init__(
        self,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None
    ):
        self.provider = (provider or Config.LLM_PROVIDER or "claude-cli").lower()

        # openai 提供方所需的连接参数（CLI 模式下不使用）
        self.api_key = api_key or Config.LLM_API_KEY
        self.base_url = base_url or Config.LLM_BASE_URL
        self.model = model or Config.LLM_MODEL_NAME

        if self.provider not in CLI_PROVIDERS and self.provider not in OPENAI_COMPATIBLE_PROVIDERS:
            raise ValueError(
                f"不支持的 LLM 提供方: {self.provider!r}。"
                f"可选: 'claude-cli' / 'codex-cli' / 'openai' / 'kimi' / 'minimax'。"
            )

        # 仅在使用 OpenAI 兼容提供方（openai/kimi）时才创建 OpenAI 客户端（CLI 模式无需 API Key）
        self._openai_client = None
        if self.provider in OPENAI_COMPATIBLE_PROVIDERS:
            from openai import OpenAI
            if not self.api_key:
                raise ValueError(f"LLM_PROVIDER={self.provider} 时必须配置 LLM_API_KEY")
            client_kwargs: Dict[str, Any] = {"api_key": self.api_key, "base_url": self.base_url}
            # Kimi-for-coding 网关按 User-Agent 校验 coding-agent 身份；
            # 不带可识别的 UA 会被拒绝（access_terminated_error）。
            if self.provider == "kimi":
                client_kwargs["default_headers"] = {"User-Agent": Config.LLM_USER_AGENT}
            self._openai_client = OpenAI(**client_kwargs)

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[Dict] = None
    ) -> str:
        """
        发送聊天请求，返回模型响应文本。

        CLI 提供方在瞬时失败时自动指数退避重试（3 次）。
        """
        if self.provider in OPENAI_COMPATIBLE_PROVIDERS:
            return self._chat_openai(messages, temperature, max_tokens, response_format)

        last_error: Optional[Exception] = None
        for attempt in range(MAX_RETRIES):
            try:
                if self.provider == "codex-cli":
                    return self._chat_codex_cli(messages, temperature, max_tokens, response_format)
                return self._chat_claude_cli(messages, temperature, max_tokens, response_format)
            except RuntimeError as exc:
                last_error = exc
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        f"LLM 调用失败 (第 {attempt + 1}/{MAX_RETRIES} 次)，{delay}s 后重试: {exc}"
                    )
                    time.sleep(delay)
        raise last_error if last_error is not None else RuntimeError("LLM 调用失败")

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096
    ) -> Dict[str, Any]:
        """发送聊天请求并返回解析后的 JSON。"""
        response = self.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"}
        )
        # 清理 markdown 代码块标记
        cleaned_response = response.strip()
        cleaned_response = re.sub(r'^```(?:json)?\s*\n?', '', cleaned_response, flags=re.IGNORECASE)
        cleaned_response = re.sub(r'\n?```\s*$', '', cleaned_response)
        cleaned_response = cleaned_response.strip()

        try:
            return json.loads(cleaned_response)
        except json.JSONDecodeError:
            raise ValueError(f"LLM返回的JSON格式无效: {cleaned_response[:500]}")

    # ------------------------------------------------------------------
    # 共享辅助
    # ------------------------------------------------------------------
    def _split_system_message(self, messages: List[Dict[str, str]]):
        """从对话消息中拆分出 system 指令。"""
        system_text = None
        conversation = []
        for msg in messages:
            if msg.get("role") == "system":
                if system_text is None:
                    system_text = msg["content"]
                else:
                    system_text += "\n\n" + msg["content"]
            else:
                conversation.append(msg)
        return system_text, conversation

    def _flatten_prompt(
        self,
        messages: List[Dict[str, str]],
        response_format: Optional[Dict] = None
    ) -> str:
        """将多轮消息扁平化为单条 prompt（CLI 提供方使用）。"""
        system_text, conversation = self._split_system_message(messages)

        prompt_parts: List[str] = []
        if system_text:
            prompt_parts.append(f"SYSTEM INSTRUCTIONS:\n{system_text}\n")

        if response_format and response_format.get("type") == "json_object":
            prompt_parts.append(
                "IMPORTANT: Respond with valid JSON only. "
                "No markdown, no explanation, just pure JSON.\n"
            )

        for msg in conversation:
            role = msg.get("role", "user").upper()
            prompt_parts.append(f"{role}: {msg['content']}")

        return "\n\n".join(prompt_parts)

    def _clean_content(self, content: str) -> str:
        """移除推理模型的 <think> 标签。"""
        return re.sub(r'<think>[\s\S]*?</think>', '', content).strip()

    # ------------------------------------------------------------------
    # openai 提供方
    # ------------------------------------------------------------------
    def _chat_openai(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        response_format: Optional[Dict] = None
    ) -> str:
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            kwargs["response_format"] = response_format

        # kimi / minimax 推理模型：默认关闭推理，避免 reasoning 吃光 max_tokens 导致 content 为空。
        if self.provider in ("kimi", "minimax"):
            extra_body = Config.reasoning_extra_body()
            if extra_body:
                kwargs["extra_body"] = extra_body

        response = self._openai_client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        content = choice.message.content
        # 推理模型在 content 被推理耗尽时会返回空串/None（finish_reason=length）。
        # 明确报错而不是把空串交给下游 JSON 解析，便于定位与重试。
        if content is None or not content.strip():
            finish = getattr(choice, "finish_reason", None)
            raise RuntimeError(
                f"OpenAI 兼容提供方({self.provider})返回空 content（finish_reason={finish}）。"
                f"若为 kimi/minimax 推理模型，请确认已关闭推理(LLM_{self.provider.upper()}_DISABLE_THINKING)或增大 max_tokens。"
            )
        return self._clean_content(content)

    # ------------------------------------------------------------------
    # claude-cli 提供方
    # ------------------------------------------------------------------
    def _chat_claude_cli(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        response_format: Optional[Dict] = None
    ) -> str:
        """通过 Claude Code CLI 调用。"""
        prompt = self._flatten_prompt(messages, response_format)

        try:
            result = subprocess.run(
                ["claude", "-p", "--output-format", "json", prompt],
                capture_output=True, text=True, timeout=300,
                cwd="/tmp"
            )

            if result.returncode != 0:
                logger.error(f"Claude CLI error: {result.stderr[:200]}")
                raise RuntimeError(f"Claude CLI failed: {result.stderr[:200]}")

            try:
                output = json.loads(result.stdout)
                # Detect error envelopes (e.g. is_error / non-success subtype) so the
                # caller's exponential-backoff retry kicks in instead of silently
                # propagating an empty/invalid result (often rate-limit induced).
                if isinstance(output, dict) and (
                    output.get("is_error") or output.get("subtype") not in (None, "success")
                ):
                    raise RuntimeError(
                        f"Claude CLI error envelope: subtype={output.get('subtype')!r} "
                        f"is_error={output.get('is_error')!r}"
                    )
                content = output.get("result", result.stdout) if isinstance(output, dict) else result.stdout
            except json.JSONDecodeError:
                content = result.stdout.strip()

            content = self._clean_content(content)
            if not content:
                raise RuntimeError("Claude CLI returned empty result")
            return content

        except subprocess.TimeoutExpired:
            raise RuntimeError("Claude CLI timed out after 300s")
        except FileNotFoundError:
            raise RuntimeError(
                "未找到 `claude` 可执行文件，请确认已安装 Claude Code CLI 并加入 PATH"
            )

    # ------------------------------------------------------------------
    # codex-cli 提供方
    # ------------------------------------------------------------------
    def _chat_codex_cli(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        response_format: Optional[Dict] = None
    ) -> str:
        """通过 Codex CLI 调用。"""
        prompt = self._flatten_prompt(messages, response_format)

        try:
            result = subprocess.run(
                ["codex", "exec", "--skip-git-repo-check"],
                input=prompt,
                capture_output=True, text=True, timeout=180,
                cwd="/tmp"
            )

            if result.returncode != 0:
                logger.error(f"Codex CLI error: {result.stderr[:200]}")
                raise RuntimeError(f"Codex CLI failed: {result.stderr[:200]}")

            raw = result.stdout.strip()
            parts = raw.split("\ncodex\n")
            if len(parts) > 1:
                content = parts[-1].strip()
                lines = content.split("\n")
                clean_lines = []
                for line in lines:
                    if line.strip() == "tokens used":
                        break
                    clean_lines.append(line)
                content = "\n".join(clean_lines).strip()
            else:
                content = raw
            return self._clean_content(content)

        except subprocess.TimeoutExpired:
            raise RuntimeError("Codex CLI timed out after 180s")
        except FileNotFoundError:
            raise RuntimeError(
                "未找到 `codex` 可执行文件，请确认已安装 Codex CLI 并加入 PATH"
            )
