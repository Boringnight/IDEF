# -*- coding: utf-8 -*-
"""引擎共享导入(避免职责模块与主引擎的循环依赖)。

集中导出协议对象与通用工具,各 engine 子模块统一 `from ..engine_imports import ...`,
从而无需反向 import 主 engine.py。所有导入对象与用途在此集中注释(导入基线)。
"""
import random                                  # 引擎级随机数工具(节点出生/抖动/采样)

from .. import protocol as P                    # 协议核心:信标/割点/DSDV/可调参数 PARAMS


def zh(nid: str) -> str:
    """节点 id → 中文称谓(基站/月球车/探测器/道钉)。"""
    if nid.startswith("BASE"):
        return "主基站"
    if nid.startswith("ROVER"):
        return f"月球车{nid.split('-')[1]}"
    if nid.startswith("PROBE"):
        return f"深处探测器{nid.split('-')[1]}"
    return f"{int(nid.split('-')[1]):02d}号道钉"
