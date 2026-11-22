"""系统管理路由"""
import json
import os
import shutil
import tempfile
from dataclasses import asdict
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from xiaomusic import __version__
from xiaomusic.api.dependencies import config, log, xiaomusic
from xiaomusic.utils.system_utils import deepcopy_data_no_sensitive_info
router = APIRouter()

@router.get("/")
async def read_index():
    """首页"""
    folder = os.path.dirname(
        os.path.dirname(os.path.dirname(__file__))
    )  # xiaomusic 目录
    return FileResponse(f"{folder}/static/index.html")

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
        if data.get("llm_api_key") == "******" or data.get("llm_api_key", "") == "":
            data["llm_api_key"] = config_obj.llm_api_key
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
        if "llm_api_key" in data and (
            data["llm_api_key"] == "******" or data["llm_api_key"] == ""
        ):
            data["llm_api_key"] = config_obj.llm_api_key

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






