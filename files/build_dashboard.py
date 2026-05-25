#!/usr/bin/env python3
"""
独立刷新决策端 HTML 报告。

前提：已运行过 main.py，生成了：
  output/summary.json       — KPI 指标数据
  output/charts/*.html      — Plotly 交互图表
  output/charts/*.png       — Matplotlib 静态图表

用法：
  python build_dashboard.py             # 使用默认 output/ 目录
  python build_dashboard.py /path/to/output  # 指定输出目录
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from visualization.report import generate_dashboard

output_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "output"

if not output_dir.exists():
    print(f"[错误] 目录不存在: {output_dir}")
    print("请先运行 python main.py 生成图表和摘要数据。")
    sys.exit(1)

if not (output_dir / "summary.json").exists():
    print(f"[警告] 未找到 summary.json，KPI 卡片将显示占位数据")

path = generate_dashboard(output_dir)
print(f"[完成] 决策报告已刷新: {path.absolute()}")
