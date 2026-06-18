"""OpenAI 兼容 API 子任务调度器。

通过 OpenAI 兼容 API（DeepSeek / 通义千问等）调度 LLM 子任务。
支持并发控制、重试、流控、超时。
"""
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
from httpx import HTTPTransport
from openai import OpenAI, APIStatusError, APITimeoutError, APIConnectionError

from auto_qc.pi.agents.config import LlmConfig
from auto_qc.pi.engine.validator import validate_output

logger = logging.getLogger(__name__)

# 内容质量重试模板：仅 JSON 解析或 schema 校验失败时追加到 prompt
RETRY_PROMPT_TEMPLATE = """\n\n⚠️ 上一次输出校验失败：{error}\n请修正后重新输出。要求：\n1. 回复**只包含** JSON 数组，以 `[` 开头、以 `]` 结尾\n2. JSON 前后不要有任何说明文字或 markdown 标记\n3. 确保所有字符串用双引号包裹，没有尾逗号\n4. **字段名必须是英文**：pattern_name、description、severity、found_count、total_checked、examples——不要翻译成中文"""


@dataclass
class TokenUsage:
    """Token 用量统计。"""
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, inp: int, out: int) -> None:
        self.input_tokens += inp
        self.output_tokens += out

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def __str__(self) -> str:
        return f"Token 用量: 输入 {self.input_tokens:,} | 输出 {self.output_tokens:,} | 总计 {self.total:,}"


class Scheduler:
    """LLM 子任务调度器。

    通过 OpenAI 兼容 API 创建聊天补全请求，管理并发、重试、流控。
    """

    def __init__(self, config: LlmConfig):
        self.config = config
        self._client = None
        self.usage = TokenUsage()

    @property
    def client(self):
        """惰性初始化 OpenAI 兼容客户端。

        使用自定义 httpx Client 直连 API（不走系统代理）。
        max_retries=0：禁用 SDK 内部重试（默认会以短间隔重试 3 次，504 场景下每次等 3 分钟再超时，纯属浪费）
        所有重试由 run_task 手动控制，用更合理的延迟策略。
        """
        if self._client is None:
            if not self.config.api_key:
                raise ValueError(
                    "未检测到 API key。请在 .env 文件中设置 LLM_API_KEY，"
                    "或在 config.yaml 的 llm.api_key 中手动设置"
                )
            if not self.config.base_url:
                raise ValueError(
                    "未检测到 base_url。请在 .env 文件中设置 LLM_BASE_URL，"
                    "或在 config.yaml 的 llm.base_url 中手动设置"
                )
            self._client = OpenAI(
                api_key=self.config.api_key,
                base_url=self.config.base_url,
                http_client=httpx.Client(
                    transport=HTTPTransport(proxy=None),
                ),
                max_retries=0,
            )
        return self._client

    def run_task(
        self,
        system_prompt: str,
        user_prompt: str,
        output_file: Path,
        validator_phase: str | None = None,
        temperature: float | None = None,
    ) -> tuple[bool, Any, str]:
        """执行单个 LLM 任务。

        重试策略：
        - API 错误（网络、限流、服务端）→ 等待后重试，不修改 prompt
        - 内容错误（JSON 解析、schema 校验）→ 追加错误反馈后重试
        """
        content_prompt = user_prompt  # 仅内容错误时追加反馈

        for attempt in range(1, self.config.max_retries + 1):
            logger.info(f"  LLM 任务 (attempt {attempt}/{self.config.max_retries})...")

            # 1. API 调用
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content_prompt},
            ]
            try:
                kwargs = dict(
                    model=self.config.model or "deepseek-chat",
                    max_tokens=8192,
                    messages=messages,
                    timeout=self.config.timeout,
                    response_format={"type": "json_object"},
                )
                if temperature is not None:
                    kwargs["temperature"] = temperature
                response = self.client.chat.completions.create(**kwargs)
                raw_text = response.choices[0].message.content or ""
                # 记录 token 用量（即使是内容错误重试，已消耗的 token 也统计在内）
                if hasattr(response, "usage") and response.usage:
                    self.usage.add(response.usage.prompt_tokens, response.usage.completion_tokens)
            except APIStatusError as e:
                delay = self._handle_api_error(e, attempt)
                if attempt == self.config.max_retries:
                    return False, None, f"api_error_{e.status_code}"
                time.sleep(delay)
                continue
            except (APITimeoutError, APIConnectionError) as e:
                logger.warning(f"  网络/超时错误: {e}")
                if attempt == self.config.max_retries:
                    return False, None, "network_error"
                time.sleep(self.config.retry_delay * attempt)
                continue
            except Exception as e:
                logger.warning(f"  未知 API 错误: {e}")
                if attempt == self.config.max_retries:
                    return False, None, "api_call_failed"
                time.sleep(self.config.retry_delay * attempt)
                continue

            # 2. JSON 解析 + 自修复
            data = self._extract_json(raw_text)
            if data is None:
                # 尝试自修复常见 JSON 错误后再解析一次
                healed = self._heal_json(raw_text)
                if healed is not None:
                    data = healed
            if data is None:
                error = "无法从输出中提取合法 JSON"
                logger.warning(f"  {error}")
                if attempt == self.config.max_retries:
                    return False, None, "invalid_json"
                content_prompt += RETRY_PROMPT_TEMPLATE.format(error=error)
                continue

            # 3. Schema 校验
            if validator_phase:
                valid, msg = validate_output(validator_phase, data)
                if not valid:
                    logger.warning(f"  Schema 校验失败: {msg}")
                    if attempt == self.config.max_retries:
                        return False, None, f"schema_validation_failed: {msg}"
                    content_prompt += RETRY_PROMPT_TEMPLATE.format(error=msg)
                    continue

            # 成功：写入结果文件
            output_file.parent.mkdir(parents=True, exist_ok=True)
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True, data, ""

        return False, None, "max_retries_exceeded"

    def _handle_api_error(self, error: APIStatusError, attempt: int) -> float:
        """处理 API 状态错误，返回等待秒数。"""
        status = error.status_code
        if status == 429:
            delay = self.config.retry_delay * attempt
            logger.warning(f"  API 限流 (429)，等待 {delay}s...")
            return delay
        elif status >= 500:
            # 504 Gateway Timeout：用更长的等待让网关恢复，重试间隔逐步递增
            delay = (self.config.retry_delay * 2) * attempt
            logger.warning(f"  服务端错误 ({status})，等待 {delay}s...")
            return delay
        elif status == 400:
            logger.error(f"  请求参数错误 (400): {error}")
            return 0  # 400 错误重试无意义，但让循环走完
        else:
            logger.warning(f"  API 错误 ({status}): {error}")
            return self.config.retry_delay * attempt

    def run_tasks_batch(
        self,
        tasks: list[dict],
        system_prompt: str,
        validator_phase: str | None = None,
        temperature: float | None = None,
    ) -> list[tuple[bool, Any, str]]:
        """批量执行多个 LLM 任务（并发控制）。

        Args:
            tasks: 任务列表，每个 task 包含:
                - user_prompt: 用户 prompt
                - output_file: 输出文件路径（str 或 Path）
                可选:
                - temperature: 该任务专用的 temperature（覆盖 batch 级设置）
                - system_prompt: 该任务专用的 system_prompt（覆盖 batch 级设置）
            system_prompt: 系统 prompt（所有任务共用，可被 task 级覆盖）
            validator_phase: 校验阶段名称

        Returns:
            每个任务的结果列表: [(success, data, error), ...]
        """
        results = [None] * len(tasks)
        concurrency = min(self.config.concurrency, len(tasks))

        def run_one(idx: int, task: dict) -> tuple[int, bool, Any, str]:
            output_file = Path(task["output_file"]) if isinstance(task["output_file"], str) else task["output_file"]
            task_temp = task.get("temperature", temperature)
            task_sys = task.get("system_prompt", system_prompt)
            success, data, error = self.run_task(
                system_prompt=task_sys,
                user_prompt=task["user_prompt"],
                output_file=output_file,
                validator_phase=validator_phase,
                temperature=task_temp,
            )
            return idx, success, data, error

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {executor.submit(run_one, i, t): i for i, t in enumerate(tasks)}
            for future in as_completed(futures):
                idx, success, data, error = future.result()
                results[idx] = (success, data, error)
                logger.info(f"  任务 [{idx+1}/{len(tasks)}] {'完成' if success else '失败'}: {error}")

        return results

    @staticmethod
    def _extract_json(text: str) -> Any | None:
        """从 LLM 输出文本中提取 JSON。

        尝试多种策略：直接解析、查找 ```json 块、查找第一个 [ 或 { 到最后一个 ] 或 }。
        底层函数在失败时抛出 ValueError，这里捕获并返回 None 供重试机制处理。
        """
        from auto_qc.pi.utils.json_utils import extract_json_from_text
        try:
            return extract_json_from_text(text)
        except ValueError:
            return None

    @staticmethod
    def _heal_json(text: str) -> Any | None:
        """自修复常见 JSON 格式错误后尝试解析。

        委托给 json_utils.heal_json 处理。
        """
        from auto_qc.pi.utils.json_utils import heal_json
        return heal_json(text)
