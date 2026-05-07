# 5 人制足球视频分析 MVP 执行指令

## Role

你是一名兼具计算机视觉、体育数据分析和业余 5 人制足球训练经验的工程负责人。你的目标不是堆模型，而是用可靠、可解释、可复用的工程流程，把固定机位比赛视频转化成有训练价值的赛后报告。

## Core Task

在 `/Users/bytedance/Documents/Learn/M5/Football` 项目内，设计并逐步实现一套命令行 MVP，用于分析 DJI Action5 固定机位 5 人制足球比赛视频。第一版重点分析红队 5 名球员，输出个人评分、技术统计、跑位/战术复盘、训练建议、CSV、Markdown、HTML 和关键片段时间戳。

## Context

原始视频:

```text
/Users/bytedance/Documents/Learn/M5/Football/videos/raw/dji_export_20260505_234303_1777995783034_editor.mp4
```

拍摄条件:

- 固定机位，球场中线靠左，约 4 米高
- 基本全场可见
- 足球基本清晰
- 场线、球门和关键区域清楚

红队:

| player_id | name | number | visual_hint | role |
|---|---|---:|---|---|
| red_hda | HDA | 1 | 黄色衣服 | goalkeeper |
| red_lxy | LXY | 2 | 2 号 | defender |
| red_tyx | TYX | 18 | 18 号, 绿色球鞋 | right_forward |
| red_jyn | JYN | 9 | 9 号 | center_forward |
| red_zmc | ZMC | 28 | 28 号 | left_forward |

蓝队暂不做个人分析，只作为对手整体参与追踪、控球、压迫和空间判断。

当前片段按红队无换人处理。后续配置必须支持换人时间。

## Deliverables

项目最终至少应包含:

```text
configs/
  match_red_mvp.yaml
scripts/
  00_probe_video.py
  01_sample_frames.py
  02_calibrate_field.py
  03_detect_track.py
  04_assign_identity.py
  05_infer_events.py
  06_compute_metrics.py
  07_generate_report.py
data/
  interim/
reports/
  <match_id>/
    report.md
    report.html
    player_metrics.csv
    events.csv
    possessions.csv
    key_clips.csv
    tracking_quality.csv
    calibration.json
```

若先做方案而非代码实现，至少要完整定义上述流程、指标、数据结构和验收标准。

## Technical Basis

优先使用成熟工具，不手写核心检测/追踪能力:

- Ultralytics YOLO 的 track 模式支持 BoT-SORT 和 ByteTrack，并能通过 Python API 或 CLI 运行视频追踪: https://docs.ultralytics.com/modes/track/
- Ultralytics YOLO26 官方文档说明其支持 detection、segmentation、classification、pose、OBB 等任务，且有 Python/CLI 用法: https://docs.ultralytics.com/models/yolo26/
- Roboflow Supervision 提供视频帧读取、ByteTrack、检测对象封装、区域工具和标注输出，适合快速搭建 MVP: https://supervision.roboflow.com/develop/notebooks/object-tracking/
- Supervision 的 PolygonZone 可基于 tracker_id 做区域触发，可用于禁区、半场、边路、压迫区等空间指标: https://supervision.roboflow.com/latest/detection/tools/polygon_zone/
- OpenCV homography 可把图像点映射到球场平面坐标，适合固定机位的场地校准: https://docs.opencv.org/4.x/d9/dab/tutorial_homography.html
- SoccerNet 将足球理解拆成 action spotting、field localization、camera calibration、tracking、jersey number recognition 等任务，说明“视频分析”应拆成多个子问题处理: https://www.soccer-net.org/tasks/action-spotting
- SoccerNet Tracking 明确足球多目标追踪的对象包括球员、门将、裁判、球，且追踪可用于分别评估球员表现: https://www.soccer-net.org/tasks/tracking
- ByteTrack 论文思路是关联几乎所有检测框而非只保留高置信框，适合处理遮挡和低置信检测: https://arxiv.org/abs/2110.06864

## Execution Principles

1. 先跑通闭环，再追求精度。
2. 检测、追踪、事件推断、评分和报告生成分层实现。
3. 每个推断都带置信度，低置信事件进入关键校验列表。
4. 不做逐帧人工标注，但允许一次性球场校准、身份对照、换人录入和关键事件校验。
5. 所有指标都应能从轨迹、球权、事件或空间位置回溯，不输出没有数据依据的评价。
6. 固定机位是核心前提。后续若换机位，需要重新校准球场映射。
7. 第一版不要追求自动识别球衣号码，号码仅作为身份校验线索。

## Implementation Steps

### 1. 环境与视频探测

安装并固定依赖:

```text
python >= 3.11
ffmpeg / ffprobe
opencv-python
ultralytics
supervision
numpy
pandas
scipy
pyyaml
jinja2
matplotlib 或 plotly
tqdm
```

`00_probe_video.py` 输出:

- 分辨率
- 帧率
- 总时长
- 总帧数
- 编码信息
- 是否可被 OpenCV 正常读取

如果视频过长或过大，生成低分辨率 proxy 供调试，但最终分析仍应保留高分辨率抽帧能力，尤其用于足球检测。

### 2. 抽帧与质量检查

`01_sample_frames.py` 从全片均匀抽取 20 到 50 张关键帧，另抽取开球、攻防转换、射门附近片段用于调试。

检查:

- 全场是否完整可见
- 场线是否足够清楚
- 足球在不同区域是否可见
- 红蓝队球衣颜色是否可区分
- 红队 5 人身份线索是否可用

输出:

```text
data/interim/<match_id>/sample_frames/
```

### 3. 球场坐标校准

`02_calibrate_field.py` 从清晰帧中选择至少 8 个场地点，建议包括:

- 四个角点
- 中线与边线交点
- 禁区/罚球区关键点
- 两侧球门中心或门柱点

将图像像素映射到标准 5 人制球场坐标。默认按 40m x 20m，可在 `configs/match_red_mvp.yaml` 中覆盖实际场地尺寸。

输出:

- `calibration.json`: 图像点、球场点、homography 矩阵、重投影误差
- 质量门槛: 平均重投影误差尽量低于 0.5m；超过 1.0m 时报告中标记跑动距离和空间指标低置信

### 4. 球员与足球检测追踪

`03_detect_track.py` 产出逐帧对象表:

```text
frame_idx,timestamp,track_id,class,x1,y1,x2,y2,conf,cx,cy,field_x,field_y
```

建议路线:

- 球员: 使用 Ultralytics YOLO detection + BoT-SORT 或 ByteTrack；优先开启 ReID 或外观辅助以减少遮挡后的 ID 串号。
- 足球: 优先尝试通用 `sports ball` 或足球专用检测模型；若漏检多，使用高分辨率切片、低阈值候选、轨迹连续性和近脚区域约束。
- 蓝队: 仍需检测和追踪，但只保留 opponent track，不生成个人评分。
- 追踪帧率: MVP 可先用 10 到 15 fps；足球事件附近可提高帧率或全帧率复算。

质量输出:

- 球员检测覆盖率
- 红队 track 断裂次数
- 足球检测覆盖率
- ID switch 可疑片段
- 每类对象低置信片段

### 5. 红队身份绑定

`04_assign_identity.py` 将 track_id 映射到红队球员。

规则:

- 守门员 HDA: 黄色衣服、活动区域接近本方球门。
- TYX: 18 号、右边前锋、绿色球鞋为辅助线索。
- LXY/JYN/ZMC: 结合开局站位、号码可见性、角色区域、运动轨迹和颜色。
- 当出现 track 断裂，优先使用位置连续性、外观特征和角色区域重连。

输出:

```text
track_id,player_id,start_time,end_time,confidence,reason
```

低置信身份必须进入人工校验列表，不得静默写入确定结论。

### 6. 球权与事件推断

`05_infer_events.py` 以轨迹和球位置推断事件。

核心事件:

- pass_attempt
- pass_completed
- pass_lost
- shot
- shot_on_target_candidate
- steal
- turnover
- pressure_event
- off_ball_run

基本逻辑:

- 控球: 球位于某球员脚下半径阈值内，且球速/相对速度符合可控状态，持续超过最小帧数。
- 传球: 红队球员 A 控球后，球离脚并移动至红队球员 B 控球，记为成功传球。
- 传丢: 红队球员 A 控球后，下一稳定控球者为蓝队或球出界，记为传丢或失误候选。
- 抢断: 蓝队控球后，红队球员在接近范围内造成球权转为红队，记为抢断候选。
- 射门: 红队球员最后触球后，球高速朝对方球门方向运动，进入球门/门将/门框/门区附近结果区域。
- 压迫: 红队球员在蓝队控球时进入压迫距离，且朝持球者或关键传球路线移动。
- 无球跑动: 非持球红队球员产生明显位移，并改善接应角度、纵深、宽度、压迫或牵制效果。

第一版必须将射门、抢断、传丢、关键传球设为可校验事件，输出时间戳和置信度。

### 7. 指标计算

`06_compute_metrics.py` 输出球员级指标。

通用指标:

- 上场时间
- 触球/控球次数
- 传球尝试、成功、成功率
- 向前传球、横传/回传
- 射门、射正候选
- 抢断候选、成功抢回球权
- 丢失球权
- 跑动距离
- 高强度跑动次数
- 平均站位深度和宽度
- 站位纪律分
- 压迫次数、有效压迫次数
- 无球接应次数
- 关键片段贡献

空间/战术指标:

- 热区图
- 进攻宽度贡献
- 防守回收速度
- 与队友平均间距
- 三线/菱形结构保持情况
- 失球后 3 秒反抢参与
- 球在强侧/弱侧时的支援位置

门将 HDA 额外指标:

- 出球参与
- 站位离门距离
- 面对射门/危险球处理候选
- 防线身后保护
- 开球或手抛球方向

### 8. 评分模型

输出 0 到 10 分总评，并拆成 5 个子项。评分要服务业余 5 人制，不应直接套职业大场数据。

建议权重:

| 子项 | 权重 | 说明 |
|---|---:|---|
| 技术执行 | 25% | 传球、控球、射门、失误控制 |
| 攻防贡献 | 25% | 射门、抢断、回防、转换参与 |
| 站位纪律 | 20% | 角色区域、队形保持、补位 |
| 无球价值 | 15% | 接应、拉开宽度、纵深跑、牵制 |
| 压迫与投入 | 15% | 压迫频率、反抢、逼迫对手处理球 |

门将也使用 0 到 10 总分，但解释时不强行套用前锋指标。门将技术执行主要看出球、处理危险球和站位；攻防贡献主要看防线组织、球门保护和转换发起。

评分规则:

- 5.0: 基本完成角色任务，但贡献有限或失误较多
- 6.0: 合格，有稳定参与和少量正向贡献
- 7.0: 明显正向，攻防至少一端有持续价值
- 8.0: 本队关键球员，多项指标优秀且关键片段有贡献
- 9.0+: 业余比赛中非常突出，技术、决策、强度和团队价值同时在线

必须避免:

- 只用传球成功率评分
- 用总跑动距离替代无球质量
- 把低置信射门/抢断写成确定事实
- 因为某球员镜头更清楚而给更高分

### 9. 报告生成

`07_generate_report.py` 生成:

- `report.md`
- `report.html`
- `player_metrics.csv`
- `events.csv`
- `possessions.csv`
- `key_clips.csv`
- `tracking_quality.csv`

报告结构:

1. 比赛概览
2. 数据质量说明
3. 红队整体表现
4. 每名球员评分与解释
5. 技术统计表
6. 站位与跑位复盘
7. 压迫与防守复盘
8. 关键片段时间戳
9. 训练建议
10. 低置信事件和建议校验点

`key_clips.csv` 字段:

```text
timestamp_start,timestamp_end,event_type,players,confidence,reason,review_needed
```

## Suggested MVP Timeline

第 1 周:

- 环境安装
- 视频探测
- 抽帧
- 球场校准
- 球员检测追踪初版

第 2 周:

- 足球检测和轨迹平滑
- 红队身份绑定
- 传球、射门、抢断候选推断
- 数据质量报告

第 3 周:

- 指标计算
- 评分模型
- Markdown/CSV/HTML 报告
- 关键片段时间戳

第 4 周:

- 在当前视频上调参
- 做 5 到 15 分钟人工校验闭环
- 修正评分解释
- 固化命令行入口

## CLI Shape

建议最终命令:

```bash
python scripts/run_match_analysis.py \
  --config configs/match_red_mvp.yaml \
  --video videos/raw/dji_export_20260505_234303_1777995783034_editor.mp4 \
  --match-id red_mvp_20260505
```

也可以先保留分步命令，便于调试。

## Quality Bar

主流程完成时应满足:

- 能读入视频并输出可复用的中间数据。
- 红队 5 人能被稳定绑定身份，低置信片段可见。
- 传球、射门、抢断至少能输出候选事件和关键时间戳。
- 每名球员都有总分、子项分、证据和训练建议。
- HTML/Markdown/CSV 文件可打开、字段清晰。
- 报告明确说明哪些结论是高置信，哪些只是候选。

## Known Non-Goals For MVP

- 不追求职业数据公司级别的全自动事件精度。
- 不做 xG、助攻链路、复杂战术模型或多机位融合。
- 不强依赖自动球衣号码识别。
- 不对蓝队个人评分。
- 不把单场评分扩展为长期能力画像，除非后续有多场数据。

## Final Self-Check

完成后按以下问题自检:

1. 是否真正解决“赛后表现分析”而不只是生成轨迹图。
2. 是否符合用户不想人工标注的约束。
3. 是否对固定机位、场线清楚、球清晰这些优势做了充分利用。
4. 是否把不确定性暴露给用户。
5. 是否能在下一场比赛复用。
6. 自评分是否达到 9.5 以上；若不足，说明扣分原因并修订。
