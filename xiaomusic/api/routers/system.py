"""系统管理路由"""
import asyncio
import json
import os
import shutil
import tempfile
from dataclasses import asdict
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from starlette.background import BackgroundTask

from xiaomusic import __version__
from xiaomusic.api.dependencies import config, log, xiaomusic
from xiaomusic.utils.emby_utils import EmbyUtil
from xiaomusic.utils.library_mirror import LibraryMirror
from xiaomusic.utils.mirror_sync import sync_mirror
from xiaomusic.utils.system_utils import deepcopy_data_no_sensitive_info

router = APIRouter()

# 手动同步进度（内存态，仅用于页面展示；不含任何凭据或路径）。
_MIRROR_PROGRESS: dict[str, Any] = {
    "running": False,
    "phase": "idle",
    "kind": "",
    "page": 0,
    "pages": 0,
    "items": 0,
    "total": 0,
    "reason": "",
}


def _mirror_token(cfg) -> str:
    """只取服务端凭据：优先配置的管理员 key，其次运行中 Emby 客户端的 token。"""
    key = str(getattr(cfg, "emby_api_key", "") or "")
    if key:
        return key
    try:
        for name in ("emby_util", "emby"):
            getter = getattr(getattr(xiaomusic, name, None), "get_access_token", None)
            if callable(getter):
                token = str(getter() or "")
                if token:
                    return token
    except Exception:  # 客户端尚未初始化时退回到未配置状态
        pass
    return ""

@router.get("/")
async def read_index():
    """首页"""
    folder = os.path.dirname(
        os.path.dirname(os.path.dirname(__file__))
    )  # xiaomusic 目录
    return FileResponse(f"{folder}/static/index.html")


def _short_page(relative_path: str) -> HTMLResponse:
    """短路由返回页面，并注入 base 让相对静态资源仍从原目录加载。"""
    folder = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    with open(os.path.join(folder, "static", relative_path), encoding="utf-8") as f:
        html = f.read()
    if "<base " not in html:
        html = html.replace("<head>", '<head>\n    <base href="/static/default/">', 1)
    return HTMLResponse(html)


@router.get("/player", include_in_schema=False)
async def player_page():
    """播放器页面（短地址）"""
    return _short_page("default/index.html")


@router.get("/settings", include_in_schema=False)
async def settings_page():
    """设置页面（短地址）"""
    return _short_page("default/setting.html")


@router.get("/getversion")
def getversion():
    """获取版本"""
    log.debug("getversion %s", __version__)
    return {"version": __version__}


@router.get("/getsetting")
async def getsetting(need_device_list: bool = False):
    """获取设置"""
    config_data = xiaomusic.getconfig()
    data = asdict(config_data)
    data["password"] = "******"
    data["emby_password"] = "******"
    data["emby_api_key"] = "******"
    data["llm_api_key"] = "******"
    if need_device_list:
        device_list = await xiaomusic.getalldevices()
        log.info(f"getsetting device_list: {device_list}")
        data["device_list"] = device_list
    return data


@router.post("/savesetting")
async def savesetting(request: Request):
    """保存设置"""
    try:
        data_json = await request.body()
        data = json.loads(data_json.decode("utf-8"))
        debug_data = deepcopy_data_no_sensitive_info(data)
        log.info(f"saveconfig: {debug_data}")
        config_obj = xiaomusic.getconfig()
        if data.get("password") == "******" or data.get("password", "") == "":
            data["password"] = config_obj.password
        if data.get("emby_password") == "******" or data.get("emby_password", "") == "":
            data["emby_password"] = config_obj.emby_password
        if data.get("emby_api_key") == "******" or data.get("emby_api_key", "") == "":
            data["emby_api_key"] = config_obj.emby_api_key
        if data.get("llm_api_key") == "******" or data.get("llm_api_key", "") == "":
            data["llm_api_key"] = config_obj.llm_api_key
        await xiaomusic.saveconfig(data)
        return "save success"
    except json.JSONDecodeError as err:
        raise HTTPException(status_code=400, detail="Invalid JSON") from err


@router.post("/xiaoemby/system/sync_mirror")
async def sync_mirror_now(request: Request):
    """手动触发本地曲库镜像同步；只回传摘要，不回显主机、路径或凭据。"""
    try:
        data = json.loads((await request.body()).decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError) as err:
        raise HTTPException(status_code=400, detail="Invalid JSON") from err
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    kind = str(data.get("kind") or "auto").strip().lower()
    if kind not in {"auto", "full", "daily", "weekly"}:
        raise HTTPException(status_code=400, detail="Invalid kind")

    cfg = xiaomusic.getconfig()
    _MIRROR_PROGRESS.update(
        running=True, phase="start", kind=kind, page=0, pages=0,
        items=0, total=0, reason=""
    )

    def report(update: dict[str, Any]) -> None:
        _MIRROR_PROGRESS.update(update)

    try:
        result = await asyncio.to_thread(
            sync_mirror, cfg, token=_mirror_token(cfg), kind=kind, progress=report
        )
    except Exception as err:
        log.warning(f"手动镜像同步异常: {type(err).__name__}")
        result = {"ok": False, "skipped": False, "reason": type(err).__name__, "alerts": []}
    finally:
        _MIRROR_PROGRESS["running"] = False
        if result.get("ok"):
            _MIRROR_PROGRESS["phase"] = "done"
        elif result.get("skipped"):
            _MIRROR_PROGRESS["phase"] = "idle"
        else:
            _MIRROR_PROGRESS["phase"] = "failed"

    return {
        "ok": bool(result.get("ok")),
        "skipped": bool(result.get("skipped")),
        "reason": result.get("reason", ""),
        "kind": result.get("kind", kind),
        "version": int(result.get("version") or 0),
        "tracks": int(result.get("items") or 0),
        "alerts": list(result.get("alerts") or []),
    }


@router.get("/xiaoemby/system/mirror_sync_progress")
async def mirror_sync_progress():
    """查询手动同步进度（只读、无凭据）。"""
    return dict(_MIRROR_PROGRESS)


@router.get("/xiaoemby/system/mirror_status")
async def mirror_status():
    """只读镜像版本/曲目数/最近同步日；未配置或文件不存在时不创建文件。"""
    cfg = xiaomusic.getconfig()
    db_path = str(getattr(cfg, "mirror_db_path", "") or "")
    empty = {"configured": bool(db_path), "version": 0, "tracks": 0, "last_sync": None}
    if not db_path or not os.path.exists(db_path):
        return empty

    def read_status():
        mirror = LibraryMirror(db_path)
        try:
            info = mirror.version_info()
            day = info.get("last_sync_day")
            last_sync = date.fromordinal(int(day)).isoformat() if day else None
            return {
                "configured": True,
                "version": int(info.get("version") or 0),
                "tracks": mirror.track_count(),
                "last_sync": last_sync,
            }
        finally:
            mirror.close()

    try:
        return await asyncio.to_thread(read_status)
    except Exception as err:
        log.warning(f"读取镜像状态失败: {type(err).__name__}")
        return {**empty, "reason": type(err).__name__}


@router.post("/xiaoemby/system/test_emby_login")
async def test_emby_login(request: Request):
    """测试 Emby 用户名密码能否登录（不保存配置）"""
    try:
        data = json.loads((await request.body()).decode("utf-8"))
        config_obj = xiaomusic.getconfig()
        host = data.get("host") or config_obj.emby_host
        username = data.get("username") or config_obj.emby_username
        password = data.get("password") or ""
        if password in ("******", ""):
            password = config_obj.emby_password

        if not host or not username or not password:
            return {"success": False, "message": "请先填写 Emby 服务器地址、用户名和密码"}

        util = EmbyUtil(
            host=host,
            user_id="",
            log=log,
            username=username,
            password=password,
        )
        if util.login():
            return {
                "success": True,
                "message": f"登录成功：{username}",
                "userId": (util._auth_user_id or ""),
            }
        return {"success": False, "message": "登录失败，请检查用户名或密码"}
    except Exception as err:
        log.error(f"Test Emby login failed: {err}")
        return {"success": False, "message": f"测试异常：{err}"}


@router.post("/xiaoemby/system/modifiysetting")
async def modifiysetting(request: Request):
    """修改部分设置"""
    try:
        data_json = await request.body()
        data = json.loads(data_json.decode("utf-8"))
        debug_data = deepcopy_data_no_sensitive_info(data)
        log.info(f"modifiysetting: {debug_data}")

        config_obj = xiaomusic.getconfig()

        # 处理密码字段，如果是 ****** 或空字符串则保持原值
        if "password" in data and (
            data["password"] == "******" or data["password"] == ""
        ):
            data["password"] = config_obj.password
        if "llm_api_key" in data and (
            data["llm_api_key"] == "******" or data["llm_api_key"] == ""
        ):
            data["llm_api_key"] = config_obj.llm_api_key
        if "emby_password" in data and (
            data["emby_password"] == "******" or data["emby_password"] == ""
        ):
            data["emby_password"] = config_obj.emby_password
        if "emby_api_key" in data and (
            data["emby_api_key"] == "******" or data["emby_api_key"] == ""
        ):
            data["emby_api_key"] = config_obj.emby_api_key

        # 更新配置
        config_obj.update_config(data)

        # 保存配置到文件
        xiaomusic.save_cur_config()

        # 重新加载定时任务
        if "crontab_json" in data:
            log.info("crontab_json已修改，重新加载定时任务")
            xiaomusic.crontab.reload_config(xiaomusic)

        return {"success": True, "message": "Configuration updated successfully"}
    except json.JSONDecodeError as err:
        raise HTTPException(status_code=400, detail="Invalid JSON") from err
    except Exception as err:
        log.error(f"Error updating configuration: {err}")
        raise HTTPException(status_code=500, detail=str(err)) from err


@router.get("/xiaoemby/system/gettimerrules")
async def gettimerrules():
    """获取所有定时规则"""
    try:
        # 获取当前配置中的定时规则
        config_data = xiaomusic.getconfig()
        crontab_json = config_data.crontab_json

        if not crontab_json:
            return {"rules": []}

        rules = json.loads(crontab_json)
        return {"rules": rules}
    except Exception as err:
        log.error(f"Error getting timer rules: {err}")
        return {"rules": []}


@router.post("/xiaoemby/system/addtimerrule")
async def addtimerrule(request: Request):
    """添加或更新定时规则"""
    try:
        data_json = await request.body()
        rule_data = json.loads(data_json.decode("utf-8"))

        # 获取当前配置中的定时规则
        config_data = xiaomusic.getconfig()
        crontab_json = config_data.crontab_json

        rules = []
        if crontab_json:
            rules = json.loads(crontab_json)

        # 确保name字段正确设置
        if "name" not in rule_data:
            rule_data["name"] = "set_pull_ask"

        # 检查是否提供了ID
        if "id" in rule_data:
            # 更新现有规则
            rule_id = rule_data["id"]
            updated = False
            for i, rule in enumerate(rules):
                if rule.get("id") == rule_id:
                    rules[i] = rule_data
                    updated = True
                    break

            if not updated:
                # 如果没有找到对应ID的规则，作为新规则处理
                rules.append(rule_data)

            # 调试：打印更新后的规则列表
            log.info(f"更新后的定时规则: {rules}")
        else:
            # 为新规则生成ID
            import uuid
            rule_data["id"] = str(uuid.uuid4())

            # 添加新规则
            rules.append(rule_data)

            # 调试：打印添加后的规则列表
            log.info(f"添加后的定时规则: {rules}")

        # 更新配置
        update_data = asdict(config_data)
        update_data["crontab_json"] = json.dumps(rules)

        # 保存配置
        await xiaomusic.saveconfig(update_data)

        return {"success": True, "message": "Timer rule added successfully", "rules": rules}
    except json.JSONDecodeError as err:
        log.error(f"Invalid JSON: {err}")
        raise HTTPException(status_code=400, detail="Invalid JSON") from err
    except Exception as err:
        log.error(f"Error adding timer rule: {err}")
        raise HTTPException(status_code=500, detail=str(err)) from err


@router.post("/xiaoemby/system/deletetimerrule")
async def deletetimerrule(request: Request):
    """删除定时规则"""
    try:
        data_json = await request.body()
        delete_data = json.loads(data_json.decode("utf-8"))
        rule_id = delete_data.get("id")

        if not rule_id:
            raise HTTPException(status_code=400, detail="Missing rule ID")

        # 获取当前配置中的定时规则
        config_data = xiaomusic.getconfig()
        crontab_json = config_data.crontab_json

        rules = []
        if crontab_json:
            rules = json.loads(crontab_json)

        # 调试：打印当前规则和要删除的ID
        log.info(f"当前规则: {rules}")
        log.info(f"要删除的规则ID: {rule_id}")

        # 过滤掉要删除的规则
        new_rules = [rule for rule in rules if rule.get("id") != rule_id]

        # 调试：打印删除后的规则
        log.info(f"删除后的规则: {new_rules}")

        # 更新配置
        update_data = asdict(config_data)
        update_data["crontab_json"] = json.dumps(new_rules)

        # 保存配置
        await xiaomusic.saveconfig(update_data)

        return {"success": True, "message": "Timer rule deleted successfully", "rules": new_rules}
    except json.JSONDecodeError as err:
        log.error(f"Invalid JSON: {err}")
        raise HTTPException(status_code=400, detail="Invalid JSON") from err
    except Exception as err:
        log.error(f"Error deleting timer rule: {err}")
        raise HTTPException(status_code=500, detail=str(err)) from err


@router.get("/downloadlog")
def downloadlog():
    """下载日志"""
    file_path = config.log_file
    if os.path.exists(file_path):
        # 创建一个临时文件来保存日志的快照
        temp_file = tempfile.NamedTemporaryFile(delete=False)
        try:
            with open(file_path, "rb") as f:
                shutil.copyfileobj(f, temp_file)
            temp_file.close()

            # 使用BackgroundTask在响应发送完毕后删除临时文件
            def cleanup_temp_file(tmp_file_path):
                os.remove(tmp_file_path)

            background_task = BackgroundTask(cleanup_temp_file, temp_file.name)
            # 使用配置中的日志文件名作为下载文件名
            log_filename = os.path.basename(file_path)
            return FileResponse(
                temp_file.name,
                media_type="text/plain",
                filename=log_filename,
                background=background_task,
            )
        except Exception as e:
            os.remove(temp_file.name)
            raise HTTPException(
                status_code=500, detail="Error capturing log file"
            ) from e
    else:
        return {"message": "File not found."}






