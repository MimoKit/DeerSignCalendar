from __future__ import annotations

import base64
from pathlib import Path
from typing import Iterable

from gsuid_core.data_store import get_res_path

PLUGIN_NAME = 'DeerSignCalendar'

BASE_DIR = Path(__file__).parent
DATA_DIR = get_res_path(PLUGIN_NAME)

# 用户手动放置的背景图目录，随 GsCore data 走，不再写到插件代码目录。
BACKGROUND_DIR = DATA_DIR / 'sign-bj'

# 背景图参与 HTML/CSS 渲染，优先使用浏览器稳定支持的静态图片格式。
BACKGROUND_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp'}

_MIME_TYPES = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.webp': 'image/webp',
}


def ensure_data_dirs() -> None:
    """创建插件运行数据目录。"""
    BACKGROUND_DIR.mkdir(parents=True, exist_ok=True)


def _iter_images(root: Path, extensions: set[str]) -> tuple[Path, ...]:
    if not root.is_dir():
        return ()

    images = [
        path
        for path in root.rglob('*')
        if path.is_file() and path.suffix.lower() in extensions
    ]
    return tuple(sorted(images, key=lambda path: str(path).lower()))


def _unique_existing_dirs(candidates: Iterable[Path]) -> tuple[Path, ...]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        if not path.is_dir():
            continue
        try:
            key = str(path.resolve()).lower()
        except Exception:
            key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return tuple(result)


def _xwuid_panel_roots() -> tuple[Path, ...]:
    """参考今日老婆 local 模式，自动查找本机 XWUID 用户上传面板图目录。

    这里只读取 custom_role_pile，不再混用 resource/role_pile 默认立绘。
    """
    xwuid_data = get_res_path('XutheringWavesUID')
    candidates: list[Path] = [
        xwuid_data / 'custom_role_pile',
    ]

    # 兼容用户把插件仓库放在 gsuid_core 外层开发目录时的常见路径。
    for root in (Path.cwd(), BASE_DIR.parent, BASE_DIR.parent.parent):
        candidates.extend(
            [
                root / 'data' / 'XutheringWavesUID' / 'custom_role_pile',
                root / 'gsuid_core' / 'data' / 'XutheringWavesUID' / 'custom_role_pile',
            ]
        )

    return _unique_existing_dirs(candidates)


def get_background_image_paths() -> tuple[Path, ...]:
    """获取可用背景图。

    优先读取 data/DeerSignCalendar/sign-bj 下用户手动放入的背景图；
    如果该目录为空，则按今日老婆 local 模式读取本机 XWUID 用户上传面板图。
    """
    ensure_data_dirs()

    custom_images = _iter_images(BACKGROUND_DIR, BACKGROUND_EXTENSIONS)
    if custom_images:
        return custom_images

    panel_images: list[Path] = []
    for root in _xwuid_panel_roots():
        panel_images.extend(_iter_images(root, BACKGROUND_EXTENSIONS))
    return tuple(sorted(panel_images, key=lambda path: str(path).lower()))


def image_mime(path: Path) -> str:
    return _MIME_TYPES.get(path.suffix.lower(), 'image/jpeg')


def image_to_data_uri(path: Path) -> str:
    data = path.read_bytes()
    b64 = base64.b64encode(data).decode('ascii')
    return f'data:{image_mime(path)};base64,{b64}'
