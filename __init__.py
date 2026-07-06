from __future__ import annotations

import asyncio
import json
import base64
import random
import hashlib
import calendar
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from jinja2 import Environment, FileSystemLoader, select_autoescape

from gsuid_core.bot import Bot
from gsuid_core.config import core_config
from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.sv import Plugins, SV

from . import help  # noqa: F401 - 加载帮助模块
from .deer_config import DeerSignConfig
from .paths import BACKGROUND_DIR, get_background_image_paths, image_mime

try:
    from playwright.async_api import async_playwright
except Exception:
    async_playwright = None


Plugins(
    name='DeerSignCalendar',
    allow_empty_prefix=True,
)

sv = SV('鹿签日历')
BASE_DIR = Path(__file__).parent
TEMPLATE_ROOT = BASE_DIR / 'templates'
STATE_PATH = BASE_DIR / 'sign_state.json'
SIGN_IMAGES_DIR = BASE_DIR / 'sign_images'
BG_IMAGES_DIR = BACKGROUND_DIR

sign_templates = Environment(
    loader=FileSystemLoader([str(TEMPLATE_ROOT)]),
    autoescape=select_autoescape(('html', 'xml')),
)

QQ_AVATAR_URL = 'http://q1.qlogo.cn/g?b=qq&nk={qid}&s=640'
_STATE_LOCK = asyncio.Lock()

# 盲盒 SVG - 圆润可爱盲盒风格
MYSTERY_BOX_SVG = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200">
  <defs>
    <linearGradient id="boxBody" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" style="stop-color:#b8e6ff"/>
      <stop offset="100%" style="stop-color:#7ec8e3"/>
    </linearGradient>
    <linearGradient id="boxLid" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" style="stop-color:#ffe066"/>
      <stop offset="100%" style="stop-color:#ffb347"/>
    </linearGradient>
    <linearGradient id="star" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#fff176"/>
      <stop offset="100%" style="stop-color:#ffee58"/>
    </linearGradient>
    <filter id="ds">
      <feDropShadow dx="0" dy="4" stdDeviation="4" flood-color="#000" flood-opacity="0.12"/>
    </filter>
  </defs>
  <rect x="0" y="0" width="200" height="200" rx="20" fill="#f5f0ff"/>
  <rect x="40" y="80" width="120" height="90" rx="18" fill="url(#boxBody)" filter="url(#ds)"/>
  <rect x="40" y="80" width="120" height="90" rx="18" fill="none" stroke="#fff" stroke-width="2" opacity="0.5"/>
  <ellipse cx="100" cy="170" rx="50" ry="6" fill="#000" opacity="0.06"/>
  <rect x="34" y="66" width="132" height="24" rx="12" fill="url(#boxLid)" filter="url(#ds)"/>
  <rect x="34" y="66" width="132" height="24" rx="12" fill="none" stroke="#fff" stroke-width="1.5" opacity="0.6"/>
  <rect x="90" y="66" width="20" height="104" rx="6" fill="#fff" opacity="0.2"/>
  <rect x="40" y="90" width="120" height="6" rx="3" fill="#fff" opacity="0.2"/>
  <circle cx="100" cy="52" r="16" fill="url(#boxLid)" filter="url(#ds)"/>
  <path d="M86 52 C86 38 100 32 100 46" fill="none" stroke="#ff9800" stroke-width="4" stroke-linecap="round"/>
  <path d="M114 52 C114 38 100 32 100 46" fill="none" stroke="#ff9800" stroke-width="4" stroke-linecap="round"/>
  <text x="100" y="140" text-anchor="middle" font-size="40" font-weight="900" fill="#fff" font-family="Arial" opacity="0.85">?</text>
  <polygon points="155,45 158,51 165,52 160,57 161,64 155,60 149,64 150,57 145,52 152,51" fill="url(#star)" opacity="0.9"/>
  <polygon points="50,100 52,104 56,105 53,108 54,112 50,110 46,112 47,108 44,105 48,104" fill="url(#star)" opacity="0.7"/>
  <polygon points="160,100 161,103 164,103 162,105 163,108 160,107 157,108 158,105 156,103 159,103" fill="url(#star)" opacity="0.6"/>
  <circle cx="60" cy="75" r="3" fill="#fff" opacity="0.6"/>
  <circle cx="145" cy="130" r="2.5" fill="#fff" opacity="0.5"/>
  <circle cx="70" cy="150" r="2" fill="#fff" opacity="0.4"/>
</svg>'''


def _get_mystery_box_data_uri() -> str:
    b64 = base64.b64encode(MYSTERY_BOX_SVG.encode('utf-8')).decode('ascii')
    return f'data:image/svg+xml;base64,{b64}'


CONTAINER_WIDTH_DEFAULT = 820
CONTAINER_WIDTH_MIN = 620
CONTAINER_WIDTH_MAX = 980


def _container_width_for_aspect(aspect_ratio: float) -> int:
    """根据背景图宽高比算一个卡片宽度，避免所有图片都被硬塞进同一个 820px 的框里。
    用 0.35 次幂压一下缩放幅度，竖图也能按比例变窄、横图变宽，又不会让 7 列格子挤得太小。"""
    width = CONTAINER_WIDTH_DEFAULT * (aspect_ratio ** 0.35)
    return round(max(CONTAINER_WIDTH_MIN, min(CONTAINER_WIDTH_MAX, width)))


def _dedupe_image_paths_by_content(images: list[Path], label: str) -> list[Path]:
    """按文件内容去重图片，避免不同文件名但同图导致视觉上重复。"""
    deduped: list[Path] = []
    seen_digests: set[str] = set()
    for image in images:
        try:
            digest = hashlib.sha256(image.read_bytes()).hexdigest()
        except Exception as e:
            logger.warning(f'[鹿签日历] 读取{label}失败，已跳过: {image.name}, {e}')
            continue
        if digest in seen_digests:
            continue
        seen_digests.add(digest)
        deduped.append(image)
    return deduped


def _background_identity(image: Path) -> str:
    """生成背景图稳定标识。

    这里不能读取整张图片计算内容 hash：
    官服的 XWUID 面板图目录可能是 SSHFS 远程挂载，逐张读文件会造成大量网络下载。
    用路径 + 文件大小 + 修改时间即可满足“本月每次尽量换不同背景”的历史记录需求。
    """
    try:
        stat = image.stat()
        return f'{image}|{stat.st_size}|{stat.st_mtime_ns}'
    except Exception:
        return str(image)


def _dedupe_background_candidates(images: list[Path]) -> list[tuple[Path, str]]:
    """返回按文件标识去重后的背景候选。

    注意：只做目录项 / stat 级别的轻量去重，不读取图片内容，避免远程挂载目录产生大流量。
    """
    candidates: list[tuple[Path, str]] = []
    seen_identities: set[str] = set()
    for image in images:
        identity = _background_identity(image)
        if identity in seen_identities:
            continue
        seen_identities.add(identity)
        candidates.append((image, identity))
    return candidates


def _background_no_repeat_enabled() -> bool:
    try:
        value = DeerSignConfig.get_config('DeerNoRepeatImage').data
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {'1', 'true', 'yes', 'on', '开启', '开'}
        return bool(value)
    except Exception as e:
        logger.warning(f'[鹿签日历] 读取背景图不重复配置失败，使用默认关闭: {e}')
        return False


def _external_render_url() -> str:
    """读取外置渲染地址；留空或格式不对时返回空串，走本地 Playwright。"""
    try:
        value = DeerSignConfig.get_config('DeerExternalRenderUrl').data
    except Exception as e:
        logger.warning(f'[鹿签日历] 读取外置渲染地址配置失败，使用本地渲染: {e}')
        return ''

    url = str(value or '').strip()
    if not url:
        return ''
    if not url.startswith(('http://', 'https://')):
        logger.warning(f'[鹿签日历] 外置渲染地址格式不正确，已忽略: {url}')
        return ''
    return url


def _select_background_image(
    user_key: str,
    year: int,
    month: int,
    day: int,
    candidates: list[tuple[Path, str]],
    state: Dict[str, Any],
    advance: bool,
) -> Path:
    if not candidates:
        raise ValueError('empty background candidates')

    if _background_no_repeat_enabled() and advance:
        month_key = f'{year}-{month:02d}'
        history_root = state.setdefault('_background_history', {})
        user_history = history_root.setdefault(user_key, {})
        month_history = user_history.setdefault(month_key, {})

        current_digests = {digest for _, digest in candidates}
        used = [
            digest
            for digest in month_history.get('used', [])
            if isinstance(digest, str) and digest in current_digests
        ]
        if len(used) >= len(candidates):
            used = []

        order = list(range(len(candidates)))
        rng = random.Random(f'bg-order-{user_key}-{year}-{month}')
        rng.shuffle(order)

        used_set = set(used)
        chosen_index = order[0]
        for idx in order:
            if candidates[idx][1] not in used_set:
                chosen_index = idx
                break

        chosen, digest = candidates[chosen_index]
        used.append(digest)
        month_history['used'] = used
        month_history['pool_size'] = len(candidates)
        return chosen

    rng = random.Random(f'bg-{user_key}-{year}-{month}-{day}')
    return rng.choice([path for path, _ in candidates])


def _get_bg_image_data_uri(
    user_key: str,
    year: int,
    month: int,
    day: int,
    state: Dict[str, Any],
    advance: bool,
) -> tuple[str, int, Optional[int]]:
    """从本地背景图/面板图目录选一张背景图，转为 base64 data URI；
    同时按图片真实宽高比算出卡片宽度，并把卡片高度也锁定成 width / 图片宽高比，
    让卡片本身的宽高比和图片完全一致——这样 background-size: cover 不需要裁掉任何部分，
    整张图都能完整放进卡片里（日历内容只占卡片上半部分，下面继续露出背景图）。

    默认按"用户+日期"做种子选图。
    如果控制台开启「背景图不重复」，则同一用户同月每次渲染都会优先取一张没用过的背景图；
    这只写入独立的背景历史，不改变签到日期结果。

    背景读取顺序：
    1. data/DeerSignCalendar/sign-bj 下用户手动放入的背景图；
    2. 如果上面为空，则按今日老婆 local 模式读取本机 XWUID 用户上传面板图。
    不读取 XWUID 默认立绘目录，避免默认立绘混进背景池。"""
    candidates = _dedupe_background_candidates(
        sorted(get_background_image_paths(), key=lambda p: str(p).lower()),
    )
    if not candidates:
        return '', CONTAINER_WIDTH_DEFAULT, None
    chosen = _select_background_image(
        user_key,
        year,
        month,
        day,
        candidates,
        state,
        advance,
    )
    try:
        data = chosen.read_bytes()
        b64 = base64.b64encode(data).decode('ascii')
        mime = image_mime(chosen)
        container_width = CONTAINER_WIDTH_DEFAULT
        container_height: Optional[int] = None
        try:
            from PIL import Image
            with Image.open(chosen) as img:
                w, h = img.size
            if w > 0 and h > 0:
                aspect_ratio = w / h
                container_width = _container_width_for_aspect(aspect_ratio)
                container_height = round(container_width / aspect_ratio)
        except Exception as e:
            logger.warning(f'[鹿签日历] 读取背景图尺寸失败，使用默认宽度: {e}')
        return f'data:{mime};base64,{b64}', container_width, container_height
    except Exception:
        return '', CONTAINER_WIDTH_DEFAULT, None


def _scan_sign_images() -> List[Path]:
    """扫描所有可用的签到盲盒图片。"""
    if not SIGN_IMAGES_DIR.exists():
        return []
    images = [
        f
        for f in sorted(SIGN_IMAGES_DIR.rglob('*'), key=lambda p: str(p).lower())
        if f.is_file() and f.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp', '.gif')
    ]
    images = _dedupe_image_paths_by_content(images, '盲盒图片')
    return sorted(images, key=lambda p: p.name)


def _get_image_for_day(
    user_id: str,
    year: int,
    month: int,
    day: int,
    images: List[Path],
    shuffle_seed: int = 0,
) -> str:
    """根据 user_id + 年月 + shuffle_seed 生成一个不重复的排列，每天对应不同盲盒图片。"""
    if not images:
        return _get_mystery_box_data_uri()
    seed_str = f'{user_id}-{year}-{month}-{shuffle_seed}'
    rng = random.Random(seed_str)
    shuffled = list(range(len(images)))
    rng.shuffle(shuffled)
    idx = shuffled[(day - 1) % len(shuffled)]
    img_path = images[idx]
    try:
        data = img_path.read_bytes()
        b64 = base64.b64encode(data).decode('ascii')
        suffix = img_path.suffix.lower()
        mime = {
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.webp': 'image/webp',
            '.gif': 'image/gif',
        }.get(suffix, 'image/png')
        return f'data:{mime};base64,{b64}'
    except Exception:
        return _get_mystery_box_data_uri()


def _load_state() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding='utf-8'))
    except Exception as e:
        logger.warning(f'[鹿签日历] 读取签到状态失败: {e}')
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    temp = STATE_PATH.with_suffix('.json.tmp')
    try:
        temp.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8'
        )
        temp.replace(STATE_PATH)
    except Exception as e:
        logger.warning(f'[鹿签日历] 保存签到状态失败: {e}')
        try:
            if temp.exists():
                temp.unlink()
        except Exception:
            pass


def _get_user_key(ev: Event) -> str:
    return f'{ev.user_id}'


def _get_month_key() -> str:
    now = datetime.now()
    return f'{now.year}-{now.month:02d}'


def _get_today_day() -> int:
    return datetime.now().day


def _get_user_signs(state: Dict[str, Any], user_key: str, month_key: str) -> list:
    return state.get(user_key, {}).get(month_key, [])


def _do_sign(state: Dict[str, Any], user_key: str, month_key: str, day: int) -> None:
    """执行签到（仅添加，不 toggle）"""
    user_data = state.setdefault(user_key, {})
    signs = user_data.setdefault(month_key, [])
    if day not in signs:
        signs.append(day)
        signs.sort()


def _build_calendar_context(
    ev: Event,
    state: Dict[str, Any],
    advance_background: bool = False,
) -> Dict[str, Any]:
    now = datetime.now()
    year = now.year
    month = now.month
    month_key = _get_month_key()
    user_key = _get_user_key(ev)
    signed_days = _get_user_signs(state, user_key, month_key)

    days_in_month = calendar.monthrange(year, month)[1]
    first_weekday = calendar.monthrange(year, month)[0]
    # 周一为第一列，first_weekday 已经是 0=周一，直接用
    first_day_offset = first_weekday

    avatar_url = QQ_AVATAR_URL.format(qid=ev.user_id)
    mystery_box = _get_mystery_box_data_uri()
    images = _scan_sign_images()
    shuffle_seed = state.get('_shuffle_seed', 0)

    cells = []
    for _ in range(first_day_offset):
        cells.append({'day': 0, 'signed': False, 'img': '', 'is_today': False})
    for day in range(1, days_in_month + 1):
        is_today = day == now.day
        if day in signed_days:
            img = _get_image_for_day(user_key, year, month, day, images, shuffle_seed)
            cells.append({'day': day, 'signed': True, 'img': img, 'is_today': is_today})
        else:
            cells.append({'day': day, 'signed': False, 'img': mystery_box, 'is_today': is_today})

    title = f'{year}-{month:02d} 签到日历'
    bg_image, container_width, container_height = _get_bg_image_data_uri(
        user_key,
        year,
        month,
        now.day,
        state,
        advance_background,
    )

    return {
        'title': title,
        'avatar_url': avatar_url,
        'bg_image': bg_image,
        'container_width': container_width,
        'container_height': container_height,
        'cells': cells,
        'year': year,
        'month': month,
        'days_in_month': days_in_month,
        'signed_count': len(signed_days),
    }


async def _render_calendar(context: Dict[str, Any]) -> Optional[bytes]:
    template = sign_templates.get_template('calendar.html')
    html_content = template.render(**context)

    remote_url = _external_render_url()
    if remote_url:
        remote_image = await _render_calendar_via_remote(html_content, remote_url)
        if remote_image is not None:
            return remote_image

    if async_playwright is None:
        return None

    playwright = None
    browser = None
    try:
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(
            args=['--no-sandbox', '--disable-setuid-sandbox']
        )
        # 竖图背景图按真实宽高比锁高度，卡片可能很高（极端竖图能到 1500+px），视口给够余量；
        # device_scale_factor=2 按 2 倍分辨率截图，配合下面的 PNG（无损）输出，
        # 避免 OneBot V11/QQ 端把缩略图放大后看起来发糊
        page = await browser.new_page(
            viewport={'width': CONTAINER_WIDTH_MAX + 20, 'height': 1700},
            device_scale_factor=2,
        )
        await page.set_content(html_content, wait_until='load')
        container = page.locator('.container')
        await page.wait_for_selector('.container', timeout=3000)
        size = await container.evaluate(
            """(el) => {
                const rect = el.getBoundingClientRect();
                return { width: Math.ceil(rect.width), height: Math.ceil(rect.height) };
            }"""
        )
        if size and size.get('width') and size.get('height'):
            await page.set_viewport_size({
                'width': max(1, int(size['width'])),
                'height': max(1, int(size['height'])),
            })
        return await container.screenshot(type='png')
    finally:
        if browser is not None:
            await browser.close()
        if playwright is not None:
            await playwright.stop()


async def _render_calendar_via_remote(html_content: str, remote_url: str) -> Optional[bytes]:
    """使用外置渲染服务渲染日历 HTML。

    兼容 XWUID 现有协议：POST {"html": "..."}，服务直接返回图片 bytes。
    额外附带 selector / viewport / device_scale_factor 等字段，方便更精细的渲染服务裁剪
    `.container`，旧服务会自动忽略这些额外字段。
    """
    start_time = time.monotonic()
    payload = {
        'html': html_content,
        'selector': '.container',
        'clip_selector': '.container',
        'wait_for_selector': '.container',
        'viewport': {'width': CONTAINER_WIDTH_MAX + 20, 'height': 1700},
        'device_scale_factor': 2,
        'image_format': 'png',
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                remote_url,
                json=payload,
                headers={'Content-Type': 'application/json'},
            )
        elapsed = time.monotonic() - start_time
        if response.status_code != 200:
            logger.warning(
                f'[鹿签日历] 外置渲染失败，状态码: {response.status_code}, '
                f'耗时: {elapsed:.2f}s, 响应: {response.text[:300]}'
            )
            return None
        if not response.content:
            logger.warning(f'[鹿签日历] 外置渲染返回空内容，耗时: {elapsed:.2f}s')
            return None
        logger.info(
            f'[鹿签日历] 外置渲染成功，耗时: {elapsed:.2f}s，'
            f'HTML大小: {len(html_content) / 1024:.1f}KB，图片大小: {len(response.content)} bytes'
        )
        return response.content
    except httpx.TimeoutException:
        elapsed = time.monotonic() - start_time
        logger.warning(f'[鹿签日历] 外置渲染超时 ({elapsed:.2f}s)，回退本地渲染')
        return None
    except Exception as e:
        elapsed = time.monotonic() - start_time
        logger.warning(f'[鹿签日历] 外置渲染异常 ({elapsed:.2f}s): {e}，回退本地渲染')
        return None


def _is_master(ev: Event) -> bool:
    masters = core_config.get_config('masters') or []
    return str(ev.user_id) in {str(m) for m in masters}


@sv.on_command('🦌满', block=True)
async def handle_sign_full(bot: Bot, ev: Event):
    if not _is_master(ev):
        await bot.send('只有 bot 主人可以使用此命令')
        return

    async with _STATE_LOCK:
        state = _load_state()
        user_key = _get_user_key(ev)
        month_key = _get_month_key()
        now = datetime.now()
        days_in_month = calendar.monthrange(now.year, now.month)[1]

        user_data = state.setdefault(user_key, {})
        user_data[month_key] = list(range(1, days_in_month + 1))
        context = _build_calendar_context(ev, state, advance_background=True)
        _save_state(state)

    image = await _render_calendar(context)
    if image:
        await bot.send(image)
    else:
        await bot.send(f'已一键签满本月全部 {days_in_month} 天')


@sv.on_command('🦌', block=True)
@sv.on_command('鹿', block=True)
async def handle_sign(bot: Bot, ev: Event):
    async with _STATE_LOCK:
        state = _load_state()
        user_key = _get_user_key(ev)
        month_key = _get_month_key()
        today = _get_today_day()

        signed_days = _get_user_signs(state, user_key, month_key)
        if today in signed_days:
            already_signed = True
        else:
            already_signed = False
            _do_sign(state, user_key, month_key, today)
            signed_days = _get_user_signs(state, user_key, month_key)
        context = _build_calendar_context(ev, state, advance_background=True)
        _save_state(state)

    image = await _render_calendar(context)
    if image:
        await bot.send(image)
    else:
        if already_signed:
            await bot.send(f'今天已经签到过了！本月已签到 {len(signed_days)} 天')
        else:
            await bot.send(f'签到成功！本月已签到 {len(signed_days)} 天')


@sv.on_command('🦌补签', block=True)
@sv.on_command('补签', block=True)
async def handle_make_up_sign(bot: Bot, ev: Event):
    text = (getattr(ev, 'text', '') or '').strip()
    keyword = text
    for prefix in ('🦌补签', '补签'):
        if keyword.startswith(prefix):
            keyword = keyword[len(prefix):].strip()
            break

    if not keyword:
        await bot.send('请指定要补签的日期，格式：🦌补签 <日数>，例如：🦌补签 3')
        return

    m = re.search(r'(\d{1,2})', keyword)
    if not m:
        await bot.send('补签格式无效，请输入数字日期，例如：🦌补签 3')
        return

    day = int(m.group(1))
    now = datetime.now()
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    if day < 1 or day > days_in_month:
        await bot.send(f'无效日期，请输入本月 1 到 {days_in_month} 的日期')
        return
    if day > now.day:
        await bot.send('补签只能补当天或之前的日期')
        return

    async with _STATE_LOCK:
        state = _load_state()
        user_key = _get_user_key(ev)
        month_key = _get_month_key()
        signed_days = _get_user_signs(state, user_key, month_key)
        if day in signed_days:
            await bot.send(f'你已经补签过 {day} 日了，本月已签到 {len(signed_days)} 天')
            return
        _do_sign(state, user_key, month_key, day)
        signed_days = _get_user_signs(state, user_key, month_key)
        context = _build_calendar_context(ev, state, advance_background=True)
        _save_state(state)

    image = await _render_calendar(context)
    if image:
        await bot.send(image)
    else:
        await bot.send(f'补签成功！已补签 {day} 日，本月已签到 {len(signed_days)} 天')


@sv.on_command('/签到日历', block=True)
@sv.on_command('签到日历', block=True)
@sv.on_command('🦌日历', block=True)
async def handle_calendar(bot: Bot, ev: Event):
    async with _STATE_LOCK:
        state = _load_state()
        context = _build_calendar_context(ev, state, advance_background=True)
        _save_state(state)
    image = await _render_calendar(context)
    if image:
        await bot.send(image)
    else:
        user_key = _get_user_key(ev)
        month_key = _get_month_key()
        signed_days = _get_user_signs(state, user_key, month_key)
        now = datetime.now()
        await bot.send(
            f'{now.year}-{now.month:02d} 签到日历\n'
            f'已签到: {", ".join(str(d) for d in signed_days) if signed_days else "无"}\n'
            f'本月共签到 {len(signed_days)} 天'
        )
