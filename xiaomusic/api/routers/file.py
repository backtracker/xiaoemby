"""文件操作路由"""

import os
import shutil
from fastapi import APIRouter, Depends
from xiaomusic.api.dependencies import config, log

router = APIRouter()


@router.post("/xiaoemby/file/cleantempdir")
async def cleantempdir():
    try:
        temp_dir = config.temp_dir
        if not os.path.exists(temp_dir):
            log.info(f"临时目录不存在: {temp_dir}")
            # 目录不存在时也创建，保持目录结构统一
            os.makedirs(temp_dir, exist_ok=True)
            log.info(f"已创建临时目录: {temp_dir}")
            return

        # 递归删除整个临时目录（包括目录内所有文件/子目录）
        shutil.rmtree(temp_dir)
        log.debug(f"已删除临时目录: {temp_dir}")

        # 重新创建空的临时目录
        os.makedirs(temp_dir, exist_ok=True)
        log.info(f"已重新创建临时目录: {temp_dir}")

        log.info("定时清理临时文件完成，已删除并重建临时目录")
    except Exception as e:
        log.exception(f"清理临时文件异常: {e}")

    log.info("clean_temp_dir ok")
    return {"ret": "OK"}
