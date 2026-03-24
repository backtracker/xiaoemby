"""系统管理路由"""
import asyncio
import json
import os
import io
import base64
import shutil
import tempfile
from dataclasses import asdict
from qrcode.main import QRCode
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from xiaomusic import __version__
from xiaomusic.api.dependencies import config, log, xiaomusic
from xiaomusic.utils.system_utils import deepcopy_data_no_sensitive_info, restart_xiaomusic
from xiaomusic.qrcode_login import MiJiaAPI
router = APIRouter()
auth_data_path = config.conf_path if config.conf_path else None
mi_jia_api = MiJiaAPI(auth_data_path=auth_data_path)

@router.get("/")
async def read_index():
    """首页"""
    folder = os.path.dirname(
        os.path.dirname(os.path.dirname(__file__))
    )  # xiaomusic 目录
    return FileResponse(f"{folder}/static/index.html")

@router.get("/xiaoemby/get_qrcode")
async def get_qrcode():
    """生成小米账号扫码登录用二维码，返回 base64 图片 URL。"""
    try:
        qrcode_data = mi_jia_api.get_qrcode()
        # 已登录时 get_qrcode 返回 False，无需扫码
        if qrcode_data is False:
            return {
                "success": True,
                "already_logged_in": True,
                "qrcode_url": "",
                "message": "已登录，无需扫码",
            }

        # 优先使用小米返回的官方二维码图片 URL，与扫码内容一致且最可靠
        print(qrcode_data)
        if qrcode_data.get("qr"):
            qrcode_url = qrcode_data["qr"]
        else:
            # 无 qr 时用 loginUrl 本地生成二维码图
            qr = QRCode(version=1, box_size=8, border=2)
            qr.add_data(qrcode_data["loginUrl"])
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = io.BytesIO()
            img.save(buf, "PNG")
            buf.seek(0)
            b64 = base64.b64encode(buf.read()).decode("ascii")
            qrcode_url = f"data:image/png;base64,{b64}"
        # 返回二维码的同时，在后台启动 get_logint_status，不阻塞本次响应
        asyncio.create_task(get_logint_status(qrcode_data["lp"]))
        return {
            "success": True,
            "qrcode_url": qrcode_url,
            "status_url": qrcode_data.get("lp", ""),
            "expire_seconds": config.qrcode_timeout,
        }
    except Exception as e:
        log.exception("get_qrcode failed: %s", e)
        return {"success": False, "message": str(e)}


async def get_logint_status(lp: str):
    """轮询获取扫码登录状态"""
    try:
        await asyncio.to_thread(mi_jia_api.get_logint_status, lp)
    except ValueError as e:
        log.exception("get_logint_status failed: %s", e)

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
        await xiaomusic.saveconfig(data)
        return "save success"
    except json.JSONDecodeError as err:
        raise HTTPException(status_code=400, detail="Invalid JSON") from err


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

        # 更新配置
        config_obj.update_config(data)

        # 保存配置到文件
        xiaomusic.save_cur_config()

        return {"success": True, "message": "Configuration updated successfully"}
    except json.JSONDecodeError as err:
        raise HTTPException(status_code=400, detail="Invalid JSON") from err
    except Exception as err:
        log.error(f"Error updating configuration: {err}")
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






