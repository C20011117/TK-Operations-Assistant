"""导出 OpenAPI 文档，供前端生成 TypeScript 类型。用法：python -m tk_workspace.api.export_openapi <输出路径>"""

import json
import sys
from pathlib import Path

from tk_workspace.api.main import create_app


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "openapi.json")
    spec = create_app().openapi()
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
