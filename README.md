[English](#english-version) | [中文版本](#中文版本)

---

## English Version

# Guangzhou Residential Real Estate Investment Analysis

**End-to-end data pipeline: async scraping → financial modeling → interactive decision dashboard**

![Python](https://img.shields.io/badge/Python-3.9%2B-blue)
![Pandas](https://img.shields.io/badge/Pandas-2.x-green)
![Plotly](https://img.shields.io/badge/Plotly-interactive-orange)
![Playwright](https://img.shields.io/badge/Playwright-async-purple)

---

### Background

Using a representative residential property in Haizhu District, Guangzhou as a case study, this project compares asset management strategies: whole-unit rental, short-term rental (民宿), room-by-room subletting, and outright sale. Rather than relying on intuition, the entire decision process is driven by real market data — from automated scraping through financial modeling to an interactive report anyone can open in a browser.

---

### Key Features

1. **Async Multi-Source Scraper** — Playwright-based async scrapers for Beike (贝壳) housing-price maps, rental listings, and Tubatu (土巴兔) renovation cost estimates. Handles browser session management, login flows, pagination, and anti-bot request headers across concurrent contexts.

2. **Geospatial Data Enrichment** — Filters listings using a precise Haizhu District bounding box to remove mis-tagged locations. Extracts POI proximity features (subway stations, commercial districts) via Nominatim geocoding for downstream regression analysis.

3. **Three-Scenario DCF Financial Model** — For each rental strategy, computes annual NOI with occupancy rate, operating-expense, and rent-growth adjustments; deducts 30-year mortgage amortization payments; applies a terminal cap rate; and solves IRR via Newton-Raphson iteration. Outputs IRR, NPV, and the full annual cash-flow series for each scenario.

4. **Sensitivity Analysis** — Two-dimensional grid sweep across key DCF assumptions (discount rate, occupancy rate, renovation capex) to quantify how outcomes shift under different market conditions. Results are visualised as a heatmap in the dashboard.

5. **OLS Regression Analysis** — Fits unit-price vs. floor-area and rent-to-sale-ratio models to quantify market-level pricing relationships and identify spatial pricing patterns at the community level.

6. **Interactive Decision Dashboard** — A self-contained single HTML file with 10+ Plotly/Matplotlib charts, KPI summary cards, and an embedded JavaScript property calculator. No server required — open directly in any browser.

---

### Architecture

#### Three-Phase Pipeline

```
┌─────────────────────────────────────────────────────────────────┐
│  Phase 1 – Scrape  (Steps 1–3)                                  │
│                                                                  │
│  Beike Map Prices ──┐                                           │
│  Beike Rent Data  ──┼──► browser_engine.py  ──► CSV output     │
│  Tubatu Reno Costs──┘                                           │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│  Phase 2 – Analyse  (Steps 4–11)                                │
│                                                                  │
│  cleaner.py          → data normalisation, rent-to-sale ratio   │
│  dcf.py              → three-mode DCF, IRR, NPV, cash flows     │
│  sensitivity.py      → 2-D sensitivity grid sweep              │
│  poi_scraper.py      → subway / school POI collection           │
│  geo_features.py     → Nominatim geocoding, bbox filter, IRR   │
│  regression.py       → OLS on unit price & rent-to-sale ratio  │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│  Phase 3 – Report  (Steps 12–13)                                │
│                                                                  │
│  charts.py           → 10+ Plotly / Matplotlib figures          │
│  report.py           → assembles KPI cards + JS calculator     │
│                         → dashboard.html  +  summary.json       │
└─────────────────────────────────────────────────────────────────┘
```

#### Module Responsibilities

| Module | File | Responsibility |
|--------|------|----------------|
| Pipeline | `main.py` | Orchestrates all three phases; CLI + interactive mode selector |
| Scrapers | `scrapers/` | Async Playwright scrapers; browser session management, login, anti-bot |
| Cleaner | `processing/cleaner.py` | Outlier removal, unit normalisation, rent-to-sale ratio |
| Geo Features | `processing/geo_features.py` | Haizhu bbox filter, POI distance features, per-community IRR |
| DCF Engine | `models/dcf.py` | Mortgage amortisation → annual NOI → terminal value → IRR → NPV |
| Sensitivity | `models/sensitivity.py` | 2-D parameter sweep over discount rate / occupancy / capex |
| Regression | `models/regression.py` | OLS on unit price vs. area and rent-to-sale ratio |
| Charts | `visualization/charts.py` | 10+ charts: distributions, DCF cash flows, sensitivity heatmap, regression fit |
| Dashboard | `visualization/report.py` | Assembles KPI cards, chart embeds, and JS calculator into single HTML |

#### Key Design Decisions

- **Thin orchestrator** — `run_full_pipeline()` is ~30 lines; all logic lives in the three phase functions (`_run_scrape_phase`, `_run_analysis_phase`, `_run_report_phase`) and in dedicated modules.
- **Decoupled financial models** — DCF, sensitivity, and regression are pure NumPy / statsmodels functions with no visualisation dependencies, enabling isolated testing and reuse.
- **Smart caching** — `--mode analyze` reloads the most recent CSV snapshot, skipping scraping entirely; `--mode scrape --steps` lets you re-collect individual sources without touching the rest.
- **Self-contained dashboard** — single HTML output with no external dependencies; shareable by file transfer alone.
- **Bounded IRR solver** — Newton-Raphson constrained to [−90%, 2000%] to handle irregular cash-flow edge cases gracefully.

---

### Tech Stack

| Category | Tools |
|----------|-------|
| Web Scraping | Playwright (async), custom browser engine, anti-bot headers |
| Data Processing | Pandas, NumPy, GeoPandas |
| Financial Modelling | DCF, IRR (Newton-Raphson), NPV, mortgage amortisation |
| Sensitivity Analysis | Two-dimensional parameter grid sweep |
| Statistical Analysis | OLS Regression (statsmodels) |
| Visualisation | Matplotlib, Plotly (interactive HTML), HTML / CSS / JS |

---

### Project Structure

```
├── files/
│   ├── main.py                   # Pipeline entry point — orchestrates all three phases
│   ├── build_dashboard.py        # Standalone dashboard refresh (no re-scrape)
│   ├── scrapers/
│   │   ├── beike_map_scraper.py  # Housing price map scraper
│   │   ├── beike_rent_scraper.py # Rental listing scraper
│   │   ├── tubatu_scraper.py     # Renovation cost scraper
│   │   ├── poi_scraper.py        # POI geospatial scraper + Nominatim geocoding
│   │   └── browser_engine.py     # Shared Playwright session management
│   ├── processing/
│   │   ├── cleaner.py            # Data cleaning and rent-to-sale ratio
│   │   └── geo_features.py       # Geographic feature engineering, per-community IRR
│   ├── models/
│   │   ├── dcf.py                # DCF + IRR + NPV engine (three rental modes)
│   │   ├── sensitivity.py        # Two-dimensional sensitivity analysis
│   │   └── regression.py         # OLS regression analysis
│   └── visualization/
│       ├── charts.py             # Chart generation (Plotly + Matplotlib)
│       └── report.py             # Dashboard HTML builder
└── output/
    ├── dashboard.html            # Main output — interactive investment decision report
    └── summary.json              # Machine-readable KPI summary
```

---

### Quick Start

```bash
# Install dependencies
pip install pandas numpy matplotlib plotly playwright statsmodels geopandas
playwright install chromium

# --- Run modes ---

# Interactive mode: menu-driven mode and step selection
python3 files/main.py

# Full pipeline (scrape → clean → model → visualise)
python3 files/main.py --mode full

# Scrape only — collect all three sources, save CSV, then exit
python3 files/main.py --mode scrape

# Scrape only — collect specific sources (1=Beike prices, 2=Beike rent, 3=Tubatu)
python3 files/main.py --mode scrape --steps 1,3

# Analyse only — load most recent CSV snapshot, skip scraping entirely
python3 files/main.py --mode analyze

# Force re-scrape Beike (ignores cached CSV)
python3 files/main.py --mode full --refresh-beike

# Open Beike login window before scraping (required when session expires)
python3 files/main.py --mode full --login

# Refresh dashboard only — re-run all charts and report from existing data
python3 files/build_dashboard.py output
```

> **Note:** On the first run, or when the Beike session has expired, pass `--login` to open an interactive browser window for authentication. Login state is persisted in a local browser profile and is excluded from version control.

---

### Sample Results

Based on **30 property listings** in Haizhu District, Guangzhou (data collected May 2026):

| Metric | Value |
|--------|-------|
| Sample size | 30 listings |
| Avg. unit price | 34,500 CNY / ㎡ |
| Avg. monthly rent | 82.8 CNY / ㎡ |
| Gross rental yield | 2.88% |
| Rent-to-sale ratio | 1 : 417 |
| Renovation cases (Tubatu) | 180 real cases |
| Best IRR — with mortgage | **4.44%** (民宿 / short-term rental mode) |
| Best IRR — mortgage-free | **2.76%** (民宿 / short-term rental mode) |
| Best annual NOI — already owned | **66,100 CNY** (隔断分租 / subletting mode) |
| Best monthly cashflow — already owned | **5,500 CNY** (隔断分租 mode) |

---

### Data Sources & Disclaimer

Data scraped from **Beike (贝壳找房)** housing-price map and rental listings, and **Tubatu (土巴兔)** renovation cost database. Collected May 2026, Haizhu District, Guangzhou. For personal research and decision-making purposes only. Not investment advice.

---

---

## 中文版本

# 广州海珠区住宅资产管理策略分析

**端到端数据管道：异步采集 → 财务建模 → 交互式决策报告**

![Python](https://img.shields.io/badge/Python-3.9%2B-blue)
![Pandas](https://img.shields.io/badge/Pandas-2.x-green)
![Plotly](https://img.shields.io/badge/Plotly-交互式-orange)
![Playwright](https://img.shields.io/badge/Playwright-异步-purple)

---

### 项目背景

以广州市海珠区一套代表性住宅为案例，比较整租、民宿短租、隔断分租与出售的资产管理策略。为避免凭直觉决策，从零搭建了这套完整的数据驱动系统：从自动化采集真实市场数据，到财务建模量化各方案回报，再到生成任何人打开浏览器即可查看的交互式决策报告，全流程覆盖。

---

### 核心功能

1. **异步多源爬虫** — 基于 Playwright 的异步爬虫，采集贝壳找房二手房价格地图、租金挂牌数据及土巴兔装修报价；处理浏览器会话管理、登录流程、分页翻页和反爬虫请求头，多浏览器上下文并发执行。

2. **地理增强数据处理** — 基于海珠区精确边界框（bbox）过滤地址异常房源；通过 Nominatim 地理编码提取 POI 地理特征（地铁站距离、商圈覆盖情况），用于下游回归分析的影响因子量化。

3. **三场景 DCF 财务建模** — 针对整租、民宿、隔断分租三种模式，逐年计算 NOI（含出租率、运营费率、租金增长率），扣除等额还款月供，按终值资本化率计算退出价值，使用 Newton-Raphson 迭代求解 IRR，输出 IRR、NPV 及完整逐年现金流序列。

4. **敏感性分析** — 对折现率、出租率、初始装修投入等关键假设进行二维参数网格扫描，量化各方案在不同市场条件下的结果变化幅度，输出热力图展示风险暴露。

5. **OLS 回归分析** — 对单价 vs. 面积、租售比进行线性回归建模，量化市场层面的定价规律，并在小区级别识别空间定价差异。

6. **交互式决策 Dashboard** — 单个独立 HTML 文件，包含 10+ 张 Plotly/Matplotlib 图表、KPI 摘要卡片，以及嵌入式 JavaScript 房产投资计算器。纯前端，无需服务器，浏览器直接打开即可使用。

---

### 系统架构

#### 三阶段流水线

```
┌─────────────────────────────────────────────────────────────────┐
│  第一阶段 – 数据采集  （步骤 1–3）                               │
│                                                                  │
│  贝壳二手房价格 ──┐                                              │
│  贝壳租金数据   ──┼──► browser_engine.py  ──► CSV 输出          │
│  土巴兔装修报价 ──┘                                              │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│  第二阶段 – 建模分析  （步骤 4–11）                               │
│                                                                  │
│  cleaner.py          → 数据清洗、租售比计算                       │
│  dcf.py              → 三模式 DCF、IRR、NPV、逐年现金流          │
│  sensitivity.py      → 二维敏感性网格扫描                        │
│  poi_scraper.py      → 地铁站 / 学校 POI 采集                   │
│  geo_features.py     → Nominatim 地理编码、bbox 过滤、小区 IRR  │
│  regression.py       → 单价与租售比 OLS 回归                    │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│  第三阶段 – 报告生成  （步骤 12–13）                              │
│                                                                  │
│  charts.py           → 10+ 张 Plotly / Matplotlib 图表          │
│  report.py           → 组装 KPI 卡片 + JS 计算器                │
│                         → dashboard.html  +  summary.json       │
└─────────────────────────────────────────────────────────────────┘
```

#### 模块职责

| 模块 | 文件 | 职责 |
|------|------|------|
| 流水线入口 | `main.py` | 统筹三阶段执行；CLI 参数解析 + 交互式模式选择 |
| 爬虫层 | `scrapers/` | 异步 Playwright 爬虫；会话管理、登录、反爬虫处理 |
| 数据清洗 | `processing/cleaner.py` | 去除异常值、单位标准化、计算租售比 |
| 地理特征 | `processing/geo_features.py` | 海珠区 bbox 过滤、POI 距离特征提取、小区级 IRR 计算 |
| DCF 引擎 | `models/dcf.py` | 月供计算 → 年 NOI → 终值 → IRR（Newton-Raphson） → NPV |
| 敏感性分析 | `models/sensitivity.py` | 折现率 / 出租率 / 装修投入的二维参数网格扫描 |
| 回归分析 | `models/regression.py` | 单价 vs. 面积、租售比 OLS 回归建模 |
| 图表生成 | `visualization/charts.py` | 10+ 张图表：分布图、DCF 现金流、敏感性热力图、回归拟合 |
| 报告构建 | `visualization/report.py` | 将 KPI 卡片、图表嵌入、JS 计算器组装为单页 HTML |

#### 关键设计决策

- **薄编排层** — `run_full_pipeline()` 约 30 行，全部逻辑下沉到三个阶段函数（`_run_scrape_phase` / `_run_analysis_phase` / `_run_report_phase`）和各专属模块
- **财务模型与可视化解耦** — DCF、敏感性分析、回归均为纯 NumPy/statsmodels 函数，无可视化依赖，便于独立测试和复用
- **智能缓存策略** — `--mode analyze` 直接加载最新 CSV 快照跳过爬取；`--mode scrape --steps` 允许单独重采某一来源而不影响其他数据
- **单文件独立 Dashboard** — 输出为单个 HTML 文件，无外部依赖，通过文件传输即可分享
- **有界 IRR 求解器** — Newton-Raphson 设定 [−90%, 2000%] 边界，优雅处理现金流异常的极端情况

---

### 技术栈

| 类别 | 工具 |
|------|------|
| 网络爬虫 | Playwright（异步）、自定义浏览器引擎、反爬虫请求头 |
| 数据处理 | Pandas、NumPy、GeoPandas |
| 财务建模 | DCF 现金流折现、IRR（Newton-Raphson）、NPV、等额还款 |
| 敏感性分析 | 二维参数网格扫描 |
| 统计分析 | OLS 回归（statsmodels） |
| 数据可视化 | Matplotlib、Plotly（交互式 HTML）、HTML / CSS / JS |

---

### 项目结构

```
├── files/
│   ├── main.py                   # 主流程入口 — 统筹三阶段执行
│   ├── build_dashboard.py        # 独立刷新报告（无需重新爬取）
│   ├── scrapers/
│   │   ├── beike_map_scraper.py  # 贝壳二手房价格地图爬虫
│   │   ├── beike_rent_scraper.py # 贝壳租金挂牌爬虫
│   │   ├── tubatu_scraper.py     # 土巴兔装修报价爬虫
│   │   ├── poi_scraper.py        # POI 地理数据采集 + Nominatim 地理编码
│   │   └── browser_engine.py     # Playwright 会话管理（共享）
│   ├── processing/
│   │   ├── cleaner.py            # 数据清洗与租售比计算
│   │   └── geo_features.py       # 地理特征工程、小区级 IRR 计算
│   ├── models/
│   │   ├── dcf.py                # DCF + IRR + NPV 引擎（三种租赁模式）
│   │   ├── sensitivity.py        # 二维敏感性分析
│   │   └── regression.py         # OLS 回归分析
│   └── visualization/
│       ├── charts.py             # 图表生成（Plotly + Matplotlib）
│       └── report.py             # Dashboard HTML 构建器
└── output/
    ├── dashboard.html            # 主输出 — 交互式投资决策报告
    └── summary.json              # KPI 指标摘要（机器可读）
```

---

### 快速开始

```bash
# 安装依赖
pip install pandas numpy matplotlib plotly playwright statsmodels geopandas
playwright install chromium

# --- 运行模式 ---

# 交互模式：菜单引导选择运行模式和采集步骤
python3 files/main.py

# 完整流程（爬取 → 清洗 → 建模 → 生成报告）
python3 files/main.py --mode full

# 仅爬虫 — 采集全部三个来源，保存 CSV 后退出
python3 files/main.py --mode scrape

# 仅爬虫 — 指定来源（1=贝壳买房 2=贝壳租房 3=土巴兔）
python3 files/main.py --mode scrape --steps 1,3

# 仅分析 — 加载最近一次 CSV 快照，完全跳过爬虫
python3 files/main.py --mode analyze

# 强制重新采集贝壳数据（忽略缓存 CSV）
python3 files/main.py --mode full --refresh-beike

# 采集前打开贝壳登录窗口（会话过期时使用）
python3 files/main.py --mode full --login

# 仅刷新 Dashboard — 基于已有数据重新生成图表和报告
python3 files/build_dashboard.py output
```

> **提示：** 首次运行或贝壳会话过期时，加上 `--login` 参数会自动打开浏览器进行交互式登录。登录状态保存在本地浏览器配置文件中，已通过 `.gitignore` 排除出版本控制。

---

### 分析结果示例

基于广州市海珠区 **30 套** 房源数据（采集时间：2026 年 5 月）：

| 指标 | 数值 |
|------|------|
| 样本量 | 30 套 |
| 平均单价 | 3.45 万元 / ㎡ |
| 平均月租金 | 82.8 元 / ㎡ |
| 年化租金收益率 | 2.88% |
| 租售比 | 1 : 417 |
| 装修参考案例（土巴兔） | 180 个真实案例 |
| 最优 IRR（有贷款） | **4.44%**（民宿模式） |
| 最优 IRR（无贷款 / 已还清） | **2.76%**（民宿模式） |
| 最优年 NOI（已持有） | **6.61 万元**（隔断分租模式） |
| 最优月现金流（已持有） | **0.55 万元**（隔断分租模式） |

---

### 数据说明

数据来源于**贝壳找房**（二手房成交价格地图 + 租房挂牌）及**土巴兔**装修报价平台，采集时间 2026 年 5 月，范围限定广州市海珠区。仅供个人研究与决策参考，不构成投资建议。
