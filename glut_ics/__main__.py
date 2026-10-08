"""支持 ``python -m glut_ics`` 运行。"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
