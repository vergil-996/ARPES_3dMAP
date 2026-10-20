"""多数据源工作区：一次成功加载对应一份独立会话。

本模块只描述"有哪些数据源、谁处于活动状态、页面属于谁"，不创建窗口、不读
Qt。窗口侧的状态（数组、页面树、处理版本、相机……）由应用层按会话整体换入
换出，这里只保存引用。

三个身份不要混用：

- ``session_id``：独立工作区的标识。重复加载同一个 NPZ 也必须是两个会话，
  所以它和文件摘要无关。
- ``source_identity`` / 配方来源 ID：数据内容与计算来源的标识，仍由
  :mod:`bandscope.core.view_recipe` 维护；同一文件的两个会话共享内容身份，
  但不共享会话。
- ``scope_id``：数据域在**会话内**的局部标识（``full``、``roi-1``……）。跨源
  的缓存与任务归属用 ``(session_id, scope_id)`` 组合键区分。
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

#: 入口类型：原始 NPZ、经转换的 MAT、视图复现文件。
ENTRY_NPZ = "npz"
ENTRY_MAT = "mat"
ENTRY_VIEW_RECIPE = "view_recipe"

#: 复现文件去掉的完整后缀（不是普通的 ``.json``）。
VIEW_RECIPE_SUFFIX = ".bsview.json"

#: 没有别名时的兜底名称。
FALLBACK_ALIAS = "数据"


def new_session_id() -> str:
    """新的工作区标识；与数据内容、文件摘要无关。"""
    return uuid.uuid4().hex


def default_alias(path, *, entry_kind: str = ENTRY_NPZ) -> str:
    """文件名去掉扩展名：NPZ/MAT 去一个后缀，复现文件去掉完整后缀。"""
    name = os.path.basename(str(path or ""))
    if not name:
        return FALLBACK_ALIAS
    lower = name.lower()
    if entry_kind == ENTRY_VIEW_RECIPE or lower.endswith(VIEW_RECIPE_SUFFIX):
        if lower.endswith(VIEW_RECIPE_SUFFIX):
            name = name[: -len(VIEW_RECIPE_SUFFIX)]
        else:
            name = os.path.splitext(name)[0]
    else:
        name = os.path.splitext(name)[0]
    return name.strip() or FALLBACK_ALIAS


def unique_alias(existing: Iterable[str], base: str, *, exclude: Optional[str] = None) -> str:
    """重名时追加 ``_2``、``_3``…；空名称退回兜底名。"""
    base = str(base or "").strip() or FALLBACK_ALIAS
    taken = {str(item) for item in existing if item is not None and str(item) != exclude}
    if base not in taken:
        return base
    suffix = 2
    while f"{base}_{suffix}" in taken:
        suffix += 1
    return f"{base}_{suffix}"


@dataclass
class SourceSession:
    """一份已加载数据的独立工作区。"""

    session_id: str
    entry_kind: str
    entry_path: str
    primary_path: str
    alias: str
    #: 用户改名前的默认别名；空名称、Esc 撤销都回到它。
    alias_base: str
    #: 应用层挂上来的状态包（数组按引用保存，不做深拷贝）。
    state: Dict[str, object] = field(default_factory=dict)
    #: 该工作区当前登记的页面（含主页），顺序为登记顺序。
    page_ids: List[str] = field(default_factory=list)
    #: 切回时应该激活的页面。
    target_page_id: Optional[str] = None
    home_page_id: Optional[str] = None

    @property
    def is_view_recipe(self) -> bool:
        return self.entry_kind == ENTRY_VIEW_RECIPE

    def contains_page(self, page_id) -> bool:
        return page_id in self.page_ids

    def describe(self) -> Dict[str, object]:
        """给状态栏/提示用的只读摘要。"""
        return {
            "session_id": self.session_id,
            "alias": self.alias,
            "entry_kind": self.entry_kind,
            "entry_path": self.entry_path,
            "primary_path": self.primary_path,
            "page_count": len(self.page_ids),
        }


class SourceSessionManager:
    """会话集合与活动源；不持有窗口，也不做任何渲染。

    关闭采用两段式：``mark_closing`` 先让该源在清理期间拒绝被激活，清理完成后
    再 ``close`` 把它摘掉并选好下一个活动源。这样页面逐页退订时不会误把某个
    正在关闭的源切回来。
    """

    def __init__(self) -> None:
        self._sessions: Dict[str, SourceSession] = {}
        self._order: List[str] = []
        self._closing: set = set()
        self._page_owner: Dict[str, str] = {}
        self._active_id: Optional[str] = None

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self._order)

    def __iter__(self):
        for session_id in list(self._order):
            session = self._sessions.get(session_id)
            if session is not None:
                yield session

    def session_ids(self) -> List[str]:
        return list(self._order)

    def sessions(self) -> List[SourceSession]:
        return list(self)

    def get(self, session_id) -> Optional[SourceSession]:
        if session_id is None:
            return None
        return self._sessions.get(str(session_id))

    def has_session(self, session_id) -> bool:
        return str(session_id) in self._sessions if session_id is not None else False

    @property
    def active_id(self) -> Optional[str]:
        return self._active_id

    def active(self) -> Optional[SourceSession]:
        return self._sessions.get(self._active_id) if self._active_id else None

    def is_active(self, session_id) -> bool:
        return session_id is not None and str(session_id) == self._active_id

    def aliases(self) -> List[str]:
        return [session.alias for session in self]

    def alias_for(self, session_id) -> str:
        session = self.get(session_id)
        return session.alias if session is not None else ""

    def source_items(self) -> List[Tuple[str, str]]:
        """下拉框条目：``(session_id, 别名)``，顺序即打开顺序。"""
        return [(session.session_id, session.alias) for session in self]

    # ------------------------------------------------------------------
    # 新增与激活
    # ------------------------------------------------------------------
    def add(
        self,
        *,
        entry_kind: str,
        entry_path,
        primary_path=None,
        alias=None,
        session_id=None,
        state=None,
        activate=True,
    ) -> SourceSession:
        """登记新会话；别名自动去重，不做任何覆盖或合并。"""
        entry_path = str(entry_path or "")
        primary_path = str(primary_path or entry_path)
        base = default_alias(entry_path, entry_kind=entry_kind)
        requested = str(alias).strip() if alias is not None else base
        final_alias = unique_alias(self.aliases(), requested or base)
        session = SourceSession(
            session_id=str(session_id or new_session_id()),
            entry_kind=str(entry_kind),
            entry_path=entry_path,
            primary_path=primary_path,
            alias=final_alias,
            alias_base=base,
            state=dict(state or {}),
        )
        if session.session_id in self._sessions:
            raise ValueError(f"数据源标识重复：{session.session_id}")
        self._sessions[session.session_id] = session
        self._order.append(session.session_id)
        if activate or self._active_id is None:
            self._active_id = session.session_id
        return session

    def set_active(self, session_id) -> bool:
        """切换活动源；返回是否真的发生变化。"""
        session = self.get(session_id)
        if session is None or session.session_id in self._closing:
            return False
        if session.session_id == self._active_id:
            return False
        self._active_id = session.session_id
        return True

    def activate_fallback(self, session_id) -> bool:
        """源级恢复：允许把活动源设回 ``session_id``（含已关闭清理结束的源）。"""
        session = self.get(session_id)
        if session is None or session.session_id in self._closing:
            return False
        changed = self._active_id != session.session_id
        self._active_id = session.session_id
        return changed

    # ------------------------------------------------------------------
    # 别名
    # ------------------------------------------------------------------
    def set_alias(self, session_id, alias) -> str:
        """落地别名：空名称恢复默认名，重名自动追加序号。"""
        session = self.get(session_id)
        if session is None:
            return ""
        text = str(alias or "").strip()
        if not text:
            text = session.alias_base
        final_alias = unique_alias(self.aliases(), text, exclude=session.alias)
        session.alias = final_alias
        return final_alias

    # ------------------------------------------------------------------
    # 页面归属
    # ------------------------------------------------------------------
    def register_pages(self, session_id, page_ids: Iterable[str], *, home_page_id=None) -> None:
        """登记某个会话持有的页面；页面 ID 必须全局唯一。"""
        session = self.get(session_id)
        if session is None:
            return
        registered: List[str] = []
        conflicts: List[str] = []
        for page_id in page_ids:
            page_id = str(page_id)
            owner = self._page_owner.get(page_id)
            if owner is not None and owner != session.session_id:
                conflicts.append(page_id)
            registered.append(page_id)
        if conflicts:
            # 先整体校验再落地，避免失败时留下半套归属记录。
            raise ValueError(f"页面 ID 已被其他数据源占用：{conflicts[0]}")
        for page_id in registered:
            self._page_owner[page_id] = session.session_id
        session.page_ids = registered
        if home_page_id is not None:
            session.home_page_id = str(home_page_id)

    def register_page(self, session_id, page_id) -> None:
        session = self.get(session_id)
        if session is None:
            return
        page_id = str(page_id)
        owner = self._page_owner.get(page_id)
        if owner is not None and owner != session.session_id:
            raise ValueError(f"页面 ID 已被其他数据源占用：{page_id}")
        self._page_owner[page_id] = session.session_id
        if page_id not in session.page_ids:
            session.page_ids.append(page_id)

    def unregister_page(self, page_id) -> None:
        page_id = str(page_id)
        owner = self._page_owner.pop(page_id, None)
        if owner is not None:
            session = self._sessions.get(owner)
            if session is not None and page_id in session.page_ids:
                session.page_ids.remove(page_id)

    def owner_of(self, page_id) -> Optional[str]:
        if page_id is None:
            return None
        return self._page_owner.get(str(page_id))

    def owns_page(self, session_id, page_id) -> bool:
        return self.owner_of(page_id) == (str(session_id) if session_id is not None else None)

    def session_for_page(self, page_id) -> Optional[SourceSession]:
        return self.get(self.owner_of(page_id))

    def page_owner_map(self) -> Dict[str, str]:
        return dict(self._page_owner)

    # ------------------------------------------------------------------
    # 关闭
    # ------------------------------------------------------------------
    def mark_closing(self, session_id) -> None:
        if session_id is not None:
            self._closing.add(str(session_id))

    def is_closing(self, session_id) -> bool:
        return session_id is not None and str(session_id) in self._closing

    def unmark_closing(self, session_id) -> None:
        if session_id is not None:
            self._closing.discard(str(session_id))

    def neighbor_id(self, session_id) -> Optional[str]:
        """关闭后的选择顺序：前一份，没有前一份则后一份。"""
        session_id = str(session_id)
        if session_id not in self._order:
            return None
        index = self._order.index(session_id)
        if index > 0:
            return self._order[index - 1]
        if index + 1 < len(self._order):
            return self._order[index + 1]
        return None

    def close(self, session_id) -> Tuple[Optional[SourceSession], Optional[str]]:
        """摘除会话并落到下一个活动源；返回 ``(关闭的会话, 新的活动源)``。"""
        session_id = str(session_id)
        session = self._sessions.pop(session_id, None)
        if session is None:
            self._closing.discard(session_id)
            return None, self._active_id
        successor = self.neighbor_id(session_id)
        self._order = [item for item in self._order if item != session_id]
        for page_id in list(session.page_ids):
            self.unregister_page(page_id)
        session.page_ids = []
        self._closing.discard(session_id)
        if self._active_id == session_id:
            self._active_id = successor if successor in self._sessions else None
        return session, self._active_id

    def close_all(self) -> List[SourceSession]:
        closed = []
        for session_id in self.session_ids():
            session, _ = self.close(session_id)
            if session is not None:
                closed.append(session)
        self._active_id = None
        return closed
