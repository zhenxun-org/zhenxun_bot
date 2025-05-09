import os
from pathlib import Path
import shutil

import aiofiles
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from zhenxun.utils._build_image import BuildImage

from ....base_model import Result, SystemFolderSize
from ....utils import authentication, get_system_disk
from .model import AddFile, DeleteFile, DirFile, RenameFile, SaveFile

router = APIRouter(prefix="/system")

IMAGE_TYPE = ["jpg", "jpeg", "png", "gif", "bmp", "webp", "svg"]


@router.get(
    "/get_dir_list",
    dependencies=[authentication()],
    response_model=Result[list[DirFile]],
    response_class=JSONResponse,
    description="获取文件列表",
)
async def _(path: str | None = None) -> Result[list[DirFile]]:
    base_path = Path(path) if path else Path()
    data_list = []
    for file in os.listdir(base_path):
        file_path = base_path / file
        is_image = any(file.endswith(f".{t}") for t in IMAGE_TYPE)
        data_list.append(
            DirFile(
                is_file=not file_path.is_dir(),
                is_image=is_image,
                name=file,
                parent=path,
                size=None if file_path.is_dir() else file_path.stat().st_size,
                mtime=file_path.stat().st_mtime,
            )
        )
    return Result.ok(data_list)


@router.get(
    "/get_resources_size",
    dependencies=[authentication()],
    response_model=Result[list[SystemFolderSize]],
    response_class=JSONResponse,
    description="获取文件列表",
)
async def _(full_path: str | None = None) -> Result[list[SystemFolderSize]]:
    return Result.ok(await get_system_disk(full_path))


@router.post(
    "/delete_file",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="删除文件",
)
async def _(param: DeleteFile) -> Result:
    path = Path(param.full_path)
    if not path or not path.exists():
        return Result.warning_("文件不存在...")
    try:
        path.unlink()
        return Result.ok("删除成功!")
    except Exception as e:
        return Result.warning_(f"删除失败: {e!s}")


@router.post(
    "/delete_folder",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="删除文件夹",
)
async def _(param: DeleteFile) -> Result:
    path = Path(param.full_path)
    if not path or not path.exists() or path.is_file():
        return Result.warning_("文件夹不存在...")
    try:
        shutil.rmtree(path.absolute())
        return Result.ok("删除成功!")
    except Exception as e:
        return Result.warning_(f"删除失败: {e!s}")


@router.post(
    "/rename_file",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="重命名文件",
)
async def _(param: RenameFile) -> Result:
    path = (
        (Path(param.parent) / param.old_name) if param.parent else Path(param.old_name)
    )
    if not path or not path.exists():
        return Result.warning_("文件不存在...")
    try:
        path.rename(path.parent / param.name)
        return Result.ok("重命名成功!")
    except Exception as e:
        return Result.warning_(f"重命名失败: {e!s}")


@router.post(
    "/rename_folder",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="重命名文件夹",
)
async def _(param: RenameFile) -> Result:
    path = (
        (Path(param.parent) / param.old_name) if param.parent else Path(param.old_name)
    )
    if not path or not path.exists() or path.is_file():
        return Result.warning_("文件夹不存在...")
    try:
        new_path = path.parent / param.name
        shutil.move(path.absolute(), new_path.absolute())
        return Result.ok("重命名成功!")
    except Exception as e:
        return Result.warning_(f"重命名失败: {e!s}")


@router.post(
    "/add_file",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="新建文件",
)
async def _(param: AddFile) -> Result:
    path = (Path(param.parent) / param.name) if param.parent else Path(param.name)
    if path.exists():
        return Result.warning_("文件已存在...")
    try:
        path.open("w")
        return Result.ok("新建文件成功!")
    except Exception as e:
        return Result.warning_(f"新建文件失败: {e!s}")


@router.post(
    "/add_folder",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="新建文件夹",
)
async def _(param: AddFile) -> Result:
    path = (Path(param.parent) / param.name) if param.parent else Path(param.name)
    if path.exists():
        return Result.warning_("文件夹已存在...")
    try:
        path.mkdir()
        return Result.ok("新建文件夹成功!")
    except Exception as e:
        return Result.warning_(f"新建文件夹失败: {e!s}")


@router.get(
    "/read_file",
    dependencies=[authentication()],
    response_model=Result[str],
    response_class=JSONResponse,
    description="读取文件",
)
async def _(full_path: str) -> Result:
    path = Path(full_path)
    if not path.exists():
        return Result.warning_("文件不存在...")
    try:
        text = path.read_text(encoding="utf-8")
        return Result.ok(text)
    except Exception as e:
        return Result.warning_(f"读取文件失败: {e!s}")


@router.post(
    "/save_file",
    dependencies=[authentication()],
    response_model=Result[str],
    response_class=JSONResponse,
    description="读取文件",
)
async def _(param: SaveFile) -> Result[str]:
    path = Path(param.full_path)
    try:
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(param.content)
        return Result.ok("更新成功!")
    except Exception as e:
        return Result.warning_(f"保存文件失败: {e!s}")


@router.get(
    "/get_image",
    dependencies=[authentication()],
    response_model=Result[str],
    response_class=JSONResponse,
    description="读取图片base64",
)
async def _(full_path: str) -> Result[str]:
    path = Path(full_path)
    if not path.exists():
        return Result.warning_("文件不存在...")
    try:
        return Result.ok(BuildImage.open(path).pic2bs4())
    except Exception as e:
        return Result.warning_(f"获取图片失败: {e!s}")


@router.get(
    "/ping",
    response_model=Result[str],
    response_class=JSONResponse,
    description="检查服务器状态",
)
async def _() -> Result[str]:
    return Result.ok("pong")
