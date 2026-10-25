# QuickScene

[English](README.md) | **简体中文**

X-Plane 12 的地景制作工作台，**城市**和**机场**都能做，在浏览器里操作：

- **城市地景**：在地图上画区域，从 OpenStreetMap 生成（建筑轮廓和高度 → X-Plane 12 自带 facade → DSF，并屏蔽区域内的
  默认 autogen 建筑），再逐栋精修：高度、指定任意库里的 facade、换成自己的 3D 模型，添加物体、树木、地面多边形和线
- **机场**，参照 WED 的设计：从 X-Plane 自带机场数据库、Scenery Gateway、已安装的地景包、OpenStreetMap 或一条空白跑道
  开始；在地图上编辑跑道、道面、标线、标志牌、机位、ATC 滑行路由、灯光、频率和 3D 地景；检查；导出 apt.dat + DSF 并安装
- **地景素材库浏览器**，带 3D 预览：X-Plane 能看到的所有库（自带库，以及 Custom Scenery 里的 OpenSceneryX、MisterX、
  SAM、RuScenery 等）和你自己的模型
- **导入入口**，让专业模型和已有工作接得进来：X-Plane OBJ8（XPlane2Blender 导出）、Wavefront `.obj`/`.mtl` 和 glTF
  `.gltf`/`.glb`（Blender、SketchUp、3ds Max 等）转换成 OBJ8；任意已有地景包的 DSF 内容（WED 工程、Gateway 机场、
  其他插件）；X-Plane、Gateway 或已安装包里的机场 apt.dat

QuickScene 在 v0.1.1 及以前叫 *xp12-cityscene-gen* / *CityScene*，那些版本生成的地景包仍然可以导入。

## 下载（Windows）

不需要 Python：从 [Releases](https://github.com/han14777/quickscene/releases/latest) 下载 Windows 压缩包，
解压到任何可写的位置，双击 exe（`QuickScene.exe`；v0.1.1 及以前是 `CityScene.exe`），浏览器会打开界面。首次启动时会从
Laminar Research（files.x-plane.com，SHA-256 校验）下载 DSFTool 到 `tools` 文件夹，并自动找到 X-Plane 12。
项目和生成的地景都保存在解压出来的文件夹里。

以下内容是从源码运行。

## 环境

- Python 3.11+：`pip install -e ".[dev]"`（pyosmium、Pillow、numpy、shapely、pyproj 等）
- X-Plane 12（安装位置自动从 `%LOCALAPPDATA%\x-plane_install_12.txt` 读取）
- Laminar [XPTools](https://developer.x-plane.com/tools/xptools/) 里的 `DSFTool.exe`：首次使用时自动下载到 `tools/`，
  也可以在"本机设置"里填自己的路径（保存到 `config/local.yaml`，不进 git）
- 浏览器从公共 CDN 加载 Leaflet、leaflet-draw 和 three.js，地图瓦片来自 OpenStreetMap / Esri

## 启动界面

```
python -m quickscene gui        # 或双击 start_gui.bat
```

默认英文，标题旁的 EN / 中文 切换。面板顶部在**城市地景**和**机场**之间切换；**素材库…** 随时打开地景素材库。
地图右上角的图层切换提供 OpenStreetMap、卫星影像，以及你在本机设置里填的影像瓦片网址。

## 地景素材库与 3D 预览

X-Plane 能看到的每个 `library.txt` 都会建立索引（一般装几十个库约 6 万个素材；索引会缓存，库有变化时自动更新）。
可按路径搜索，按库、类型、分类（建筑、植被、车辆、人物、街道设施、机场、地面、线等）筛选。选中即 3D 预览：

| 类型 | 预览 |
|------|------|
| `.obj` | 带贴图的模型（解码 DDS），灯显示为点；最近一级 LOD |
| `.fac` | 第 1 类 facade 按墙面分段拼成一栋楼；第 2 类 facade 显示为贴图方盒 |
| `.for` | 一组树：X-Plane 12 的 3D 树模型，或经典的十字交叉面片 |
| `.pol` / `.lin` | 按真实比例铺在地面上的纹理 |
| `.agp` / `.str` | 按文件定义摆放的物体 |

"使用"把素材填到你正在选择的地方；"复制路径"复制库路径。**项目模型**页列出当前项目自己的模型，并可导入新模型（见下）。

每个地景包都会在 `README.txt` 和结果面板里列出需要的库，并对没有任何已安装库提供的素材发出警告。

## 导入 3D 模型

素材库"项目模型"页的"导入 3D 模型…"（或替换建筑时的"浏览…"）支持：

- **X-Plane `.obj` / `.agp`**：连同其引用的贴图复制进项目
- **Wavefront `.obj` + `.mtl`** 和 **glTF `.gltf` / `.glb`**：转换成 OBJ8。每种材质的贴图（或纯色）合并进一张贴图图集，
  重算纹理坐标，应用节点变换，缺失的法线自动计算。可设置缩放（厘米填 0.01，英寸填 0.0254）和朝上的轴（Z 轴朝上的文件选 Z）。
  超出 0..1 重复的 UV 会被折回并提示；要完全准确请先把贴图烘焙到 0..1

导入的模型放在 `jobs/<项目>/models/`（或 `airports/<项目>/models/`），生成时复制进地景包。

## 城市地景

面板一开始有**新建项目**、**打开项目**、**导入地景包**三个选项。新建项目：

1. 搜索地点，用地图左上角的工具画矩形或多边形（可以画多个）
2. 项目设置：名称、默认高度、渲染距离、OSM 数据来源，可选 **OpenStreetMap 树木**（单棵树、行道树、林地；树种可从素材库选）。
   树木会避开生成的建筑和道路
3. "保存选区并生成"：运行时显示进度和日志；完成后建筑按高度着色显示在地图上
4. 精修（见下），然后退出 X-Plane，点"安装到 X-Plane"

### 编辑建筑

点击建筑：保留并改高度（其他部分同比例缩放）、换 facade 风格或**指定任意库里的某个 facade**；删除；或换成模型（素材库里的或
自己的），可设朝向和东 / 北偏移。修改按 OSM 建筑保存在 `jobs/<项目>/overrides.json`，重新下载 OSM 也不会丢。
"下载 Blender 参考模型"把这栋楼以放置点为原点导出成 Wavefront OBJ，在上面建模后用
[XPlane2Blender](https://github.com/X-Plane/XPlane2Blender) 导出即可对齐。

### 地景物体与细节

第 6 步可以从素材库或自己的模型添加任何东西：**物体**（点击放置，可连续放置）、**facade**、**森林**（区域、沿线、或每个点
一棵树）、**地面多边形**、**线 / 物体串**。在地图或列表里选中后可拖动整体或顶点、旋转（R / Shift+R）、复制（Ctrl+D）、
删除（Del）、撤销（Ctrl+Z）；物体可以离地放置（`OBJECT_AGL`）。物体保存在 `jobs/<项目>/placements.json`；
"保存并重新生成"不需要重新下载就能重建 DSF。

"从其他地景包导入物体"会读取任意地景包的 DSF（用 DSFTool 反编译），把区域内的物体、facade、森林、多边形和线导入为可编辑的
物体，保留 facade 墙面类型、UV 贴图的多边形和曲线；包内自带的模型复制进本项目。

### 导入 QuickScene 生成的地景包

每个地景包都在 `<包>/quickscene/` 里带着项目数据（区域、设置、修改、放置的物体、清洗后的 OSM 建筑），所以可以还原成项目：
开始时选"导入地景包"，或运行 `python -m quickscene import <地景包文件夹>`。原包不会被改动。

## 机场

切换到**机场**。**新建机场**可以从这些来源开始：

| 来源 | 得到什么 |
|------|----------|
| X-Plane 自带机场数据库 | X-Plane 自带的约 3.8 万个机场（Global Airports 的 apt.dat，首次建立索引）：跑道、道面、标线、标志牌、机位、ATC 路由、流向 |
| Scenery Gateway | gateway.x-plane.com 上的推荐版本，包括它的 3D 物体和 facade |
| 已安装的地景包 | 它的 apt.dat，可选同时导入 DSF 里的全部物体、facade、森林和多边形；包内模型复制进项目 |
| OpenStreetMap | 草稿：跑道（按 `ref` 或方位命名两端）、带中线灯和道面的滑行道中线、停机坪、背向滑行道的机位、风向袋、塔台、边界、航站楼和机库（facade） |
| 空白 | 在地图中心放一条 1500 米跑道 |

编辑器的用法和 WED 一样：**工具**新建各类要素（跑道、直升机坪、道面、标线、边界、滑行路由、机位、风向袋、灯标、塔台视点、
标志牌、PAPI / VASI，以及素材库里的物体 / facade / 森林 / 地面 / 线）；**机场内容**树列出所有东西；**属性**用 apt.dat 自己的
取值表编辑选中项（道面、标志、进近灯、标线和灯的类型、机位类型和机型、ICAO 翼展等级、航司、活动区等）。点击线或道面上的某个点
可以单独编辑它，并拖动贝塞尔控制柄。支持撤销 / 重做、Ctrl+S、历史保存（可恢复），ATC 流向等行可以按文本编辑。

每次保存都会运行**检查**（参照 WED 的校验）：跑道编号、对向配对和方位、长宽、内移入口、机位（名称、类型、机型、是否在边界内）、
滑行路由网络（缺节点、零长度、互不连通）、频率、标志牌语法、缺少边界 / 风向袋 / 塔台视点等。有错误时不能导出；点击问题可跳转。

**导出**写出 `build/<包>/Earth nav data/apt.dat`（1200 格式；写完回读核对数量）和每个瓦片一个的 DSF（DSFTool 编译后反编译核对），
复制项目模型，列出需要的库，可选隐藏边界内 X-Plane 默认的物体 / 建筑 / 树木。**安装**把包链接到 Custom Scenery，排在
Global Airports 之前。apt.dat 里的所有内容在导入 → 编辑 → 导出之间都保留，包括贝塞尔曲线、ATC 流向和地勤车路线。

## 命令行

```
python -m quickscene new sydney_cbd --bbox 151.200,-33.875,151.215,-33.860 --render-distance 30
python -m quickscene build sydney_cbd            # fetch → clean → classify → build_dsf
python -m quickscene build sydney_cbd --steps clean,classify,build_dsf   # 改了设置后，不重新下载
python -m quickscene install sydney_cbd
python -m quickscene uninstall sydney_cbd
python -m quickscene list
python -m quickscene reference sydney_cbd way/123   # 把一栋楼导出成给 Blender 用的 Wavefront OBJ
python -m quickscene import "X-Plane 12/Custom Scenery/CityScene_sydney_cbd"   # 地景包 → 项目
```

也可以用 `--zone area.geojson`；`--osm-file 文件.osm.pbf`（或下载链接）从文件读取 OSM。

## 从文件读取 OSM 数据（BBBike、Geofabrik）

Overpass 服务器繁忙时，在项目设置里选"OSM 文件（.osm.pbf）或下载链接"，填本地文件或链接：
[BBBike](https://extract.bbbike.org/)（现成的城市文件或自定义区域；"从 BBBike 获取这片区域…"会自动填好所画的轮廓，
"查看 BBBike"在文件就绪后自动填入链接）或 [Geofabrik](https://download.geofabrik.de/)（按国家或州）。
用 [pyosmium](https://osmcode.org/pyosmium/) 读取。

## 渲染距离

X-Plane 12 自带 facade 在 8–18 千米外不再绘制，之后由合并的低模体块接替，直到渲染距离（默认 30 千米，
`--render-distance`，`dsf.render_distance_km`）。

## 城市生成流程

| 步骤 | 做什么 |
|------|--------|
| fetch | 按网格分块从 Overpass 下载 `building`、`building:part`、`highway`（打开树木时还有树和林地），按项目缓存 |
| clean | 组装轮廓和关系，保留质心在区域内的建筑，拆分 `building:part`，补高度（`height` → `building:levels` × 层高 → 默认值），切掉或去掉压在路上的建筑，去重、消除重叠 |
| classify | 把建筑分为高层 / 中层 / 低层和历史建筑，选楼层范围合适的自带 facade（或编辑里指定的 facade） |
| build_dsf | 每个 1° 瓦片一个 DSF：facade、模型、放置的物体、OSM 树木，以及屏蔽默认物体和 facade 的矩形排除区；DSFTool 编译后反编译核对没有丢失；远景体块直到渲染距离 |

## 文件

| 路径 | 内容 |
|------|------|
| `config/settings.yaml` | 全局默认：高度、清洗、facade、排除区、树木 |
| `config/local.yaml` | 本机路径（X-Plane、DSFTool）和 OSM 服务器，不进 git |
| `jobs/<项目>/` | 城市项目：`job.json`、`zone.geojson`、`raw/`、`processed/`、`overrides.json`、`placements.json`、`models/`、`build.log` |
| `airports/<项目>/` | 机场项目：`airport.json`（模型）、`models/`、`history/`（历史保存） |
| `cache/` | 素材库索引、Global Airports 索引、转换后的贴图 |
| `build/<包>/` | 生成的地景包，安装时链接到 Custom Scenery |

## 安装做了什么

- 在 `Custom Scenery/` 里建一个指向 `build/<包>/` 的文件夹链接（符号链接，或任何用户都能建的 junction）
- 带时间戳备份 `scenery_packs.ini`，把本包一行插在 `*GLOBAL_AIRPORTS*` 前面，其他行不动（排除区只对排在下面的包生效）
- X-Plane 运行时拒绝安装或卸载（它退出时会重写 ini）

## 已知限制

- 生成的地景还没有在游戏里逐项验证（树木、放置的物体、机场导出）
- 没有 OSM `height` / `building:levels` 的建筑用默认高度；X-Plane 12 的通用 facade 没有单层款式
- 预览是近似的：没有动画、没有夜间灯光，第 2 类 facade 显示为贴图方盒
- 模型导入把所有材质合并成一张图集（OBJ8 只能有一张贴图）；重复平铺的贴图会被折回
- 机场编辑器显示贝塞尔曲线和控制柄，但新画的线和道面是直线段（把某个点设为"曲线"即可加控制柄）

## 打包发行版

```
pip install pyinstaller
powershell -ExecutionPolicy Bypass -File packaging/build_release.ps1
```

生成 `dist/QuickScene-<版本>-windows-x64.zip`。推送 `v*` 标签会在 GitHub Actions 上执行同样的打包。

## 测试

```
python -m pytest -q
```

不联网；端到端测试会用 DSFTool 编译真实的 DSF（没有 DSFTool 时跳过）。

## 许可

Copyright (C) 2026 Hanyi Zhang

本程序是自由软件：你可以依据自由软件基金会发布的 GNU 通用公共许可证（第 3 版或任何更新版本）重新发布和修改它。
见 [LICENSE](LICENSE)。

许可只涵盖本工具的源代码。它生成的地景包含 OpenStreetMap 数据（ODbL），并引用 X-Plane 的库，这些都保留各自的许可。

## 数据与署名

- 建筑、树木、OSM 机场草稿：© OpenStreetMap contributors，ODbL 1.0。发布地景时请保留包里 README.txt 的署名
- Facade 和默认素材：X-Plane 12 自带库（Laminar Research）
- 你用到的第三方库遵循其作者的条款，发布依赖它们的地景前请先确认
- Scenery Gateway / X-Plane 自带数据库里的机场：Gateway 社区以 GPL v2+ 发布的地景
- 编辑器里的卫星影像：Esri World Imagery，仅用于显示参考；从中提取数据前请确认 Esri 的条款（或通过自定义影像网址使用你有权
  使用的影像）
