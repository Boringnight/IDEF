# LTRP · 演示版 v2(固定加宽地图)

> 本目录是演示专用副本(`ltrp-sim-----v2`),与通用版 `ltrp-sim` 的差异:
> ① **地图固定**:只有一张默认地图(MAP_SEED=7),任何"随机地图/重置"请求都回到这张图;
> ② **纵向加宽**:喉道半径 95~115(原 48~68)、腔室半径 215~265(原 135~185),管道明显更"胖";
> ③ **道钉密度翻倍**(每腔 8 颗 + 每喉道 4 颗),多次灾难后仍有充足自愈冗余;
> ④ 加宽地图上的自愈适配(桥接落点逐拍重算 / 锚点只认永久骨干方向 / 对端坐标窗口加长)。
> 启动入口:上一级目录的 `ltrp-sim/start.bat` 已指向本副本。

# LTRP · 月球熔岩管去中心化路由保持协议沙盘

对应算法设计文档:`../算法设计/算法设计_复杂熔岩管去中心化路由保持.md`
当前代码原理与优化记录:`算法原理.md`(**以该文件为准**)

复杂熔岩管结构(4~6 腔室 + 窄喉道 + 巨石)下的**真·去中心化**路由协议仿真:
后端逐节点运行 LTRP 协议栈(信标发现 / 2 跳视图 / 割点自识别 / 分布式 Bellman-Ford /
束存储携带转发),前端为纯 Canvas 2D 实时沙盘。
**引擎不做任何全局路由计算** —— 每个节点只凭本地邻居表决策。

## 快速开始

```bash
cd backend  && pip install -r requirements.txt && python main.py    # :5000
cd frontend && npm install && npm run dev                           # :5174
```

Windows 一键启动(含内网穿透):双击 `start.bat`,停止双击 `stop.bat`。

## 验证(改完代码必跑)

```bash
cd backend
PYTHONHASHSEED=0 py -3.13 tests/smoke_sim.py --fingerprint   # 1500 tick 行为指纹回归
PYTHONHASHSEED=0 py -3.13 tests/smoke_sim.py --scenarios     # 灾害/休眠/拖石/随机地图/上帝模式
py -3.13 tests/physics_check.py --physics                    # 逐 tick 校验:无穿模、移动体不超速
py -3.13 tests/physics_check.py --heal 120                   # 自愈强度:切断喉道后恢复所需 tick
py -3.13 main.py                                             # 起服务(另开窗口)
py -3.13 tests/e2e_check.py                                  # WS+REST 端到端(直连 :5000)
py -3.13 tests/e2e_check.py 5174                             # 同一套检查走 Vite 代理(:5174)
py -3.13 tests/robustness_bench.py --maps 15                 # 鲁棒性大数基准(约 40 分钟,报告见 ../鲁棒性基准报告.md)
cd ../frontend && npm run build && node tests/canvas_smoke.mjs
```

**移动速度口径**:探针(面包屑载体)合速度恒 ≤ `MOVE_V`=10cm/s;
自愈节点 `NODE_MOVE_V`=26cm/s、月球车 `ROVER_V`=55cm/s(对齐早期 v0 版本的自愈节奏)。

## 协议栈(与设计文档对应)

| 层 | 机制 | 代码 |
|---|---|---|
| L1 感知 | 信标(T_b=0.9s)/ EWMA SNR / 3 信标判死 / 双向链路准入 | `sim/protocol.py` |
| L1 韧性 | 2 跳视图割点自识别(删我后邻居是否散架)/ 流量自适应轮值休眠 | `sim/protocol.py` `engine_ext/sleep.py` |
| L2 路由 | DSDV 式分布式 Bellman-Ford,水平分割+代价上限+跳数上限防环 | `sim/protocol.py` |
| L2 度量 | DLER 能量状态机:NORMAL / PROTECTED(退出中继) / DYING(休眠保命);安全路径→最小代价,否则→最大-最小剩余能量 | `sim/nodes.py` `sim/protocol.py` |
| L3 容断 | 无路由→束存储;月球车按巡逻接触计划摆渡;地球可见窗口批量回传;关键束双路冗余 | `engine_ext/traffic.py` `engine_ext/ferry.py` |
| L1 部署 | 探针携带库存道钉沿途撒布(面包屑),未探明区域前端迷雾 | `engine_ext/deploy.py` `engine_ext/spawn.py` |
| 供电 | 地表核裂变→激光母-子多跳 bucket-brigade,按子树缺电需求加权 | `engine_ext/power.py` |

架构:后端按 Rule 2.1 分层为 **API 层(`main.py`)+ 强类型契约(`sim/contracts.py`)+
编排器(`sim/engine.py`)+ 15 个单一职责 mixin(`sim/engine_ext/`)**;
所有 DTO 跨模块传递,JSON 序列化只发生在 API 层。

## 演示脚本(建议顺序)

1. **开场**:探针从基站向右推进,面包屑道钉陆续上线,DSDV 逐跳收敛,覆盖率爬升至 ~98%。
2. **拖巨石**:改变视距,被挡链路消失,邻居表超时收缩,路由绕行 —— 全程无中心重算。
3. **⚔ 摧毁一条喉道**:两枚边界道钉阵亡 → 分区 → 失联侧数据束堆积 → 月球车按巡逻计划经过,金色虚线摆渡接驳 → 回到骨干侧多跳回传。
4. **🪨 塌方**:腔室内 2 枚道钉被掩埋 + 巨石堆积,腔室冗余吸收,覆盖率短暂下探后自愈。
5. **🌍 地月潮汐锁定**:月球始终同一面朝向地球,链路恒可见,数据实时回传(无升降/遮挡窗口)。
6. **🔥 热浪**:能耗 ×5、SNR 恶化 6dB,观察 PROTECTED 状态机与链路脱落(持续 22 真实秒,与时间缩放无关)。
7. **💤 休眠调度**:腔室冗余道钉轮值休眠(喉道边界节点与割点永不休眠)。
8. **⏱ 时间缩放**:1x~1000x 滑块,统一缩放移动/能耗/充电节奏。

## 关键交互

- 悬停节点:科技感 Tooltip(电量 SoC 进度条 / 位置坐标 / 运行状态 / 到基站路由 / 载荷 / 关键割点 / 邻居节点状态(角色·SNR·电量·状态) ,靠近边缘自动避让)
- 左键节点:Inspector(邻居表 EWMA SNR / 路由 / 状态机 + 上帝模式物理参数滑块)
- 拖拽巨石:松手触发全网视距重算;巨石压到节点会使其**直接宕机(DEAD)**,而非被 SOS 顶出巨石/休眠
- 滚轮缩放 / 空白拖拽平移
- HUD 按钮:信号覆盖范围 / 整体电量 / 协议参数 / 塌方 / 温度浪潮 / 摧毁喉道 / 休眠调度 / 随机地图 / 重置 / 时间缩放

## 目录

```
backend/   main.py(API 层) + sim/(协议与引擎) + tests/(冒烟/端到端)
frontend/  src/canvas/(绘制) + src/components/(React 组件) + tests/(画布冒烟)
```
