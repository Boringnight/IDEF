# IDEF — 月球熔岩管去中心化路由保持协议（LTRP）

本项目与月球熔岩管自组织通信网络中**去中心化路由保持协议（LTRP, Lunar Lava-Tube Routing Protocol）** 相关，包含：

- `ltrp-sim/` — 主模拟项目（后端 + 前端），含仿真、启停脚本与算法原理说明
- `lunar-lava-tube-routing-sim-main/` — 相关一份模拟实现（后端 + 前端）
- `算法设计/` — 复杂熔岩管去中心化路由保持算法设计文档
- `参考文献/` — 相关领域文献的提取文本与参考文档
- `_extract_param.py` / `_param_proof.txt` — 参数抽取脚本与参数证明

## 目录结构

```
.
├── ltrp-sim/                  # 主模拟项目 (backend + frontend)
├── lunar-lava-tube-routing-sim-main/
├── 算法设计/                   # 算法设计文档
├── 参考文献/                   # 参考文献与提取文本
├── _extract_param.py
└── _param_proof.txt
```

## 说明

- 仓库中已通过 `.gitignore` 排除虚拟环境 (`.venv`)、`node_modules`、日志、可执行工具 (`cloudflared.exe`) 及 macOS 系统文件 (`__MACOSX`)。
- 具体项目运行说明见 `ltrp-sim/README.md`。
