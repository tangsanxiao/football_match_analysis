# 实施日志

## 2026-05-06: 工程骨架、视频探测、样本帧

### 已创建

- `requirements-mvp.txt`
- `configs/match_red_mvp.yaml`
- `scripts/00_probe_video.py`
- `scripts/01_sample_frames.py`
- `scripts/02_calibrate_field.py`
- `scripts/create_point_picker.py`
- `scripts/serve_point_picker.py`
- `scripts/03_detect_track.py`
- `scripts/04_summarize_tracklets.py`
- `configs/calibration_points_red_mvp.yaml`
- `data/interim/red_mvp_20260505/`
- `reports/`

### 环境

- 系统 Python: 3.9.6
- 项目虚拟环境: `.venv`
- 已安装 MVP 依赖:
  - `opencv-python-headless`
  - `pyyaml`
  - `numpy`
  - `pandas`
  - `tqdm`
  - `jinja2`

当前仍未安装:

- `ffmpeg`
- `ffprobe`
- `ultralytics`
- `supervision`

### 检测/追踪依赖

已新增:

```text
requirements-detect.txt
```

安装命令:

```bash
.venv/bin/python -m pip install -r requirements-detect.txt
```

已安装关键依赖:

- `ultralytics 8.4.46`
- `torch 2.8.0`
- `torchvision 0.23.0`
- `lap 0.5.13`

本机 PyTorch MPS 可用，可用 `--device mps` 运行检测。

### 视频探测结果

探测命令:

```bash
.venv/bin/python scripts/00_probe_video.py --config configs/match_red_mvp.yaml
```

结果:

| 字段 | 值 |
|---|---:|
| 视频路径 | `/Users/bytedance/Documents/Learn/M5/Football/videos/raw/dji_export_20260505_234303_1777995783034_editor.mp4` |
| 文件大小 | 3.359 GB |
| 分辨率 | 2688 x 1512 |
| 帧率 | 60.0 fps |
| 总帧数 | 34,758 |
| 时长 | 579.3 秒，约 9 分 39 秒 |
| OpenCV 可读 | 是 |
| 第一帧可读 | 是 |

输出:

```text
data/interim/red_mvp_20260505/video_probe.json
```

### 抽帧结果

抽帧命令:

```bash
.venv/bin/python scripts/01_sample_frames.py --config configs/match_red_mvp.yaml
```

结果:

- 均匀抽取 36 张样本帧
- 全部保存成功
- 样本帧目录:

```text
data/interim/red_mvp_20260505/sample_frames/
```

- 样本清单:

```text
data/interim/red_mvp_20260505/sample_frames/sample_frames.csv
```

### 视觉观察

- 全场覆盖很好，适合固定机位分析。
- 球场线、两个球门、中线、禁区/罚球区线大体清楚。
- 红队球员和黄色门将可见，后续身份绑定有基础。
- 视频有 DJI Action5 Pro 水印，遮挡左侧部分画面；对左侧球门附近检测可能有轻微影响。
- 广角畸变明显，尤其画面边缘和近端底线/边线区域。后续球场坐标映射不应只假设理想 pinhole homography；MVP 可以先用 homography 跑通，但报告必须标记跑动距离/空间指标的误差风险。更稳的下一步是增加镜头畸变校正或分区/局部 homography。

### 下一步建议

1. 选择一张场线最清楚、球员遮挡少的样本帧作为校准帧。
2. 确认实际球场尺寸。如果不知道，先继续使用默认 40m x 20m。
3. 在 `configs/calibration_points_red_mvp.yaml` 中录入至少 8 个像素点到球场坐标的对应关系。
4. 运行 `scripts/02_calibrate_field.py`，计算 homography、重投影误差和校准可视化图。
5. 若重投影误差在边缘明显偏大，再加入镜头畸变校正或局部映射方案。

校准命令:

```bash
.venv/bin/python scripts/02_calibrate_field.py \
  --config configs/match_red_mvp.yaml \
  --points configs/calibration_points_red_mvp.yaml
```

当前模板尚未填写 `image_xy`，脚本会友好提示需要先填点位。

### 像素坐标拾取工具

已生成网页点位工具:

```text
data/interim/red_mvp_20260505/calibration/point_picker.html
```

生成命令:

```bash
.venv/bin/python scripts/create_point_picker.py \
  --config configs/match_red_mvp.yaml \
  --points configs/calibration_points_red_mvp.yaml
```

使用方式:

1. 用浏览器打开 `point_picker.html`。
2. 右侧选择要填写的场地点。
3. 在图片上点击对应场线位置。
4. 复制右侧生成的 YAML 片段。
5. 回填到 `configs/calibration_points_red_mvp.yaml`。
6. 运行 `scripts/02_calibrate_field.py` 生成 homography 和误差报告。

### 像素坐标自动保存服务

已新增本地服务:

```text
scripts/serve_point_picker.py
```

启动命令:

```bash
.venv/bin/python scripts/serve_point_picker.py \
  --config configs/match_red_mvp.yaml \
  --points configs/calibration_points_red_mvp.yaml \
  --host 127.0.0.1 \
  --port 8765
```

打开地址:

```text
http://127.0.0.1:8765/
```

在这个 `localhost` 页面中点击场地点后，坐标会直接写入 `configs/calibration_points_red_mvp.yaml` 对应点位的 `image_xy`。旧的 `file://` 页面仍然只能复制 YAML 片段，不能直接写文件。

### 首次校准结果

用户已完成 9 个点位标记，坐标已写入:

```text
configs/calibration_points_red_mvp.yaml
```

运行校准:

```bash
.venv/bin/python scripts/02_calibrate_field.py \
  --config configs/match_red_mvp.yaml \
  --points configs/calibration_points_red_mvp.yaml
```

结果:

| 指标 | 值 |
|---|---:|
| 点位数 | 9 |
| 平均误差 | 2.355 m |
| 最大误差 | 4.587 m |
| 质量提示 | `high_error_review_points_or_add_lens_distortion_correction` |

输出:

```text
data/interim/red_mvp_20260505/calibration/calibration.json
data/interim/red_mvp_20260505/calibration/calibration_overlay.jpg
```

误差最大的点:

- `top_left_corner`: 4.587 m
- `center_mark`: 3.410 m
- `bottom_left_corner`: 2.586 m
- `halfway_bottom_touchline`: 2.546 m
- `bottom_right_corner`: 2.517 m

判断:

- 当前点位已成功保存，工具链工作正常。
- 单个 homography 不足以可靠覆盖全场，主要原因可能是 DJI Action5 广角畸变，加上部分角点位于画面边缘或不是非常清晰的真实场线交点。
- 这版校准可用于粗略战术可视化，但暂不适合严肃计算跑动距离、压迫半径和球员间距。

下一步:

1. 优先复核 `top_left_corner`、`center_mark`、`bottom_left_corner`、`bottom_right_corner`。
2. 不要点“延长线估计出的角点”，只点真实可见的白线交点或中心点。
3. 如果修正后平均误差仍高于 1m，进入镜头畸变校正或局部 homography 方案。

### 二次校准结果

用户重新标记了:

- `top_left_corner`
- `center_mark`
- `halfway_bottom_touchline`

确认:

- `bottom_left_corner` 和 `bottom_right_corner` 在画面中确实不完整，已设置 `enabled: false`，不参与校准。
- `top_left_corner` 虽有坐标，但不是可靠清晰角点，已设置 `enabled: false`，不参与校准。

当前参与 homography 的 6 个点:

- `top_right_corner`
- `halfway_top_touchline`
- `halfway_bottom_touchline`
- `center_mark`
- `left_goal_center`
- `right_goal_center`

重算结果:

| 指标 | 值 |
|---|---:|
| 点位数 | 6 |
| 平均误差 | 1.089 m |
| 最大误差 | 2.438 m |
| 质量提示 | `high_error_review_points_or_add_lens_distortion_correction` |

误差最大的有效点:

- `center_mark`: 2.438 m
- `left_goal_center`: 1.509 m
- `top_right_corner`: 0.929 m

判断:

- 当前映射比首次 9 点校准明显改善，足够作为检测/追踪阶段的粗坐标映射。
- 仍不适合直接产出高精度跑动距离、压迫半径、球员间距。
- MVP 下一步可继续做球员/足球检测追踪；报告中需将空间类指标标记为中低置信。
- 若后续要提升空间指标，建议进入镜头畸变校正或分区/局部 homography。

## 2026-05-06: 球员/足球检测追踪 smoke test

### 已创建

- `scripts/03_detect_track.py`
- `scripts/04_summarize_tracklets.py`
- `requirements-detect.txt`

### 检测配置

当前使用:

- 模型: `yolo11n.pt`
- tracker: `bytetrack.yaml`
- class: `person` 和 `sports ball`
- 输入尺寸: `1280`
- 抽样帧率: `2 fps`
- 设备: `mps`
- 场地过滤: 开启

### 10 秒 smoke test

命令:

```bash
.venv/bin/python scripts/03_detect_track.py \
  --config configs/match_red_mvp.yaml \
  --start-sec 120 \
  --duration-sec 10 \
  --sample-fps 2 \
  --max-frames 20 \
  --device mps \
  --save-annotated-every 2
```

输出:

```text
data/interim/red_mvp_20260505/detections/segment_0120_0010_2fps/
```

结果:

| 指标 | 值 |
|---|---:|
| 处理帧数 | 20 |
| 原始 person 检测 | 121 |
| 原始 sports ball 检测 | 10 |
| 场内 person 检测 | 99 |
| 场内 sports ball 检测 | 0 |

结论:

- 人物检测链路可用。
- 场地过滤能有效去掉场边备用球。
- 通用 YOLO 的比赛用球检测暂不可用。

### 2 分钟验证段

命令:

```bash
.venv/bin/python scripts/03_detect_track.py \
  --config configs/match_red_mvp.yaml \
  --start-sec 120 \
  --duration-sec 120 \
  --sample-fps 2 \
  --max-frames 240 \
  --device mps \
  --save-annotated-every 20
```

输出:

```text
data/interim/red_mvp_20260505/detections/segment_0120_0120_2fps/
```

结果:

| 指标 | 值 |
|---|---:|
| 处理帧数 | 240 |
| 处理耗时 | 约 43 秒 |
| 原始 person 检测 | 1,865 |
| 原始 sports ball 检测 | 202 |
| 场内 person 检测 | 1,583 |
| 场内 sports ball 检测 | 6 |
| 场内 person 帧覆盖 | 100% |
| 场内 sports ball 帧覆盖 | 2.5% |

输出文件:

```text
tracks.csv
tracks_in_play.csv
summary.json
annotated_frames/
```

判断:

- `person` 检测和追踪可进入身份绑定阶段。
- `sports ball` 检测大部分是场边备用球或误检，不能直接用于传球、射门、抢断事件。
- 下一步需要补一个足球专用检测方案，或用高分辨率候选球检测/小目标切片方案。

### Tracklet 汇总

命令:

```bash
.venv/bin/python scripts/04_summarize_tracklets.py \
  --config configs/match_red_mvp.yaml \
  --detections-dir data/interim/red_mvp_20260505/detections/segment_0120_0120_2fps \
  --min-frames 8 \
  --top-n 30
```

输出:

```text
data/interim/red_mvp_20260505/detections/segment_0120_0120_2fps/tracklets.csv
data/interim/red_mvp_20260505/detections/segment_0120_0120_2fps/tracklet_contact_sheet.jpg
```

结果:

- 生成 53 条帧数不低于 8 的 person tracklet。
- contact sheet 能看到红队粉色球员、黄色门将、蓝队球员和场边人员。
- 同一名球员会被拆成多个 track_id，后续身份绑定需要做 tracklet 合并。

下一步:

1. 用 contact sheet 做红队身份初步绑定。
2. 按颜色和位置过滤场边人员。
3. 对红队球员做 tracklet 合并。
4. 单独解决足球检测，不把当前通用模型的 sports ball 结果作为事件依据。

## 2026-05-06 全片 MVP 初版

### 全片检测与追踪

命令:

```bash
.venv/bin/python scripts/03_detect_track.py \
  --config configs/match_red_mvp.yaml \
  --start-sec 0 \
  --duration-sec 580 \
  --sample-fps 2 \
  --max-frames 0 \
  --device mps \
  --save-annotated-every 60
```

输出:

```text
data/interim/red_mvp_20260505/detections/segment_0000_0580_2fps/
```

结果:

| 指标 | 值 |
|---|---:|
| 处理帧数 | 1,159 |
| 原始 person 检测 | 8,956 |
| 原始 sports ball 检测 | 803 |
| 场内 person 检测 | 7,153 |
| 场内 sports ball 检测 | 65 |
| 场内 person 帧覆盖 | 100% |
| 场内 sports ball 帧覆盖 | 5.61% |
| person track 数 | 757 |
| sports ball track 数 | 12 |

### 红队候选与身份绑定

新增脚本:

```text
scripts/05_classify_tracklets.py
scripts/06_assign_identities.py
scripts/07_generate_report.py
```

全片 tracklet 汇总:

```bash
.venv/bin/python scripts/04_summarize_tracklets.py \
  --config configs/match_red_mvp.yaml \
  --detections-dir data/interim/red_mvp_20260505/detections/segment_0000_0580_2fps \
  --min-frames 8 \
  --top-n 60
```

颜色候选分类:

```bash
.venv/bin/python scripts/05_classify_tracklets.py \
  --config configs/match_red_mvp.yaml \
  --detections-dir data/interim/red_mvp_20260505/detections/segment_0000_0580_2fps \
  --samples-per-track 5 \
  --top-n 60
```

身份绑定:

```bash
.venv/bin/python scripts/06_assign_identities.py \
  --config configs/match_red_mvp.yaml \
  --detections-dir data/interim/red_mvp_20260505/detections/segment_0000_0580_2fps \
  --right-forward-y high
```

输出:

```text
tracklet_identity_assignments.csv
tracks_red_labeled.csv
identity_bindings_auto.yaml
```

当前身份判断:

- HDA 门将: 黄色球衣 + 己方球门位置，置信度高。
- LXY/TYX/JYN/ZMC: 先用红队候选 + 角色区域做自动绑定，置信度为 provisional/medium，后续应结合号码 crop 做一次轻量校正。

### 初版报告

命令:

```bash
.venv/bin/python scripts/07_generate_report.py \
  --config configs/match_red_mvp.yaml \
  --detections-dir data/interim/red_mvp_20260505/detections/segment_0000_0580_2fps \
  --output-dir reports/red_mvp_20260505/mvp_initial
```

输出:

```text
reports/red_mvp_20260505/mvp_initial/report.md
reports/red_mvp_20260505/mvp_initial/report.html
reports/red_mvp_20260505/mvp_initial/player_metrics.csv
reports/red_mvp_20260505/mvp_initial/key_timestamps.csv
reports/red_mvp_20260505/mvp_initial/tracks_red_labeled.csv
```

初版评分:

| 球员 | 号码 | 角色 | 评分 | 身份置信度 |
|---|---:|---|---:|---|
| HDA | 1 | goalkeeper | 8.5 | high |
| TYX | 18 | right_forward | 8.4 | provisional |
| ZMC | 28 | left_forward | 7.9 | provisional |
| JYN | 9 | center_forward | 7.7 | medium |
| LXY | 2 | defender | 6.9 | provisional |

注意:

- 传球、射门、抢断仍不能作为正式技术统计，原因是足球场内检测覆盖只有 5.61%。
- 当前技术统计以站位、压迫距离、进入三区、跑动强度等 proxy 指标为主。
- 下一轮最有价值的优化是: 号码/球鞋身份校正 + 足球小目标检测方案。

## 2026-05-06 多比赛项目隔离与自动化

新增脚本:

```text
scripts/08_new_match_project.py
scripts/09_prepare_match_review.py
scripts/10_run_match_mvp.py
```

关键改造:

- 新比赛默认建立在 `matches/<match_id>/` 下。
- 每场比赛有独立的 `config/match.yaml`、`config/calibration_points.yaml`、`data/interim/`、`review/`、`reports/`。
- `02_calibrate_field.py`、`create_point_picker.py`、`serve_point_picker.py` 已支持从 `match.yaml` 的 `calibration.points_path` 读取项目内标定文件。
- `05_classify_tracklets.py` 从固定红队候选升级为按 `teams.<analyze_team>.kit.field_colors` 和 `goalkeeper_colors` 筛选重点分析球队候选。
- `06_assign_identities.py` 和 `07_generate_report.py` 支持 `teams.analyze_team`，不再硬编码红队。
- 新增多比赛使用文档: `docs/football-video-analysis-mvp/05-multi-match-automation.md`。

验证:

- `py_compile` 通过新增脚本和改造脚本。
- 使用当前视频创建了临时 `matches/test_project_smoke` 项目并成功生成项目配置，随后已清理测试目录。
- 在已有 2 分钟验证段上重新跑通颜色候选和身份绑定，新的 `analyze_*` 候选类型可被身份绑定脚本消费。
