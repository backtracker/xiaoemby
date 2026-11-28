# 静态/动态数据拆分实现说明（v0.0.19-rc2）

来源：wayfinder「本地数据缓存统一设计」图的 02 / 03 号决策工单。

## 决策回顾

- 曲库镜像只存静态曲目元数据（id/标题/艺人/流派/专辑/年份/日期/墓碑）。
- 用户态动态信息（收藏 / 累计播放次数 / 最近播放）不落镜像，一律走接口：
  - Emby `fetch_tracks_by_ids` / `fetch_recommend_candidates` 返回的 `UserData`（`IsFavorite` / `PlayCount` / `LastPlayedDate`），顺已有请求批量取，零新增 HTTP 次数；
  - PR 逐次播放事件走插件只读接口，事件不落本地；
  - `DETAIL_FIELDS` 补 `UserDataPlayCount`（此前只请求 `UserDataLastPlayedDate`，导致 `PlayCount` 恒为 0）。
- PR 插件才装几天、数据不足 → 现有档位逻辑自然降级 v2，数据积累够后自动启用 PR 模式。

## 代码改动

| 文件 | 改动 |
| --- | --- |
| `xiaomusic/utils/emby_utils.py` | `DETAIL_FIELDS` 补 `UserDataPlayCount`；`Audio` 已支持 `play_count`，`_parse_recommend_item` 已解析该字段。 |
| `xiaomusic/utils/library_mirror.py` | 删 `AUTHORITATIVE_USER`、`user_state` 表及建表/写入/查询；`normalize_emby_item` 不再产出 `users`；归约器（全量/增量/对账/墓碑）不变量不变（已逐 action 确认不读 `users`）。 |
| `xiaomusic/utils/mirror_sync.py` | 增量对账只比元数据字段，不再按账号对账用户态；水位/墓碑/全量对账保留。 |
| `xiaomusic/utils/behavior_profile.py` | `build_profile` 新增 `user_states` 注入参数 `{item_id: {user_id: {favorite, play_count, last_played_date}}}`；所有用户态读取走注入，兼容旧 `item["users"]` 以便测试渐进迁移。 |
| `xiaomusic/utils/daily_recommend.py` | 新增 `_user_states_from_pr()`：从 PR 有效播放推导累计次数与最近播放，注入画像；`_audio_from_mirror` 不再从镜像读动态字段。 |
| `xiaomusic/music_library.py` | 启动全库加载仍用 `_audio_from_mirror`，收藏/次数留空；「播放我喜欢的」走 Emby `IsFavorite=true` 搜索，不依赖内存库的收藏标记。 |
| `scripts/e2e_v3.py` | E2E fake Emby 的动态信息改由 PR 事件推导，不再调已删除的 `mirror.user_state()`。 |
| `tests/*` | 用户态相关断言改为「快照不带 users / 元数据全库共享」；画像测试通过 `user_states` 注入动态信息。 |

## 已知权衡

1. **PR 模式的收藏信号**：PR 事件没有收藏字段，画像里 `favorite` 在 PR 模式下为空；收藏强正反馈/收藏豁免等规则在 PR 模式弱化，等 Emby 侧收藏信号能批量注入候选池后再补齐。现阶段 PR 样本不足、走 v2，影响范围有限。
2. **启动全库的收藏标记**：从镜像加载的 `all_music` 中 `is_favorite` 为 `False`。当前没有 UI/语音依赖该字段显示收藏状态；「播放我喜欢的」走 Emby 搜索接口，行为不变。
3. **累计播放次数**：`cumulative()` 取 `max(UserDataPlayCount, PR 有效播放数)`；接口提供了累计次数，PR 提供近期次数，两者不冲突。
4. **PR 事件本地缓存**：按决策不做；数据积累后如生成变慢，另开工单论证（原始事件属历史事实，与动态用户态性质不同）。

## 验证

- `pytest -q`：449 passed。
- `pytest -q tests/test_e2e_v3.py`：4 passed。
- `py_compile` 全部改动文件通过。
