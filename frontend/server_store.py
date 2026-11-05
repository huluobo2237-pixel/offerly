"""Private server-side configuration, separate from Windows DPAPI files."""

import json
import os
import tempfile
from pathlib import Path


class ServerStore:
    def __init__(self, path):
        self.path = Path(path)

    def read(self):
        return (
            json.loads(self.path.read_text(encoding="utf-8"))
            if self.path.is_file()
            else None
        )

    def write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False) as output:
            temporary = Path(output.name)
            try:
                output.write(json.dumps(data, ensure_ascii=False).encode())
                output.close()
                os.chmod(temporary, 0o600)
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)

    def clear(self):
        self.path.unlink(missing_ok=True)
