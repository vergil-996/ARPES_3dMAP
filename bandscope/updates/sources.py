# -*- coding: utf-8 -*-
"""更新源抽象与来源标识。

BandScope 支持多个更新源（目前：GitHub Release 与 MatElab 实验记录本）。
各源客户端只要实现 :class:`UpdateSource` 协议即可被
:class:`~bandscope.updates.update_controller.UpdateController` 统一驱动；
发布数据一律复用 ``update_service`` 里的 ``ReleaseInfo`` / ``ReleaseAsset`` /
``UpdateCheckResult``，界面层不关心来源差异。

本模块不导入 Qt，也不导入具体源实现，避免循环依赖。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Protocol, Tuple

from bandscope.updates.update_service import ReleaseAsset, UpdateCheckResult

#: 更新源标识与 QSettings 键。
SOURCE_GITHUB = "github"
SOURCE_MATELAB = "matelab"
SOURCE_SETTING = "updates/source"
DEFAULT_SOURCE = SOURCE_GITHUB

#: 右键菜单展示用的来源名称。
SOURCE_LABELS = {
    SOURCE_GITHUB: "GitHub（默认）",
    SOURCE_MATELAB: "MatElab 实验记录本（所内）",
}


def normalize_source(value) -> str:
    """把设置值归一化成已知更新源；未知值回退默认源。"""
    text = str(value or "").strip().lower()
    return text if text in SOURCE_LABELS else DEFAULT_SOURCE


class UpdateSource(Protocol):
    """更新源客户端协议：``GitHubReleaseClient`` 与 ``MatelabReleaseClient`` 共同实现。"""

    def check_for_update(self, current_version: str, flavor: str) -> UpdateCheckResult:
        """查询最新正式版并给出当前 flavor 的安装包（有更新时）。"""
        ...

    def download_installer(
        self,
        asset: ReleaseAsset,
        destination_dir=None,
        *,
        progress: Optional[Callable[[int, int], None]] = None,
    ) -> Tuple[Path, str]:
        """下载安装包并校验大小与 SHA-256；返回 ``(路径, 实际摘要)``。"""
        ...
