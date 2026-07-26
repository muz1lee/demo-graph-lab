"""演示证据包适配器：只读加载脱敏后的 demo bundle。"""

from .loader import DemoBundle, DemoBundleError, load_demo_bundle

__all__ = ["DemoBundle", "DemoBundleError", "load_demo_bundle"]
