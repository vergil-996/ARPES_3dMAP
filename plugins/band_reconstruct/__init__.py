# -*- coding: utf-8 -*-
"""能带重构插件（MRF Band Reconstruction）。

按 Xian & Stimper et al., *Nature Computational Science* 3, 101–114 (2023)
的 MRF 方法，从三维强度体 I(kx, ky, E) 中逐带重构二维色散面 E_b(kx, ky)。

本包的算法模块是**纯 NumPy/SciPy**，零 Qt、零宿主依赖：

- :mod:`preprocess`：高斯平滑 + MCLAHE 对比度增强；
- :mod:`mrf_loss`：三线性取样与 MRF 能量/梯度（向量化）；
- :mod:`optimize`：L-BFGS-B 逐带重构与收敛记录；
- :mod:`init_surface`：解析初始化面、外部网格导入与对齐参数；
- :mod:`synthetic`：合成数据生成；
- :mod:`metrics`：论文 eq. 9/10 的验收指标。

算法模块（``preprocess`` / ``mrf_loss`` / ``optimize`` / ``init_surface`` /
``synthetic`` / ``metrics``）不读取任何宿主对象；宿主集成在 ``worker``（工作函数）、
``panel``（参数面板）与 ``entry``（插件入口）里，只经由
``bandscope.extensions.api`` 的公开接口与宿主交互。
"""
