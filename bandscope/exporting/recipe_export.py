"""Pair a frozen scientific recipe with the picture actually exported."""
from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path

from bandscope.core.view_recipe import RecipeError, ViewRecipeDocument, plain, sidecar_path


def publication_record(style, overrides, options, presentation=None):
    return plain({"style_id": style.style_id, "style_params": dict(style.params),
                  "overrides": overrides, "output": json.loads(options.to_json()),
                  "presentation": presentation.to_dict() if presentation is not None else None})


def export_pair(snapshot, style, overrides, options, path, *, presentation=None, render=None):
    if snapshot.view_recipe is None:
        raise RecipeError(snapshot.recipe_error or "此预览缺少原始数据来源，请重新加载数据并更新预览。")
    if render is None:
        from bandscope.exporting.publication_export import render_and_save
        render = render_and_save
    document = copy.deepcopy(snapshot.view_recipe)
    document["publication"] = publication_record(style, overrides, options, presentation)
    document["image_filename"] = Path(path).name
    content = ViewRecipeDocument(document).dumps()
    targets = [Path(path).resolve(), Path(sidecar_path(path)).resolve()]
    staged, backups, installed = [], {}, []
    try:
        for target in targets:
            fd, name = tempfile.mkstemp(prefix=".bandscope-", suffix=target.suffix, dir=target.parent)
            os.close(fd)
            staged.append(Path(name))
        kwargs = {"presentation": presentation} if presentation is not None else {}
        render(snapshot, style, overrides, options, str(staged[0]), **kwargs)
        with staged[1].open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # Move old files aside only after both new products are ready. This is
        # rollback-capable, not a claim of cross-file filesystem atomicity.
        for target in targets:
            if target.exists():
                fd, name = tempfile.mkstemp(prefix=".bandscope-backup-", dir=target.parent)
                os.close(fd)
                backup = Path(name)
                backup.unlink()
                os.replace(target, backup)
                backups[target] = backup
        for temporary, target in zip(staged, targets):
            os.replace(temporary, target)
            installed.append(target)
    except Exception as exc:
        failures = []
        for target in reversed(installed):
            try:
                target.unlink(missing_ok=True)
            except OSError as error:
                failures.append(str(error))
        for target, backup in backups.items():
            try:
                os.replace(backup, target)
            except OSError:
                failures.append(f"旧文件保留在 {backup}")
        if failures:
            raise RecipeError(f"配对导出失败：{exc}；回滚未完成：{'；'.join(failures)}") from exc
        raise
    else:
        for backup in backups.values():
            try:
                backup.unlink(missing_ok=True)
            except OSError:
                pass  # Both products are committed; cleanup cannot undo success.
    finally:
        for temporary in staged:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
