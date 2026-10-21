# Build with: python -m PyInstaller --noconfirm --clean SemanticSearch-v2.spec
from pathlib import Path
from PyInstaller.archive.writers import CArchiveWriter
# Quantized projector weights are pathologically slow at zlib level 9.
# Level 1 uses the same standard archive format and preserves every file byte.
# PyInstaller is pinned in requirements-build.txt for this build-time setting.
CArchiveWriter._COMPRESSION_LEVEL=1
root=Path(SPECPATH)
datas=[(str(root/name),name) for name in ('models','runtimes','style-models','research','licenses')]
datas += [(str(root/'assets-manifest.json'),'.'),(str(root/'README.txt'),'.'),
          (str(root/'egl'/'focus.html'),'egl')]
a=Analysis([str(root/'launcher.py')],pathex=[str(root)],binaries=[],datas=datas,
           hiddenimports=['numpy','PIL.Image','PIL.JpegImagePlugin','PIL.PngImagePlugin','PIL.WebPImagePlugin'],
           hookspath=[],runtime_hooks=[],excludes=['torch','scipy','pandas','matplotlib','pytest','IPython'])
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,a.binaries,a.datas,[],name='Refseek-Browser',debug=False,
        version=str(root/'windows-version.txt'),
        bootloader_ignore_signals=False,strip=False,upx=False,console=False,disable_windowed_traceback=False)
