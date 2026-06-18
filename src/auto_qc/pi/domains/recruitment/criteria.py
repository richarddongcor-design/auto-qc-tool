"""招聘场景特有的质检标准。

这些值由领域插件提供给框架，框架不硬编码它们。
"""

RECRUITMENT_CRITERIA = {
    # 无响应阈值：用户无应答 3 次后挂机是正常行为
    "no_response_threshold": 3,

    # 是否过滤 TTS 控制符号
    "filter_tts_symbols": True,

    # 用户挂机特征检测是否启用
    "detect_user_hangup": True,

    # 各严重程度的确认阈值（命中率）
    "thresholds": {
        "high": {"confirmed": 0.03, "rejected": 0.005},
        "medium": {"confirmed": 0.01, "rejected": 0.003},
        "low": {"confirmed": 0.005, "rejected": 0.001},
    },
}