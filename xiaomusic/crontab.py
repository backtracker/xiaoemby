import json
import os
import shutil

from dataclasses import asdict
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError
import datetime


async def clean_temp_dir(config):
    """清理临时目录"""
    try:
        temp_dir = config.temp_dir
        if not os.path.exists(temp_dir):
            print(f"临时目录不存在: {temp_dir}")
            # 目录不存在时也创建，保持目录结构统一
            os.makedirs(temp_dir, exist_ok=True)
            print(f"已创建临时目录: {temp_dir}")
            return

        # 递归删除整个临时目录（包括目录内所有文件/子目录）
        shutil.rmtree(temp_dir)
        print(f"已删除临时目录: {temp_dir}")

        # 重新创建空的临时目录
        os.makedirs(temp_dir, exist_ok=True)
        print(f"已重新创建临时目录: {temp_dir}")

        print("定时清理临时文件完成，已删除并重建临时目录")
    except Exception as e:
        print(f"清理临时文件异常: {e}")


class CustomCronTrigger:
    """简化的触发器，不再支持workday/offday特殊值"""

    def __init__(self, cron_expression, timezone=None):
        self.cron_expression = cron_expression

        # 分离表达式和注释
        expr_parts = cron_expression.split("#", 1)
        self.base_expression = expr_parts[0].strip()

        # 使用zoneinfo明确创建时区对象，默认使用上海时区
        if timezone:
            try:
                tz = ZoneInfo(timezone)
            except ZoneInfoNotFoundError:
                tz = ZoneInfo("Asia/Shanghai")
        else:
            tz = ZoneInfo("Asia/Shanghai")

        # 构建基础Cron触发器
        try:
            self.base_trigger = CronTrigger.from_crontab(self.base_expression, timezone=tz)
        except Exception as e:
            raise ValueError(f"无效的Cron表达式: {self.base_expression}") from e

    def get_next_fire_time(self, previous_fire_time, now):
        # 获取基础Cron表达式的下一个触发时间
        return self.base_trigger.get_next_fire_time(previous_fire_time, now)


class Crontab:
    def __init__(self, log, timezone=None):
        self.log = log
        # 使用zoneinfo明确创建时区对象，默认使用上海时区
        if timezone:
            try:
                tz = ZoneInfo(timezone)
                self.log.info(f"使用配置的时区: {timezone}")
            except ZoneInfoNotFoundError as e:
                self.log.error(f"无效的时区配置: {timezone}, 错误: {e}")
                self.log.info("将使用默认时区: Asia/Shanghai")
                tz = ZoneInfo("Asia/Shanghai")
        else:
            tz = ZoneInfo("Asia/Shanghai")
            self.log.info("未配置时区，使用默认时区: Asia/Shanghai")
        
        self.scheduler = AsyncIOScheduler(timezone=tz)

    def start(self):
        self.log.info("启动定时任务调度器...")
        self.scheduler.start()
        self.log.info("定时任务调度器已启动")

    def add_job(self, expression, job, coalesce=True):
        try:
            # 构建Cron触发器
            expr_parts = expression.split("#", 1)
            base_expression = expr_parts[0].strip()
            self.log.info(f"添加定时任务，表达式: {base_expression}")
            
            # 显式指定时区创建CronTrigger，确保与调度器使用相同的时区
            trigger = CronTrigger.from_crontab(base_expression, timezone=self.scheduler.timezone)

            # 添加任务配置：
            # coalesce: 如果任务错过了多次执行，是否只执行一次（默认True，适合播放类任务）
            # max_instances=30: 允许同时运行最多30个实例，支持多设备并发
            # misfire_grace_time=60: 任务延迟60秒内仍然执行
            self.scheduler.add_job(
                job, trigger, coalesce=coalesce, max_instances=30, misfire_grace_time=60
            )
            self.log.info(f"定时任务添加成功，表达式: {base_expression}")
        except ValueError as e:
            self.log.error(f"无效的Cron表达式: {expression}, 错误: {e}")
        except Exception as e:
            self.log.exception(f"添加定时任务失败: {expression}, 错误: {e}")

    # 添加关机任务
    def add_job_stop(self, expression, xiaomusic, did, **kwargs):
        async def job():
            await xiaomusic.stop(did, "notts")

        self.add_job(expression, job)

    # 添加播放任务
    def add_job_play(self, expression, xiaomusic, did, arg1, **kwargs):
        async def job():
            await xiaomusic.play(did, arg1)

        self.add_job(expression, job)

    # 添加播放列表任务
    def add_job_play_music_list(self, expression, xiaomusic, did, arg1, **kwargs):
        async def job():
            await xiaomusic.play_music_list(did, arg1)

        self.add_job(expression, job)

    # 添加播放自定义列表任务
    def add_job_play_music_tmp_list(self, expression, xiaomusic, did, arg1, **kwargs):
        async def job():
            name = arg1 or "crontab_tmp_list"
            cron = kwargs["cron"]
            music_list = cron["music_list"]
            music_name = cron.get("first", "")
            ret = xiaomusic.music_library.play_list_update_music(name, music_list)
            if not ret:
                self.log.warning(f"crontb play_list_update_music failed name:{name}")
            await xiaomusic.do_play_music_list(did, name, music_name)

        self.add_job(expression, job)

    # 添加语音播放任务
    def add_job_tts(self, expression, xiaomusic, did, arg1, **kwargs):
        async def job():
            await xiaomusic.do_tts(did, arg1)

        self.add_job(expression, job)

    # 刷新播放列表任务
    def add_job_refresh_music_list(self, expression, xiaomusic, **kwargs):
        async def job():
            await xiaomusic.gen_music_list()

        self.add_job(expression, job)

    # 设置音量任务
    def add_job_set_volume(self, expression, xiaomusic, did, arg1, **kwargs):
        async def job():
            await xiaomusic.set_volume(did, arg1)

        self.add_job(expression, job)



    # 开启或关闭获取对话记录
    def add_job_set_pull_ask(self, expression, xiaomusic, did, arg1, **kwargs):
        async def job():
            try:
                self.log.info(f"定时任务开始执行: set_pull_ask {arg1}")
                
                # 解析参数格式：可以是 "enable"/"disable" 或者 "enable_pull_ask|enable" 格式
                config_param = arg1.split("|")
                if len(config_param) == 1:
                    # 兼容旧格式，默认控制 enable_pull_ask
                    config_key = "enable_pull_ask"
                    state_value = config_param[0]
                else:
                    # 新格式：config_key|state_value
                    config_key = config_param[0]
                    state_value = config_param[1]
                
                if state_value == "enable":
                    new_state = True
                else:
                    new_state = False
                
                # 更新对应的配置项
                if hasattr(xiaomusic.config, config_key):
                    self.log.info(f"更新配置: {config_key} = {new_state}")
                    setattr(xiaomusic.config, config_key, new_state)
                else:
                    self.log.error(f"未知的配置项: {config_key}")
                    return
                
                # 保存配置到文件
                self.log.info("保存配置到文件...")
                await xiaomusic.saveconfig(asdict(xiaomusic.config))
                
                self.log.info(f"定时任务执行成功: set_pull_ask {arg1}，{config_key}={new_state}")
                
            except Exception as e:
                self.log.error(f"定时任务执行失败: set_pull_ask {arg1}，错误: {e}", exc_info=True)

        self.add_job(expression, job)



    # 重新初始化
    def add_job_reinit(self, expression, xiaomusic, did, arg1, **kwargs):
        async def job():
            xiaomusic.reinit()

        self.add_job(expression, job)

    def add_job_cron(self, xiaomusic, cron):
        expression = cron["expression"]  # cron 计划格式
        name = cron["name"]  # stop, play, play_music_list, tts
        did = cron.get("did", "")
        arg1 = cron.get("arg1", "")
        jobname = f"add_job_{name}"
        func = getattr(self, jobname, None)
        if callable(func):
            func(expression, xiaomusic, did=did, arg1=arg1, cron=cron)
            self.log.info(
                f"crontab add_job_cron ok. did:{did}, name:{name}, arg1:{arg1} expression:{expression}"
            )
        else:
            self.log.error(
                f"'{self.__class__.__name__}' object has no attribute '{jobname}'"
            )

    # 清空任务
    def clear_jobs(self):
        for job in self.scheduler.get_jobs():
            try:
                job.remove()
            except Exception as e:
                self.log.exception(f"Execption {e}")

    # 重新加载计划任务
    def reload_config(self, xiaomusic):
        self.log.info("重新加载定时任务配置...")
        self.clear_jobs()

        crontab_json = xiaomusic.config.crontab_json
        if not crontab_json:
            self.log.info("crontab_json为空，没有定时任务需要加载")
            return

        try:
            cron_list = json.loads(crontab_json)
            self.log.info(f"解析到 {len(cron_list)} 个定时任务")
            for cron in cron_list:
                self.add_job_cron(xiaomusic, cron)
            self.log.info("crontab reload_config ok")
        except json.JSONDecodeError as e:
            self.log.error(f"JSON解析失败: {e}, crontab_json: {crontab_json}")
        except Exception as e:
            self.log.exception(f"重新加载定时任务失败: {e}")

        # 添加定时清理临时文件任务
        if xiaomusic.config.enable_auto_clean_temp:

            async def clean_temp_job():
                clean_temp_dir(xiaomusic.config)

            self.add_job("0 3 * * *", clean_temp_job)
            self.log.info("已添加每日凌晨3点定时清理临时文件任务")
