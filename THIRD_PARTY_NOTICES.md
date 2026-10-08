# Third-party components

The application loads Qt and other native libraries dynamically. Keep the entire distribution folder intact. The build scripts and dependency list are provided so users can rebuild the application and substitute compatible library versions. Licenses distributed by installed packages are copied into the Windows distribution's `licenses` folder during release preparation.

| Component | Upstream | License (consult shipped upstream terms) |
| --- | --- | --- |
| Python | https://www.python.org/ | PSF license |
| PySide6 / Shiboken / Qt | https://www.qt.io/qt-for-python | LGPLv3 / GPLv3 / commercial; the Qt Core, Gui and Widgets modules used here support LGPL distribution |
| pynput | https://github.com/moses-palmer/pynput | LGPLv3 |
| Pillow | https://python-pillow.org/ | HPND |
| OpenCV | https://opencv.org/ | Apache-2.0; wheel includes additional third-party notices |
| NumPy | https://numpy.org/ | BSD-3-Clause and bundled library notices |
| psutil | https://github.com/giampaolo/psutil | BSD-3-Clause |
| pywinauto | https://github.com/pywinauto/pywinauto | BSD-3-Clause |
| comtypes | https://github.com/enthought/comtypes | MIT |
| pywin32 | https://github.com/mhammond/pywin32 | PSF-style license |
| PyObjC (Mac only) | https://pyobjc.readthedocs.io/ | MIT |
| PyInstaller (build tool) | https://pyinstaller.org/ | GPL with bootloader exception |

Before a public release, the repository owner should choose the project's own license and review the exact upstream notices for the built platform. This document does not grant a license to the project's source code.
