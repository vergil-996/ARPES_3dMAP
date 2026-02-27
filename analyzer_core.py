import numpy as np
from PyQt5.QtCore import QObject, pyqtSignal

class AnalyzerCore(QObject):
    progress_changed = pyqtSignal(int)
    data_loaded = pyqtSignal(tuple)

    def __init__(self):
        super().__init__()
        self.raw_data = None
        self.coords = {'X': None, 'Y': None, 'E': None, 'delay': None}
        self.is_2d_mode = False

    def load_npz(self, path):
        try:
            npz = np.load(path)
            self.coords['X'] = npz["X"].flatten()
            self.coords['Y'] = npz["Y"].flatten()
            self.coords['E'] = npz["E"].flatten()
            self.coords['delay'] = npz["delay"].flatten() if "delay" in npz else np.arange(npz["binned"].shape[3])
            self.raw_data = npz["binned"]
            return True, self.raw_data.shape
        except Exception as e:
            return False, str(e)

    def get_integrated_dynamics(self, r):
        """ 计算给定索引范围内的强度积分 """
        if self.raw_data is None: return None
        # 提取 ROI 并对 X(0), Y(1), E(2) 三个空间轴求和，保留时间轴
        roi = self.raw_data[int(r[0]):int(r[1])+1,
                            int(r[2]):int(r[3])+1,
                            int(r[4]):int(r[5])+1, :]
        return np.sum(roi, axis=(0, 1, 2))

    def get_data_for_t(self, t_idx):
        return self.raw_data[:, :, :, int(t_idx)]