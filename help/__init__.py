from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from gsuid_core.bot import Bot
from gsuid_core.help.draw_new_plugin_help import get_new_help
from gsuid_core.help.utils import register_help
from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.segment import MessageSegment
from gsuid_core.sv import SV

sv_help = SV('鹿签帮助', pm=6, area='ALL', priority=1)
PLUGIN_DIR = Path(__file__).parent.parent
HELP_DIR = Path(__file__).parent
ICON_PATH = PLUGIN_DIR / 'ICON.png'
HELP_JSON_PATH = HELP_DIR / 'help.json'


def _load_help_data():
    with HELP_JSON_PATH.open('r', encoding='utf-8') as f:
        return json.load(f)


def _load_icon() -> Image.Image | None:
    if not ICON_PATH.is_file():
        logger.warning(f'[鹿签日历] 插件头像不存在: {ICON_PATH}')
        return None
    with Image.open(ICON_PATH) as icon:
        return icon.convert('RGBA')


@sv_help.on_fullmatch(
    ('🦌帮助', '鹿帮助', '鹿签帮助', '鹿签日历帮助'),
    prefix=False,
    block=True,
)
async def handle_help(bot: Bot, ev: Event) -> None:
    icon = _load_icon()
    if icon is None:
        await bot.send(
            '🦌 签到日历 帮助\n'
            '━━━━━━━━━━━━━━━\n'
            '🦌 — 每日签到，开盲盒\n'
            '🦌日历 / 签到日历 — 查看本月签到日历\n'
            '🦌帮助 / 鹿帮助 — 显示本帮助\n'
            '━━━━━━━━━━━━━━━\n'
            '主人命令:\n'
            '🦌满 — 一键签满本月'
        )
        return

    img = await get_new_help(
        plugin_name='DeerSignCalendar',
        plugin_info={'v0.1.0': ''},
        plugin_icon=icon,
        plugin_help=_load_help_data(),
        plugin_prefix='',
        help_mode='dark',
        banner_sub_text='到点了，该鹿了，少年。',
        enable_cache=False,
        column=2,
        pm=ev.user_pm,
    )
    await bot.send(MessageSegment.image(img))


if ICON_PATH.is_file():
    try:
        with Image.open(ICON_PATH) as _icon:
            register_help('鹿签日历', '鹿帮助', _icon.convert('RGBA'))
    except Exception as exc:
        logger.warning(f'[鹿签日历] 注册插件帮助失败: {exc}')
