from __future__ import annotations

from typing import Dict

from gsuid_core.utils.plugins_config.models import (
    GSC,
    GsStrConfig,
)

CONFIG_DEFAULT: Dict[str, GSC] = {
    'DeerExternalRenderUrl': GsStrConfig(
        '外置渲染地址',
        '留空时使用本机 Playwright 渲染。填写 http(s) 地址后，鹿签日历会优先向该地址 POST {"html": "..."} 渲染图片，失败自动回退本地渲染。',
        '',
        regex=r'^(|https?://.+)$',
    ),
}
