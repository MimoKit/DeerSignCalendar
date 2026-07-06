from __future__ import annotations

from typing import Dict

from gsuid_core.utils.plugins_config.models import (
    GSC,
    GsBoolConfig,
    GsStrConfig,
)

CONFIG_DEFAULT: Dict[str, GSC] = {
    'DeerNoRepeatImage': GsBoolConfig(
        '背景图不重复',
        '默认关闭。开启后同一用户同月日历背景图会优先不重复；只影响背景图分配，不改变签到结果。',
        False,
    ),
    'DeerExternalRenderUrl': GsStrConfig(
        '外置渲染地址',
        '留空时使用本机 Playwright 渲染。填写 http(s) 地址后，鹿签日历会优先向该地址 POST {"html": "..."} 渲染图片，失败自动回退本地渲染。',
        '',
        regex=r'^(|https?://.+)$',
    ),
}
