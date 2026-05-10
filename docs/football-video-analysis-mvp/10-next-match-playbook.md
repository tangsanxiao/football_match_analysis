# 下一场比赛分析操作手册

> 日期：2026-05-10
> 适用：基于 08/09 文档建立的评估底座 + ball_v1 检测器之后,任何**新比赛**的端到端分析。
> 本文档的目标:让一场新比赛从"拿到视频"到"导出报告"在 30-60 分钟内顺利完成,并比之前的版本更可信。

---

## 1. 视频切分建议(直接答案)

**每场比赛 = 2 个视频文件,各约 20 分钟,对应上下半场。** 不要再细分。

### 为什么是上下半场两段

| 设计点 | 原因 |
|---|---|
| **半场是必须的切点** | ByteTrack 跟踪 ID 在中场休息处必然崩(球员全员出画 5 分钟后回来,ID 全部重新分配)。半场只是把这个事实变成显式边界 |
| **半场内不再切分** | ByteTrack 在更长连续段里 ID 更稳;细分反而碎片化 |
| **每半场约 20 分钟刚好** | 在 MPS 上跑完整流水线约 6-8 分钟,可接受;`max_frames=0` 即可处理整段 |
| **失败可单独重跑** | 上半场识别有问题时,下半场报告不受牵连 |
| **机位漂移可独立处理** | 中场休息后机位可能被碰过,calibration 可独立校 |

### 不推荐的切法

| 切法 | 不推荐原因 |
|---|---|
| 一个完整 40 分钟视频 | 中场跟踪必断,产生跨场假轨迹,污染身份绑定;一处出错全场重跑 |
| 切成 5 分钟一段 | 跟踪 ID 太碎,跨段同一球员被判成不同人;手动复核工作量翻番 |
| 切成 10 分钟一段 | 同上,但程度轻一些 |

### 实际操作

**录制端**(理想情况):

- DJI 相机或类似设备,**上下半场分别开始/结束录制**,自然产出两个文件
- 命名:`<match_id>_h1.mp4`、`<match_id>_h2.mp4`
- 分辨率:维持现有 2688×1512 @ 60fps 即可(项目已验证)
- 比特率:不需要降级——更高质量小目标(球)更容易被检测

**如果只有一个长文件**(比如忘了停录):

```bash
# 假设 video.mp4 是 40 分钟,中场在第 20 分钟
ffmpeg -ss 0 -t 1200 -i video.mp4 -c copy match_h1.mp4
ffmpeg -ss 1300 -i video.mp4 -c copy match_h2.mp4   # 1300 = 21:40 跳过中场休息
```

`-c copy` 是关键(无重编码,秒级完成,无质量损失)。

---

## 2. 端到端流程

按这个顺序执行,**两个半场各跑一次**,最后合并。

### 2.1 准备(全场只做一次)

#### 把视频文件放好

```bash
mkdir -p videos/raw/<match_id>/
cp /path/to/match_h1.mp4 videos/raw/<match_id>/
cp /path/to/match_h2.mp4 videos/raw/<match_id>/
```

#### 启动工作区(可选,UI 流程)

```bash
.venv/bin/python scripts/12_serve_analysis_app.py
# 浏览器打开 http://localhost:8080,通过界面创建比赛
```

或者命令行直接 scaffold(下面 2.2 会示范)。

### 2.2 上半场分析

#### 创建 match 项目

```bash
.venv/bin/python scripts/08_new_match_project.py \
  --match-id <match_id>_h1 \
  --match-name "<比赛名 上半场>" \
  --video videos/raw/<match_id>/match_h1.mp4 \
  --analyze-team red
# 产出 matches/<match_id>_h1/ 目录骨架
```

#### 编辑 `matches/<match_id>_h1/config/match.yaml`

**关键改动:启用 ball_v1**

```yaml
detection:
  model: yolo11n.pt
  conf: 0.18
  imgsz: 1280
  sample_fps: 2.0
  # 启用自训练球检测器(0% → 83.3% recall):
  ball_model: runs/detect/ball_v1/weights/best.pt
  ball_conf: 0.05
  smoke_test:
    start_sec: 0.0
    duration_sec: 1200.0       # 整个 20 分钟
    max_frames: 0              # 0 = 不限制
```

**确认 roster、team color、metrics 等其他字段**(参考 `configs/match_red_mvp.yaml`)。

#### 标定上半场场地

```bash
.venv/bin/python scripts/09_prepare_match_review.py \
  --config matches/<match_id>_h1/config/match.yaml
.venv/bin/python scripts/serve_point_picker.py \
  --config matches/<match_id>_h1/config/match.yaml
# 浏览器里点击 15 个场地参考点,提交
```

#### 跑流水线

```bash
.venv/bin/python scripts/10_run_match_mvp.py \
  --config matches/<match_id>_h1/config/match.yaml
# 这会自动跑 00→07,大约 6-8 分钟(MPS)
```

#### 人工复核

工作区 UI 走两条任务卡:

1. **球员身份校验**:60 张 thumbnail,逐个确认/纠正
2. **球位置标注**:打开后会发现**ball_v1 已经把大多数球点预标好了**(83% recall),你只需:
   - 跳过明显已正确的(可批量"批准")
   - 修正少数误差超过 1m 的
   - 补充模型漏检的(剩余 ~17%)
   - **预期工作量从过去的 30-60 分钟降到 5-10 分钟**

### 2.3 下半场分析

#### 复用上半场资源(机位没动的前提下)

```bash
# 创建 h2 项目
.venv/bin/python scripts/08_new_match_project.py \
  --match-id <match_id>_h2 \
  --match-name "<比赛名 下半场>" \
  --video videos/raw/<match_id>/match_h2.mp4 \
  --analyze-team red

# 复用上半场 calibration(若机位未动)
cp matches/<match_id>_h1/data/interim/calibration/calibration.json \
   matches/<match_id>_h2/data/interim/calibration/

# 复用上半场 roster(直接 cp config 即可)
cp matches/<match_id>_h1/config/match.yaml \
   matches/<match_id>_h2/config/match.yaml
# 然后编辑修改 video_path 指向 h2,match_id/match_name 同步更新
```

#### 跑流水线 + 人工复核

```bash
.venv/bin/python scripts/10_run_match_mvp.py \
  --config matches/<match_id>_h2/config/match.yaml
```

人工复核流程同 2.2 的最后一步。

### 2.4 合并报告

```bash
.venv/bin/python scripts/16_merge_match_segments.py \
  --match <match_id>_h1 --label 上半场 \
  --match <match_id>_h2 --label 下半场 \
  --name "<比赛名 全场>" \
  --output <match_id>
# 产出 reports/combined/<match_id>/<timestamp>/{report.md,report.html,...}
```

---

## 3. 质量护栏:**每场都跑评估**

### 全自动 Layer A(2 秒,任何场比赛适用)

```bash
.venv/bin/python scripts/17_eval_against_gold.py \
  --match <match_id>_h1 \
  --match <match_id>_h2 \
  --output post_<match_id>
```

**Layer A 必须 PASS** 才能交付报告。fail 的常见情况:

| code | 原因 |
|---|---|
| `report_file_missing` | 流水线跑挂了,某个 csv 没生成 |
| `value_above_range` | distance_per_min > 500 → 几乎肯定是 calibration 错误 |
| `illegal_confidence_label` | 报告生成代码引入了不合法置信度,需要修代码 |
| `event_player_unknown` | 事件引用了名单里没有的球员,人工复核遗漏 |

### 半自动 Layer B(可选,15 分钟)

为这场比赛标一段 30 秒金标:

```bash
mkdir -p evals/gold/<match_id>_h1
# 写 manifest.yaml(参考 evals/gold/中青赛_1_20260506_213657/manifest.yaml)
# 从 review/ball_review/ball_review_points.csv 抽取 30s 窗口写到 ball_points.csv
.venv/bin/python scripts/17_eval_against_gold.py \
  --match <match_id>_h1 --gold auto --ball-source raw \
  --output post_<match_id>_h1_layerb
```

**期望数字**(基于 ball_v1 + 中青赛_1 baseline,新比赛允许有偏差):

- `ball_recall@1.5m` ≥ 60%(良好)/ ≥ 75%(优秀)
- `ball_position_error_m` ≤ 1.5m(良好)/ ≤ 1.0m(优秀)

如果显著低于这个区间(<40%),触发以下排查:

| 现象 | 可能原因 |
|---|---|
| recall 低,position error 也大 | 机位与训练数据差异大,需要 ball_v2(把这场比赛的标注加进训练) |
| recall 低,但匹配上的位置准 | 难帧太多(光照、运动模糊);考虑光流补球 |
| recall 高但 position error 大 | calibration 没校准好;用 `02_calibrate_field` 重做 |
| Layer A fail | 流水线本身有问题,先解决 Layer A |

---

## 4. 灾难恢复

### 中途流水线失败

```bash
# 如果 03_detect_track 失败:
.venv/bin/python scripts/03_detect_track.py --config matches/<match_id>_h1/config/match.yaml --device mps

# 如果 ball_model 路径错:检查 runs/detect/ball_v1/weights/best.pt 是否存在;否则重训练或清空 ball_model 字段
```

### 报告数字看起来不合理

```bash
# 1. 跑 Layer A
.venv/bin/python scripts/17_eval_against_gold.py --match <match_id>_h1 --output diag

# 2. 看 Layer A 报告里的 warn/info,定位异常字段

# 3. 如果是球员身份相关:重新走 06 → 13 → 工作区人工校验
.venv/bin/python scripts/06_assign_identities.py --config matches/<match_id>_h1/config/match.yaml

# 4. 如果是 calibration:
# 删除 matches/<match_id>_h1/data/interim/calibration/calibration.json
# 重新走 02 + serve_point_picker
```

### 想"如果不用 ball_v1"会怎样

```yaml
# 在 match.yaml 注释掉 ball_model 即可回退到原始流程
# ball_model: runs/detect/ball_v1/weights/best.pt
```

**不会破坏现有数据**——重新跑只覆盖 `data/interim/detections/`,`review/` 和 `reports/` 留存。

---

## 5. 一份完整的"复制粘贴"模板

```bash
#!/bin/bash
# 替换以下变量
MATCH_ID="<比赛 id,如 联赛_round3_20260520>"
MATCH_NAME="<显示名,如 中青赛 第三轮>"
VIDEO_H1="/path/to/match_h1.mp4"
VIDEO_H2="/path/to/match_h2.mp4"

# 1. 上半场 scaffold + 跑流水线
.venv/bin/python scripts/08_new_match_project.py \
  --match-id "${MATCH_ID}_h1" --match-name "${MATCH_NAME} 上半场" \
  --video "$VIDEO_H1" --analyze-team red

# 2. 编辑 matches/${MATCH_ID}_h1/config/match.yaml: 启用 ball_model,补完名单
# (人工编辑,无脚本捷径)

# 3. 准备 calibration → 浏览器点 → 提交
.venv/bin/python scripts/09_prepare_match_review.py --config matches/${MATCH_ID}_h1/config/match.yaml
.venv/bin/python scripts/serve_point_picker.py    --config matches/${MATCH_ID}_h1/config/match.yaml &

# 4. 跑流水线
.venv/bin/python scripts/10_run_match_mvp.py --config matches/${MATCH_ID}_h1/config/match.yaml

# 5. 浏览器人工复核(走 12_serve_analysis_app 的 UI)

# 6. 下半场重复(可复用 calibration + roster)
.venv/bin/python scripts/08_new_match_project.py \
  --match-id "${MATCH_ID}_h2" --match-name "${MATCH_NAME} 下半场" \
  --video "$VIDEO_H2" --analyze-team red
cp matches/${MATCH_ID}_h1/data/interim/calibration/calibration.json \
   matches/${MATCH_ID}_h2/data/interim/calibration/
# 编辑 matches/${MATCH_ID}_h2/config/match.yaml,把 video_path 改成 h2

.venv/bin/python scripts/10_run_match_mvp.py --config matches/${MATCH_ID}_h2/config/match.yaml

# 7. 合并报告
.venv/bin/python scripts/16_merge_match_segments.py \
  --match "${MATCH_ID}_h1" --label 上半场 \
  --match "${MATCH_ID}_h2" --label 下半场 \
  --name "${MATCH_NAME} 全场" \
  --output "${MATCH_ID}"

# 8. 评估护栏
.venv/bin/python scripts/17_eval_against_gold.py \
  --match "${MATCH_ID}_h1" --match "${MATCH_ID}_h2" \
  --output "post_${MATCH_ID}"

echo "Final report: reports/combined/${MATCH_ID}/<timestamp>/report.html"
```

---

## 6. 与本轮优化前的差异

| 步骤 | 优化前 | 优化后 |
|---|---|---|
| 视频切分 | 没有明确建议,有人提交完整 40 分钟,有人切碎 | **明确:每半场一段,各约 20 分钟** |
| 球检测 | YOLO11n COCO,生产实测 0% 真球召回 | ball_v1 + dual-model,**recall 提升至 ~83%** |
| 人工球点工作量 | 30-60 分钟/场(逐帧点击) | 5-10 分钟/场(主要是修正模型漏检) |
| 多段合并 | 手动拼接 / 不做 | `16_merge_match_segments` 一条命令 |
| 质量验证 | 靠主观看报告 | Layer A 必跑,Layer B 可选 |
| 跨场质量趋势 | 不可见 | 每场可叠加进 baseline,看模型是否退化 |

---

## 7. 引用文件

- 评估底座:[docs/08-…md](08-eval-harness-and-yolo-ball-baseline.md)
- ball_v1 训练记录:[docs/09-…md](09-custom-ball-head-v1.md)
- 双模型代码:[scripts/03_detect_track.py](../../scripts/03_detect_track.py)(`--ball-model` 选项)
- 多段合并:[scripts/16_merge_match_segments.py](../../scripts/16_merge_match_segments.py)
- 评估驱动:[scripts/17_eval_against_gold.py](../../scripts/17_eval_against_gold.py)
- 配置模板:[configs/match_red_mvp.yaml](../../configs/match_red_mvp.yaml)(已加 ball_model 选项)
- 项目根上下文:[CLAUDE.md](../../CLAUDE.md)
