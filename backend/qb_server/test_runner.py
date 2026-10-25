"""测试必须有自己的数据目录。

``settings.DATA_ROOT`` 默认指向 ``backend/data``，也就是老师真正的卷子文件夹。
测试上传、渲染、切题全都往那儿写，写完又不删——实测跑完一轮全量测试，
那个目录里多出 58 个没有对应资料的 UUID 目录、300.7 MB，比 14 份真实资料
（332 MB）还多。除了占地方，还有两个更坏的后果：测试里跑起来的启动清理会
对着老师的真实目录执行，而某次测试留下的那份 404.html 假卷，会以「1 页」
的样子出现在资料列表里。

所以测试一律把 ``DATA_ROOT`` 换成临时目录，跑完删掉。测试自己
``override_settings(DATA_ROOT=...)`` 的用例不受影响。
"""

import shutil
import tempfile
from pathlib import Path

from django.conf import settings
from django.test.runner import DiscoverRunner


class IsolatedDataRootRunner(DiscoverRunner):
    """``manage.py test`` 默认用这个：每个测试会话一个独立的临时数据目录。"""

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        self._qb_temp_data_root = tempfile.mkdtemp(prefix="qb-test-data-")
        settings.DATA_ROOT = Path(self._qb_temp_data_root).resolve()

    def teardown_test_environment(self, **kwargs):
        super().teardown_test_environment(**kwargs)
        root = getattr(self, "_qb_temp_data_root", None)
        if root:
            shutil.rmtree(root, ignore_errors=True)
            self._qb_temp_data_root = None
