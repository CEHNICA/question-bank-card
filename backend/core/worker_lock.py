"""数据目录级别的独占锁：同一份数据只允许一个进程写。"""

import os
from pathlib import Path


class SingleInstance:
    """同一个数据目录只允许一个工作者，避免两个进程抢同一份卷。

    抢不到锁就直接退出，不排队：后台工作者不该等一个维护命令，
    维护命令也不该等一个跑着的后台。
    """

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = path.open("a+")
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            raise SystemExit("已有一个题有据工作者在运行，本进程退出。") from None
