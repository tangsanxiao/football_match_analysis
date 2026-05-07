#!/usr/bin/env python3
"""Create a browser-based pixel coordinate picker for calibration points."""

from __future__ import annotations

import argparse
import base64
import html
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def resolve_path(path: Union[str, Path]) -> Path:
    path = Path(path)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def load_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def image_data_uri(image_path: Path) -> str:
    suffix = image_path.suffix.lower()
    mime = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def point_payload(points_yaml: Dict[str, Any]) -> List[Dict[str, Any]]:
    payload = []
    for point in points_yaml.get("points", []):
        payload.append(
            {
                "name": point.get("name", ""),
                "field_xy": point.get("field_xy"),
                "image_xy": point.get("image_xy"),
                "enabled": point.get("enabled", True),
            }
        )
    return payload


def build_html(
    image_path: Path,
    points: List[Dict[str, Any]],
    autosave: bool = False,
    save_endpoint: str = "/api/point",
    submit_endpoint: str = "/api/submit",
    points_label: str = "calibration_points.yaml",
    labeling_status: str = "",
    labeling_submitted_at: str = "",
    min_submit_points: int = 4,
) -> str:
    points_json = json.dumps(points, ensure_ascii=False)
    autosave_json = json.dumps(autosave)
    save_endpoint_json = json.dumps(save_endpoint)
    submit_endpoint_json = json.dumps(submit_endpoint)
    labeling_status_json = json.dumps(labeling_status)
    labeling_submitted_at_json = json.dumps(labeling_submitted_at)
    min_submit_points_json = json.dumps(min_submit_points)
    save_status_text = "自动保存到 YAML" if autosave else "点击图片写入当前点"
    hint_text = (
        '选中一个点位后，在图上点击对应场地点。坐标会自动写入 '
        f'<code>{html.escape(points_label)}</code>。标完后点击“提交打标”完成确认。'
        if autosave
        else '选中一个点位后，在图上点击对应场地点。坐标使用原图像素坐标，适合直接填入 '
        f'<code>{html.escape(points_label)}</code> 的 <code>image_xy</code>。'
    )
    data_uri = image_data_uri(image_path)
    title = "Football Calibration Point Picker"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5f7f2;
      --panel: #ffffff;
      --ink: #17211b;
      --muted: #66736b;
      --accent: #0f7b63;
      --line: #d7ded7;
      --warn: #a15c00;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 420px;
      gap: 16px;
      min-height: 100vh;
      padding: 16px;
    }}
    .stage, .side {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }}
    .stage-header, .side-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      color: var(--muted);
      font-size: 13px;
    }}
    .image-wrap {{
      position: relative;
      overflow: auto;
      height: calc(100vh - 58px);
      background: #20251f;
    }}
    img {{
      display: block;
      width: 100%;
      height: auto;
      cursor: crosshair;
      user-select: none;
    }}
    .marker {{
      position: absolute;
      width: 18px;
      height: 18px;
      border: 2px solid #111;
      border-radius: 50%;
      background: #f8d447;
      transform: translate(-50%, -50%);
      pointer-events: none;
      box-shadow: 0 0 0 2px rgba(255,255,255,.9);
    }}
    .marker span {{
      position: absolute;
      left: 14px;
      top: -10px;
      padding: 2px 5px;
      background: rgba(0,0,0,.78);
      color: white;
      border-radius: 4px;
      font-size: 11px;
      white-space: nowrap;
    }}
    .side {{
      height: calc(100vh - 32px);
      overflow: auto;
    }}
    .controls {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      padding: 12px;
      border-bottom: 1px solid var(--line);
    }}
    select, button, textarea {{
      font: inherit;
    }}
    select, button {{
      min-height: 34px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--ink);
    }}
    button {{
      cursor: pointer;
      font-weight: 600;
    }}
    button:disabled {{
      cursor: not-allowed;
      opacity: .48;
    }}
    button.primary {{
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
    }}
    .wide {{
      grid-column: 1 / -1;
    }}
    .saved {{
      color: var(--accent);
      font-weight: 700;
    }}
    .failed {{
      color: var(--warn);
      font-weight: 700;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }}
    th, td {{
      border-bottom: 1px solid var(--line);
      padding: 7px 8px;
      text-align: left;
      vertical-align: top;
    }}
    th {{
      position: sticky;
      top: 0;
      background: #f9faf7;
      z-index: 1;
    }}
    tr.active td {{
      background: #e8f3ef;
    }}
    .hint {{
      color: var(--muted);
      padding: 10px 12px;
      font-size: 13px;
      line-height: 1.45;
    }}
    textarea {{
      display: block;
      width: calc(100% - 24px);
      height: 220px;
      margin: 12px;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      resize: vertical;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
      line-height: 1.45;
    }}
    code {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }}
    a {{
      color: var(--accent);
      text-decoration: none;
      font-weight: 650;
    }}
    @media (max-width: 980px) {{
      main {{
        grid-template-columns: 1fr;
      }}
      .image-wrap, .side {{
        height: auto;
        max-height: none;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <section class="stage">
      <div class="stage-header">
        <strong>校准图点击取点</strong>
        <span><a href="/match">比赛信息</a> · <span id="cursor">x: -, y: -</span></span>
      </div>
      <div class="image-wrap" id="wrap">
        <img id="pitch" alt="calibration frame" src="{data_uri}">
      </div>
    </section>
    <aside class="side">
      <div class="side-header">
        <strong>点位列表</strong>
        <span id="saveStatus">{save_status_text}</span>
      </div>
      <div class="controls">
        <select id="pointSelect"></select>
        <button id="clearPoint">清除当前点</button>
        <button id="submitLabeling" class="primary wide">提交打标</button>
        <button id="copyYaml" class="primary">复制 YAML 片段</button>
        <button id="downloadYaml">下载片段</button>
      </div>
      <div class="hint">
        {hint_text}
      </div>
      <table>
        <thead>
          <tr>
            <th>点位</th>
            <th>场地坐标</th>
            <th>像素坐标</th>
          </tr>
        </thead>
        <tbody id="pointTable"></tbody>
      </table>
      <textarea id="yamlOut" spellcheck="false"></textarea>
    </aside>
  </main>

  <script>
    const points = {points_json};
    const autoSave = {autosave_json};
    const saveEndpoint = {save_endpoint_json};
    const submitEndpoint = {submit_endpoint_json};
    const initialLabelingStatus = {labeling_status_json};
    const initialSubmittedAt = {labeling_submitted_at_json};
    const minSubmitPoints = {min_submit_points_json};
    const image = document.getElementById('pitch');
    const wrap = document.getElementById('wrap');
    const cursor = document.getElementById('cursor');
    const select = document.getElementById('pointSelect');
    const table = document.getElementById('pointTable');
    const yamlOut = document.getElementById('yamlOut');
    const clearPoint = document.getElementById('clearPoint');
    const submitLabeling = document.getElementById('submitLabeling');
    const copyYaml = document.getElementById('copyYaml');
    const downloadYaml = document.getElementById('downloadYaml');
    const saveStatus = document.getElementById('saveStatus');

    let activeIndex = 0;
    let labelingSubmitted = initialLabelingStatus === 'submitted';

    function pointLabel(point) {{
      return point.name || 'unnamed';
    }}

    function renderSelect() {{
      select.innerHTML = '';
      points.forEach((point, index) => {{
        const option = document.createElement('option');
        option.value = String(index);
        option.textContent = `${{index + 1}}. ${{pointLabel(point)}}`;
        select.appendChild(option);
      }});
      select.value = String(activeIndex);
    }}

    function renderTable() {{
      table.innerHTML = '';
      points.forEach((point, index) => {{
        const row = document.createElement('tr');
        row.className = index === activeIndex ? 'active' : '';
        row.addEventListener('click', () => {{
          activeIndex = index;
          select.value = String(index);
          render();
        }});
        const field = Array.isArray(point.field_xy) ? `[${{point.field_xy[0]}}, ${{point.field_xy[1]}}]` : '';
        const imageXY = Array.isArray(point.image_xy) ? `[${{point.image_xy[0]}}, ${{point.image_xy[1]}}]` : '';
        row.innerHTML = `<td>${{index + 1}}. ${{escapeHtml(pointLabel(point))}}</td><td>${{field}}</td><td>${{imageXY}}</td>`;
        table.appendChild(row);
      }});
    }}

    function escapeHtml(value) {{
      return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
    }}

    function markerPosition(point) {{
      const scaleX = image.clientWidth / image.naturalWidth;
      const scaleY = image.clientHeight / image.naturalHeight;
      return {{
        left: point.image_xy[0] * scaleX,
        top: point.image_xy[1] * scaleY
      }};
    }}

    function renderMarkers() {{
      wrap.querySelectorAll('.marker').forEach(node => node.remove());
      points.forEach((point, index) => {{
        if (!Array.isArray(point.image_xy)) return;
        const marker = document.createElement('div');
        marker.className = 'marker';
        const pos = markerPosition(point);
        marker.style.left = `${{pos.left}}px`;
        marker.style.top = `${{pos.top}}px`;
        marker.innerHTML = `<span>${{index + 1}} ${{escapeHtml(pointLabel(point))}}</span>`;
        wrap.appendChild(marker);
      }});
    }}

    function yamlSnippet() {{
      const lines = ['points:'];
      points.forEach(point => {{
        lines.push(`  - name: ${{point.name}}`);
        lines.push(`    field_xy: [${{point.field_xy[0]}}, ${{point.field_xy[1]}}]`);
        if (Array.isArray(point.image_xy)) {{
          lines.push(`    image_xy: [${{point.image_xy[0]}}, ${{point.image_xy[1]}}]`);
        }} else {{
          lines.push('    image_xy:');
        }}
        lines.push('');
      }});
      return lines.join('\\n');
    }}

    function renderYaml() {{
      yamlOut.value = yamlSnippet();
    }}

    function enabledPoints() {{
      return points.filter(point => point.enabled !== false);
    }}

    function markedPoints() {{
      return enabledPoints().filter(point => Array.isArray(point.image_xy) && point.image_xy.length === 2);
    }}

    function renderSubmitState() {{
      const marked = markedPoints().length;
      const enabled = enabledPoints().length;
      submitLabeling.textContent = labelingSubmitted ? `已提交打标 (${{marked}}/${{enabled}})` : `提交打标 (${{marked}}/${{enabled}})`;
      submitLabeling.disabled = !autoSave || marked < minSubmitPoints;
    }}

    function render() {{
      renderSelect();
      renderTable();
      renderMarkers();
      renderYaml();
      renderSubmitState();
    }}

    function setStatus(message, className = '') {{
      saveStatus.textContent = message;
      saveStatus.className = className;
    }}

    async function persistPoint(index) {{
      if (!autoSave) return true;
      const point = points[index];
      labelingSubmitted = false;
      setStatus(`保存中: ${{pointLabel(point)}}`);
      try {{
        const response = await fetch(saveEndpoint, {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{
            name: point.name,
            image_xy: Array.isArray(point.image_xy) ? point.image_xy : null
          }})
        }});
        const result = await response.json();
        if (!response.ok || !result.ok) {{
          throw new Error(result.error || `HTTP ${{response.status}}`);
        }}
        setStatus(`已保存: ${{pointLabel(point)}}`, 'saved');
        return true;
      }} catch (error) {{
        setStatus(`保存失败: ${{error.message}}`, 'failed');
        return false;
      }}
    }}

    async function submitCurrentLabeling() {{
      const marked = markedPoints().length;
      const enabled = enabledPoints().length;
      if (!autoSave) {{
        setStatus('提交失败: 请通过 serve_point_picker.py 打开页面', 'failed');
        return;
      }}
      if (marked < minSubmitPoints) {{
        setStatus(`提交失败: 至少需要 ${{minSubmitPoints}} 个点，当前 ${{marked}}/${{enabled}}`, 'failed');
        return;
      }}
      setStatus('提交打标中...');
      submitLabeling.disabled = true;
      try {{
        const response = await fetch(submitEndpoint, {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ marked_points: marked, enabled_points: enabled }})
        }});
        const result = await response.json();
        if (!response.ok || !result.ok) {{
          throw new Error(result.error || `HTTP ${{response.status}}`);
        }}
        const jobText = result.job ? '，后台分析已启动' : '';
        setStatus(`打标已提交${{jobText}}: ${{result.marked_points}}/${{result.enabled_points}}`, 'saved');
        labelingSubmitted = true;
        if (window.parent && window.parent !== window && result.job) {{
          window.parent.postMessage({{ type: 'analysis_job_started', job: result.job }}, '*');
        }}
      }} catch (error) {{
        setStatus(`提交失败: ${{error.message}}`, 'failed');
      }} finally {{
        renderSubmitState();
      }}
    }}

    function eventToImageXY(event) {{
      const rect = image.getBoundingClientRect();
      const displayX = event.clientX - rect.left;
      const displayY = event.clientY - rect.top;
      const x = Math.round(displayX * image.naturalWidth / rect.width);
      const y = Math.round(displayY * image.naturalHeight / rect.height);
      return [Math.max(0, Math.min(image.naturalWidth - 1, x)), Math.max(0, Math.min(image.naturalHeight - 1, y))];
    }}

    image.addEventListener('mousemove', event => {{
      const [x, y] = eventToImageXY(event);
      cursor.textContent = `x: ${{x}}, y: ${{y}}`;
    }});

    image.addEventListener('click', async event => {{
      const clickedIndex = activeIndex;
      points[clickedIndex].image_xy = eventToImageXY(event);
      render();
      const saved = await persistPoint(clickedIndex);
      if (saved && activeIndex < points.length - 1) {{
        activeIndex += 1;
      }}
      render();
    }});

    select.addEventListener('change', () => {{
      activeIndex = Number(select.value);
      render();
    }});

    clearPoint.addEventListener('click', async () => {{
      delete points[activeIndex].image_xy;
      render();
      await persistPoint(activeIndex);
    }});

    submitLabeling.addEventListener('click', submitCurrentLabeling);

    copyYaml.addEventListener('click', async () => {{
      renderYaml();
      await navigator.clipboard.writeText(yamlOut.value);
      copyYaml.textContent = '已复制';
      setTimeout(() => copyYaml.textContent = '复制 YAML 片段', 1200);
    }});

    downloadYaml.addEventListener('click', () => {{
      renderYaml();
      const blob = new Blob([yamlOut.value], {{ type: 'text/yaml' }});
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = 'calibration_points_snippet.yaml';
      link.click();
      URL.revokeObjectURL(link.href);
    }});

    window.addEventListener('resize', renderMarkers);
    image.addEventListener('load', render);
    if (initialLabelingStatus === 'submitted') {{
      setStatus(initialSubmittedAt ? `已提交打标: ${{initialSubmittedAt}}` : '已提交打标', 'saved');
    }}
    render();
  </script>
</body>
</html>
"""


def write_picker(config: Dict[str, Any], points_yaml: Dict[str, Any], points_path: Path) -> Path:
    image_path = resolve_path(points_yaml["calibration"]["image_path"])
    if not image_path.exists():
        raise FileNotFoundError(f"Calibration image does not exist: {image_path}")

    interim_dir = resolve_path(config["match"]["interim_dir"])
    output_dir = interim_dir / "calibration"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "point_picker.html"
    points = point_payload(points_yaml)
    content = build_html(image_path, points, points_label=str(points_path))
    output_path.write_text(content, encoding="utf-8")
    return output_path


def default_points_path(config: Dict[str, Any]) -> Path:
    configured = config.get("calibration", {}).get("points_path")
    if configured:
        return resolve_path(configured)
    return resolve_path("configs/calibration_points_red_mvp.yaml")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/match_red_mvp.yaml")
    parser.add_argument("--points", default=None)
    args = parser.parse_args()

    config_path = resolve_path(args.config)
    config = load_yaml(config_path)
    points_path = resolve_path(args.points) if args.points else default_points_path(config)
    points_yaml = load_yaml(points_path)
    output_path = write_picker(config, points_yaml, points_path)

    print(f"point_picker: {output_path}")
    print(f"source_points: {points_path}")


if __name__ == "__main__":
    main()
