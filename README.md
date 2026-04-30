# 面向智能空间的多模态感知数据采集系统和分析平台

本项目是一个适合本科毕业设计展示的最小可运行版本，目标是演示“异构设备统一接入、传感器数据规范化处理、统一存储与可视化展示”的完整闭环。

当前支持三类设备：

- 摄像头
- 树莓派环境采集节点
- WT901 蓝牙采集节点

当前平台不再内置模拟环境数据，前端展示只依赖真实树莓派节点和 WT901 蓝牙节点上报。平台现在同时支持 HTTP 和 MQTT 两种协议，其中树莓派接入真实 SEN0501 环境传感器时建议走 MQTT，WT901 蓝牙节点则通过本机 BLE 采集后使用 HTTP 上传；后续如果替换为其他真实传感器，平台后端接口和数据库结构无需修改，只需要替换对应采集层。

时间处理说明：

- 后端与数据库统一按 UTC 标准时间存储，便于不同来源设备做规范化管理
- 前端页面按浏览器本地时间显示，因此页面上看到的是“本地时间格式化后的采集时间”
- 树莓派默认每 5 秒上传一次温度、湿度、大气压、海拔、紫外线和环境光，所以页面上的时间可能比“此刻时间”落后几秒，这是正常现象
- WT901 蓝牙节点当前不主动写入 RATE 配置，按设备默认约 10Hz 回传节奏使用；上传层默认每 0.1 秒检查一次最新帧，页面默认每 0.1 秒刷新一次
- 蓝牙运动数据在数据库与本地标准化文件中保留毫秒级时间戳，便于展示高频变化

## 1. 项目目录结构

```text
smartspace-platform/
├─ app/                              # 平台后端与网页仪表盘代码
│  ├─ services/                      # 业务服务模块，封装采集接入、标准化、导出和回溯逻辑
│  │  ├─ dataset_export.py           # 数据查询与对齐导出服务，生成 CSV/JSON/manifest ZIP
│  │  ├─ environment_analysis.py     # 空间环境状态分析服务，生成舒适度、运动适宜性和调整建议
│  │  ├─ imu_activity.py             # IMU 活动识别服务，深度模型优先并提供规则兜底
│  │  ├─ imu_deep_model.py           # 可选 1D-CNN + GRU/LSTM 推理模块，加载训练好的 WT901 模型
│  │  ├─ knowledge_graph.py          # 轻量知识图谱服务，生成空间、设备、传感器和文件对象关系
│  │  ├─ mqtt_listener.py            # MQTT 接入服务，接收树莓派等节点的持续上报
│  │  ├─ normalizer.py               # 数据标准化服务，校验类型、统一时间并拆分 metrics 入库
│  │  ├─ timeline.py                 # 历史回溯服务，按目标时间查询多模态状态
│  │  └─ video.py                    # 摄像头服务，提供 MJPEG 视频流、抓拍和录像分段
│  ├─ static/                        # 仪表盘静态资源
│  │  └─ style.css                   # 页面布局、卡片、时间轴和导出面板样式
│  ├─ templates/                     # Jinja2 页面模板
│  │  └─ index.html                  # 主仪表盘页面，包含实时展示、历史回溯和数据导出交互
│  ├─ __init__.py                    # Python 包标记文件
│  ├─ constants.py                   # 设备类型、传感器类型、单位映射等全局常量
│  ├─ crud.py                        # 数据库查询与写入函数封装
│  ├─ database.py                    # SQLite/SQLAlchemy 连接配置
│  ├─ knowledge_graph.json           # 知识图谱种子三元组，用于模拟语义关联关系
│  ├─ main.py                        # FastAPI 应用入口，集中定义页面和 API 路由
│  ├─ models.py                      # 关系型数据库 ORM 模型：device、sensor、sensor_data
│  ├─ schemas.py                     # API 请求体和响应体模型，支撑自动校验与接口文档
│  └─ storage.py                     # 运行时文件存储工具，管理原始包、标准化文件、对象索引等
├─ runtime_data/                     # 运行时生成数据，不作为源代码提交
│  ├─ db/                            # SQLite 数据库文件
│  ├─ raw_uploads/                   # 原始上传包备份
│  ├─ standardized_data/             # 标准化后的传感器 JSONL 数据
│  ├─ camera_frames/                 # 摄像头抓拍图片与抓拍记录
│  ├─ camera_video/                  # 摄像头分段录像文件
│  ├─ normalized_records/            # 规范化处理结果日志
│  ├─ source_audit/                  # 数据来源审计日志
│  ├─ multimodal_index/              # 图片、视频、原始 JSON 等文件对象索引
│  ├─ knowledge_graph/               # 知识图谱运行时快照
│  ├─ exports/                       # 对齐导出的多模态数据集 ZIP
│  └─ models/                        # 可选深度学习模型文件，例如 imu_activity_cnn_gru.pt
├─ bluetooth_node/                   # 本机 WT901 蓝牙节点接入脚本
│  ├─ collector.py                   # BLE 通知采集与加速度/角速度帧解析
│  └─ uploader.py                    # 将 WT901 最新帧转换为统一 metrics 后上传到平台
├─ raspberry_pi/                     # 树莓派环境采集节点脚本
│  ├─ collector.py                   # 真实传感器读取入口，预留 SEN0501/DHT22/BME280 接口
│  └─ uploader.py                    # 环境数据上传脚本，支持 MQTT 和 HTTP 两种传输方式
├─ scripts/                          # 项目辅助脚本
│  ├─ init_db.py                     # 初始化数据库表、设备档案和传感器档案
│  └─ train_imu_cnn_gru.py           # 训练腰部佩戴 WT901 的 1D-CNN + GRU/LSTM 活动识别模型
├─ requirements.txt                  # Python 依赖清单
├─ run.py                            # 本地开发启动入口
├─ start.bat                         # Windows CMD 一键启动脚本
├─ start.ps1                         # PowerShell 一键启动脚本
└─ README.md                         # 项目说明文档
```

## 2. 运行环境

- Python 3.11
- Windows / Linux / macOS 均可
- 本地 USB 摄像头可选，没有摄像头时页面会显示占位画面

## 3. 安装与启动步骤

### 3.1 安装依赖

```bash
pip install -r requirements.txt
```

### 3.2 初始化数据库和演示数据

```bash
python scripts/init_db.py
```

执行后会自动创建：

- `runtime_data/db/smartspace.db` 数据库文件
- 1 个树莓派环境采集节点设备
- 1 个 WT901 蓝牙采集节点设备
- 1 个摄像头设备
- 12 个传感器记录
  - 温度、湿度、大气压、海拔、紫外线、环境光
  - 加速度 X/Y/Z
  - 角速度 X/Y/Z
- 不写入任何演示历史数据

### 3.3 启动后端服务

```bash
python run.py
```

如果你已经把虚拟环境准备好，也可以直接使用项目自带的一键启动脚本：

```powershell
.\start.ps1
```

或：

```bat
start.bat
```

如需临时使用内存数据库验证接口，也可以先设置：

```bash
set SMARTSPACE_DATABASE_URL=sqlite:///:memory:
```

启动后访问：

- 仪表盘页面：[http://127.0.0.1:8000](http://127.0.0.1:8000)
- 接口文档：[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

注意：

- `0.0.0.0` 是服务监听地址，不是浏览器访问地址
- 浏览器中请始终使用 `http://127.0.0.1:8000/` 或 `http://localhost:8000/`

### 3.4 启动树莓派真实上传脚本

在另一个终端执行：

```bash
python raspberry_pi/uploader.py
```

该脚本默认每 5 秒上传一次真实环境数据，包含温度、湿度、大气压、海拔、紫外线和环境光，默认走 MQTT 协议。
默认会按全新初始化后的设备档案使用 `device_id=1` 作为树莓派环境采集节点；如果你的数据库不是全新初始化的，请先通过 `/devices` 确认节点编号，再设置：

```bash
set SMARTSPACE_DEVICE_ID=1
```

### 3.5 启动 WT901 蓝牙采集上传脚本

在本机另一个终端执行：

```bash
python bluetooth_node/uploader.py
```

该脚本会：

- 通过 BLE 搜索并连接 WT901SDCL-BT50
- 解析加速度和角速度
- 当前代码不主动修改 WT901 的 RATE 参数，默认按设备常见默认值约 10Hz 接收通知数据
- 上传层默认每隔 0.1 秒检查并上传一帧最新运动数据到平台，若没有新通知帧则不会重复上传旧数据

默认优先使用当前项目内置的 WT901 设备信息：

- 名称：`WTSDCL`
- MAC：`E5:64:B0:59:D2:42`

脚本默认会先请求后端自动查找 `bluetooth_node` 类型设备；如果数据库里还没有 WT901 蓝牙节点档案，会自动创建后再上传。

如果你想强制指定设备编号，也可以手动设置：

```bash
set SMARTSPACE_BLUETOOTH_DEVICE_ID=2
```

### 3.6 MQTT Broker 说明

如果你要测试树莓派 MQTT 接入，需要先准备一个 MQTT Broker，例如本机或局域网中的 Mosquitto。

默认配置如下：

- Broker Host：`127.0.0.1`
- Broker Port：`1883`
- Topic 前缀：`smartspace/sensor/upload`

可通过环境变量修改：

```bash
set SMARTSPACE_MQTT_HOST=127.0.0.1
set SMARTSPACE_MQTT_PORT=1883
set SMARTSPACE_MQTT_TOPIC_PREFIX=smartspace/sensor/upload
```

## 4. 标准化存储设计说明

### 4.1 设计目标

平台强调“异构设备统一接入”和“传感器数据规范化存储”。

当前接入协议支持：

- HTTP：用于接口调试、Swagger 演示和本地快速验证
- MQTT：用于树莓派真实节点持续上报，建议用于 SKU:SEN0501
- BLE：用于 WT901 蓝牙节点本机采集，随后统一通过 HTTP 上传

虽然当前演示环境节点与蓝牙运动节点两类数据，但上传接口并不直接接收平铺字段，例如：

```json
{
  "temperature": 25.6,
  "humidity": 61.2
}
```

而是要求使用统一上报结构：

```json
{
  "device_id": 1,
  "device_type": "sensor_node",
  "timestamp": "2026-04-14T15:00:00Z",
  "metrics": [
    {
      "sensor_type": "temperature",
      "value": 25.6,
      "unit": "C"
    },
    {
      "sensor_type": "humidity",
      "value": 61.2,
      "unit": "%"
    }
  ]
}
```

### 4.2 后端规范化处理内容

后端在 `app/services/normalizer.py` 中统一完成以下处理：

- 校验 `device_type` 是否属于已支持类型
- 校验 `sensor_type` 是否属于已支持类型
- 校验 `unit` 是否与 `sensor_type` 匹配
- 统一 `timestamp` 为 ISO 8601 格式并转为 UTC
- 将 `metrics` 拆分后映射到 `sensor_data` 表
- 如某传感器尚未建档，则自动补建标准化 `sensor` 记录
- 将原始上传包保存到 `runtime_data/raw_uploads/`
- 将本地标准化后的单条传感器数据保存到 `runtime_data/standardized_data/`
- 将摄像头视频流持续保存为 MP4 到 `runtime_data/camera_video/`
- 将摄像头视频流按固定时间间隔抓拍并保存到 `runtime_data/camera_frames/`
- 将规范化结果保存到 `runtime_data/normalized_records/`
- 将来源信息保存到 `runtime_data/source_audit/`
- 将视频、图片、原始上传 JSON 和标准化文件登记到 `runtime_data/multimodal_index/object_index.jsonl`
- 基于 `app/knowledge_graph.json` 和当前数据库内容生成轻量知识图谱三元组

### 4.3 运行时数据目录设计

为了保证“标准化存储”和“来源可追溯”，项目新增了 `runtime_data/` 目录：

- `runtime_data/db/`
  存放 SQLite 数据库文件，是系统的结构化业务数据存储区。
- `runtime_data/raw_uploads/`
  存放原始上传 JSON 文件，完整保留设备上传时的原始包内容。
- `runtime_data/standardized_data/`
  存放本地标准化后的单条传感器数据文件，是“本地标准化存储”的核心目录。
- `runtime_data/camera_frames/`
  存放摄像头后台定时抓拍图片和画面元数据，用于本地保存视频模态数据。
- `runtime_data/camera_video/`
  存放摄像头后台按整分钟自动分段写入的 MP4 录像文件、帧时间索引和录像会话元数据。
- `runtime_data/normalized_records/`
  存放规范化后的 JSONL 记录，体现统一传感器结构和标准化时间格式。
- `runtime_data/source_audit/`
  存放来源审计日志，记录设备编号、来源 IP、采集模式、传输方式等信息。
- `runtime_data/multimodal_index/`
  存放对象索引文件，记录视频、图片、原始上传 JSON 等非结构化文件的路径、模态和时间信息。
- `runtime_data/knowledge_graph/`
  存放知识图谱运行时快照，用于展示空间、设备、传感器和媒体文件之间的语义关系。

这种设计的好处是：

- 数据库负责业务查询和页面展示
- `standardized_data/` 负责本地标准化传感器数据落盘
- `camera_video/` 负责本地保存连续 MP4 录像
  默认按整分钟生成独立 MP4；历史回放只使用已经封口并写入索引的完整片段，正在录制的当前分钟片段不参与回放
- `camera_frames/` 负责本地保存摄像头画面数据
- 文件目录负责原始数据留痕和来源追踪
- `multimodal_index/` 负责索引非结构化文件对象，便于后续回放和导出
- `knowledge_graph/` 负责保存语义关系快照，便于展示多模型扩展思路
- 既保持三表结构简洁，又增强了平台的数据治理能力

### 4.4 多模型数据库扩展设计

当前版本仍然以关系型数据库作为核心实现，使用 `device`、`sensor`、`sensor_data` 三张表管理设备、传感器和观测数据。为了支撑后续历史回溯、数据集导出和多模态语义分析，系统增加了轻量的多模型扩展层：

- 关系模型：继续使用 SQLite 管理设备、传感器和结构化观测数据。
- 时序模型：通过 `sensor_data.timestamp` 和 `standardized_data` 中的标准化 JSONL 文件表达高频传感器时间序列。
- 对象存储模型：通过 `runtime_data/multimodal_index/object_index.jsonl` 索引视频、图片和原始 JSON 文件。
- 知识图谱模型：通过 `app/knowledge_graph.json` 和 `/knowledge-graph` 接口表达空间、设备、传感器、观测数据和媒体文件之间的语义关系。

相关接口：

- `/object-index`
  查看最近登记的对象索引记录。
- `/knowledge-graph`
  查看当前系统生成的知识图谱三元组。
- `/knowledge-graph?snapshot=true`
  生成并保存一份运行时知识图谱快照到 `runtime_data/knowledge_graph/`。
- `/dataset/query`
  按时间范围和设备类型查询可导出的传感器数据与对象索引摘要。
- `/dataset/export`
  按时间范围和设备类型导出对齐后的多模态数据包，ZIP 内包含 `sensor_data.csv`、`object_index.json` 和 `manifest.json`。
- `/timeline/state`
  按指定时间点查询附近的环境数据、蓝牙运动数据和摄像头抓拍索引，用于历史回溯时间轴。
- `/timeline/frame`
  返回指定时间点附近的摄像头抓拍图片。

### 4.6 IMU 活动识别设计

WT901 蓝牙节点建议固定在腰部位置，用于采集人体整体运动趋势。平台当前使用加速度 X/Y/Z 和角速度 X/Y/Z 六个通道构建滑动窗口，默认按 10Hz 回传节奏取最近 3 秒数据，即形成约 `30 x 6` 的 IMU 输入矩阵。

活动识别采用“两层方案”：

- 深度模型层：支持加载 `runtime_data/models/imu_activity_cnn_gru.pt`，模型结构为 1D-CNN 提取局部时序特征，再通过 GRU 或 LSTM 建模连续运动上下文。
- 规则兜底层：当本地未安装 PyTorch、模型文件不存在或样本不足时，自动使用加速度波动、加速度峰值、角速度 RMS 和角速度峰值进行可解释规则识别。

这样既能在答辩中体现较完整的智能分析路线，又不会因为模型文件缺失影响系统演示稳定性。

训练自定义模型时，可准备带标签的腰部 WT901 CSV 数据，字段包括：

```text
timestamp,label,acceleration_x,acceleration_y,acceleration_z,angular_velocity_x,angular_velocity_y,angular_velocity_z
```

然后执行：

```bash
python scripts/train_imu_cnn_gru.py --csv runtime_data/labels/waist_imu.csv
```

训练完成后会默认生成：

```text
runtime_data/models/imu_activity_cnn_gru.pt
```

如果模型保存在其他路径，可通过环境变量指定：

```bash
set SMARTSPACE_IMU_MODEL_PATH=runtime_data/models/imu_activity_cnn_gru.pt
```

### 4.5 数据库表结构

#### device

- `id`
- `name`
- `device_type`
- `ip_address`
- `port`
- `status`
- `description`
- `created_at`

#### sensor

- `id`
- `device_id`
- `name`
- `sensor_type`
- `unit`
- `created_at`

#### sensor_data

- `id`
- `sensor_id`
- `timestamp`
- `value`
- `created_at`

这种结构的好处是：

- 同一套表结构可以容纳更多传感器类型
- 新增气压、光照、CO2、加速度、角速度等指标时，无需修改 `sensor_data` 表结构
- 设备接入层和存储层解耦，便于后续扩展

## 5. 真实传感器替换方式

### 5.1 两层结构

树莓派脚本分为两层：

- 数据采集层：`raspberry_pi/collector.py`
- 数据上传层：`raspberry_pi/uploader.py`

### 5.2 默认模式

默认优先使用真实 `sen0501` 采集模式。

### 5.3 预留真实传感器接口

已预留以下函数接口：

- `read_from_sen0501()`
- `read_from_dht22()`
- `read_from_bme280()`

### 5.4 切换方式

1. 在 `collector.py` 中补充真实传感器读取逻辑
2. 设置环境变量切换采集模式
3. 对于树莓派真实节点，建议同时使用 MQTT 协议上传

以 SKU:SEN0501 为例：

```bash
set SENSOR_SOURCE=sen0501
set SMARTSPACE_TRANSPORT=mqtt
python raspberry_pi/uploader.py
```

```bash
set SENSOR_SOURCE=dht22
python raspberry_pi/uploader.py
```

或：

```bash
set SENSOR_SOURCE=bme280
python raspberry_pi/uploader.py
```

切换后仍然使用相同的统一 JSON 上传格式，因此：

- 上传层无需修改
- FastAPI 后端无需修改
- SQLite 数据库结构无需修改
- 前端仪表盘无需修改

### 5.5 WT901 蓝牙节点接入方式

WT901 蓝牙节点同样采用“采集层 + 上传层”结构：

- 数据采集层：`bluetooth_node/collector.py`
- 数据上传层：`bluetooth_node/uploader.py`

其中：

- `collector.py` 负责 BLE 连接、通知订阅和原始数据帧解析
- `uploader.py` 负责把加速度、角速度转换成统一的 `metrics` 数组并上传

WT901 上传后的标准化指标包括：

- `acceleration_x`
- `acceleration_y`
- `acceleration_z`
- `angular_velocity_x`
- `angular_velocity_y`
- `angular_velocity_z`

## 6. 主要接口说明

### 6.1 设备管理

- `POST /devices`
- `GET /devices`
- `PUT /devices/{device_id}`
- `DELETE /devices/{device_id}`

### 6.2 传感器管理

- `POST /sensors`
- `GET /sensors`

### 6.3 数据接口

- `POST /sensor-data/upload`
- `GET /dashboard/latest`
- `GET /dashboard/latest-motion`
- `GET /sensor-data/history`
- `GET /video_feed`

## 7. 模块验证方法

### 7.1 数据库与初始化脚本验证

执行：

```bash
python scripts/init_db.py
```

验证点：

- 目录下生成 `runtime_data/db/smartspace.db`
- `device`、`sensor`、`sensor_data` 三张表创建成功
- 已写入树莓派节点、蓝牙节点、摄像头及对应传感器档案
- 初始状态下不写入任何演示历史数据

### 7.2 后端接口验证

启动服务后打开：

- [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

重点测试：

- `GET /devices` 能看到摄像头、树莓派节点和蓝牙节点
- `GET /sensors` 能看到温湿度、加速度、角速度传感器
- `GET /dashboard/latest` 能返回当前最新环境指标
- `GET /dashboard/latest-motion` 能返回当前最新加速度和角速度
- `GET /analysis/environment` 能返回当前空间环境状态分析
- `GET /analysis/imu-activity` 能返回 WT901 活动识别结果，并标明当前使用深度模型还是规则兜底
- `GET /sensor-data/history` 能返回折线图所需历史数据

### 7.3 标准化上传验证

执行：

```bash
python raspberry_pi/uploader.py
```

验证点：

- 终端每 5 秒打印一次上传成功信息
- `POST /sensor-data/upload` 返回 `stored_count = 2`
- `normalized_timestamp` 为规范化 ISO 时间
- 前端页面中的当前环境指标和空间环境状态分析会自动刷新
- `runtime_data/raw_uploads/` 中出现原始上传 JSON 文件
- `runtime_data/standardized_data/` 中出现本地标准化传感器数据文件
- `runtime_data/camera_frames/` 中出现摄像头抓拍图片和元数据
- `runtime_data/normalized_records/sensor_data_records.jsonl` 中出现标准化记录
- `runtime_data/source_audit/source_audit.jsonl` 中出现来源审计记录

### 7.4 WT901 蓝牙节点上传验证

执行：

```bash
python bluetooth_node/uploader.py
```

验证点：

- 终端能看到 WT901 已连接提示
- 后端 `POST /sensor-data/upload` 返回 `stored_count = 6`
- 前端蓝牙运动信息区域会按约 10Hz 自动刷新
- 前端 IMU 活动识别区域会基于最近 3 秒窗口给出静止、轻微运动、持续活动、剧烈运动等状态
- `runtime_data/standardized_data/` 中出现加速度和角速度标准化记录

### 7.5 摄像头视频流验证

打开仪表盘页面，观察：

- 摄像头可用时显示实时画面
- 摄像头不可用时显示占位画面，不影响其他模块演示

### 7.6 前端仪表盘验证

访问：

- [http://127.0.0.1:8000](http://127.0.0.1:8000)

验证点：

- 页面能显示摄像头区域
- 当前温度、湿度、大气压、海拔、紫外线和环境光能展示
- 空间环境状态分析卡片能给出综合评分、关键判断和调整建议
- 历史折线图能加载
- 温湿度折线图下方能显示 WT901 的加速度与角速度信息
- 温湿度数据每 5 秒自动刷新一次，WT901 运动信息按约 10Hz 刷新

## 8. 演示建议

建议演示顺序：

1. 先运行 `init_db.py`，展示数据库和设备档案已准备完成
2. 启动 FastAPI 服务，打开 `docs` 页面说明接口
3. 打开首页仪表盘，展示摄像头画面与“暂无实时数据”空态
4. 启动树莓派程序，展示六项环境指标和空间环境状态分析每 5 秒更新
5. 启动 WT901 蓝牙脚本，展示加速度和角速度实时刷新
6. 说明采集层后续可在 sen0501、DHT22、BME280 等真实传感器之间切换

## 9. 说明

- 当前温湿度和 WT901 运动数据都不再使用模拟数据，前端空态表示对应设备尚未上报
- 后续可替换为真实 SKU:SEN0501、DHT22、BME280 等传感器读取逻辑
- 平台后端接口和数据库结构无需修改，只需要替换树莓派采集层或蓝牙采集层
- 本项目优先强调简单、稳定、易于本地运行和毕设展示
