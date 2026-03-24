"""音乐管理路由"""

import urllib.parse

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from xiaomusic.api.dependencies import log, xiaomusic
from xiaomusic.api.models import DidPlayMusic

router = APIRouter()


@router.get("/searchmusic")
def searchmusic(name: str = ""):
    """搜索音乐"""
    return xiaomusic.music_library.searchmusic(name)


@router.post("/xiaoemby/device/pushUrl")
async def device_push_url(request: Request):
    """推送url给设备端播放"""
    try:
        # 获取请求数据
        data = await request.json()
        did = data.get("did")
        # 直接使用提供的URL
        url = data.get("url")
        decoded_url = urllib.parse.unquote(url)
        return await xiaomusic.play_url(did=did, arg1=decoded_url)
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/xiaoemby/proxy/emby/audio/{audio_id}.{container}")
async def proxy_emby_audio(audio_id: str, container: str):
    """代理Emby音频流，提供简单格式的播放地址"""
    try:
        # 构建原始Emby音频流URL
        emby_config = {
            "host": xiaomusic.config.emby_host,
            "api_key": xiaomusic.config.emby_api_key
        }
        
        # 构建原始URL
        original_url = f"{emby_config['host']}/emby/Audio/{audio_id}/stream?Container={container}&api_key={emby_config['api_key']}"
        
        # 转发请求到Emby服务器
        import requests
        response = requests.get(original_url, stream=True)
        
        # 构造响应头
        headers = {
            "Content-Type": f"audio/{container}",
            "Accept-Ranges": response.headers.get("Accept-Ranges", "bytes"),
        }
        
        # 只在 Emby 服务器返回了有效的 Content-Length 头时才设置它
        content_length = response.headers.get("Content-Length")
        if content_length and content_length.isdigit():
            headers["Content-Length"] = content_length
        
        
        # 返回流媒体响应
        from fastapi.responses import StreamingResponse
        return StreamingResponse(response.iter_content(chunk_size=1024*1024), headers=headers)
    except Exception as e:
        log.error(f"代理Emby音频失败: {e}")
        raise HTTPException(status_code=500, detail=f"代理音频失败: {e}")


@router.post("/xiaoemby/device/pushList")
async def device_push_list(request: Request):
    """WEB前端推送歌单给设备端播放"""
    try:
        # 获取请求数据
        data = await request.json()
        did = data.get("did")
        song_list = data.get("songList")
        list_name = data.get("playlistName")
        # 调用公共函数处理,处理歌曲信息 -> 添加歌单 -> 播放歌单
        return await xiaomusic.push_music_list_play(
            did=did, song_list=song_list, list_name=list_name
        )
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/playingmusic")
def playingmusic(did: str = ""):
    """当前播放音乐"""
    if not xiaomusic.did_exist(did):
        return {"ret": "Did not exist"}

    is_playing = xiaomusic.isplaying(did)
    cur_music = xiaomusic.playingmusic(did)
    cur_playlist = xiaomusic.get_cur_play_list(did)
    # 播放进度
    offset, duration = xiaomusic.get_offset_duration(did)
    return {
        "ret": "OK",
        "is_playing": is_playing,
        "cur_music": cur_music,
        "cur_playlist": cur_playlist,
        "offset": offset,
        "duration": duration,
    }


@router.get("/musiclist")
async def musiclist():
    """音乐列表"""
    return xiaomusic.music_library.get_music_list()


@router.get("/musicinfo")
async def musicinfo(name: str):
    """音乐信息"""
    url, _ = await xiaomusic.music_library.get_music_url(name)
    info = {
        "ret": "OK",
        "name": name,
        "url": url,
    }
    return info


@router.get("/musicinfos")
async def musicinfos(
    name: list[str] = Query(None),
):
    """批量音乐信息"""
    ret = []
    for music_name in name:
        url, _ = await xiaomusic.music_library.get_music_url(music_name)
        info = {
            "name": music_name,
            "url": url,
        }
        ret.append(info)
    return ret


@router.post("/playmusic")
async def playmusic(data: DidPlayMusic):
    """播放音乐"""
    did = data.did
    musicname = data.musicname
    searchkey = data.searchkey
    if not xiaomusic.did_exist(did):
        return {"ret": "Did not exist"}

    log.info(f"playmusic {did} musicname:{musicname} searchkey:{searchkey}")
    await xiaomusic.do_play(did, musicname, searchkey)
    return {"ret": "OK"}


@router.post("/debug_play_by_music_url")
async def debug_play_by_music_url(request: Request):
    """调试播放音乐URL"""
    try:
        data = await request.body()
        data_dict = json.loads(data.decode("utf-8"))
        log.info(f"data:{data_dict}")
        return await xiaomusic.debug_play_by_music_url(arg1=data_dict)
    except json.JSONDecodeError as err:
        raise HTTPException(status_code=400, detail="Invalid JSON") from err


@router.post("/xiaoemby/music/refreshlist")
async def refreshlist():
    """刷新歌曲列表"""
    await xiaomusic.gen_music_list()
    return {
        "ret": "OK",
    }
