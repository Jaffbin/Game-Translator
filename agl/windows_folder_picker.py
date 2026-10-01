"""Windows Explorer-style folder picker using the native Common Item Dialog.

Calling the Windows API directly keeps non-ASCII game paths intact; piping a
folder name through a PowerShell subprocess depends on console code pages.
"""

from __future__ import annotations

import ctypes
import sys
import uuid
from ctypes import wintypes


class _GUID(ctypes.Structure):
    _fields_ = [
        ("data1", wintypes.DWORD),
        ("data2", wintypes.WORD),
        ("data3", wintypes.WORD),
        ("data4", ctypes.c_ubyte * 8),
    ]


def _guid(value: str) -> _GUID:
    return _GUID.from_buffer_copy(uuid.UUID(value).bytes_le)


def _method(interface: ctypes.c_void_p, index: int, *argtypes):
    table = ctypes.cast(interface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(wintypes.HRESULT, ctypes.c_void_p, *argtypes)(table[index])


def choose_game_folder() -> str:
    """Return an absolute folder path, or an empty string when cancelled."""
    if sys.platform != "win32":
        return ""

    ole32 = ctypes.WinDLL("ole32")
    user32 = ctypes.WinDLL("user32")
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = wintypes.HRESULT
    ole32.CoCreateInstance.argtypes = [
        ctypes.POINTER(_GUID), ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p),
    ]
    ole32.CoCreateInstance.restype = wintypes.HRESULT
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    user32.GetForegroundWindow.restype = wintypes.HWND

    initialized = ole32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
    if initialized < 0:
        raise OSError(f"Windows folder picker COM initialization failed: 0x{initialized & 0xffffffff:08x}")

    dialog = ctypes.c_void_p()
    item = ctypes.c_void_p()
    display_name = ctypes.c_void_p()
    try:
        clsid = _guid("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7")
        iid = _guid("D57C7288-D4AD-4768-BE02-9D969532D960")
        hr = ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(dialog))
        if hr < 0:
            raise OSError(f"Windows folder picker could not open: 0x{hr & 0xffffffff:08x}")

        options = wintypes.DWORD()
        hr = _method(dialog, 10, ctypes.POINTER(wintypes.DWORD))(dialog, ctypes.byref(options))
        if hr < 0:
            raise OSError(f"Windows folder picker options failed: 0x{hr & 0xffffffff:08x}")
        # FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST
        hr = _method(dialog, 9, wintypes.DWORD)(dialog, options.value | 0x20 | 0x40 | 0x800)
        if hr < 0:
            raise OSError(f"Windows folder picker setup failed: 0x{hr & 0xffffffff:08x}")
        _method(dialog, 17, ctypes.c_wchar_p)(dialog, "选择游戏文件夹")

        hr = _method(dialog, 3, wintypes.HWND)(dialog, user32.GetForegroundWindow())
        if (hr & 0xffffffff) == 0x800704C7:  # ERROR_CANCELLED
            return ""
        if hr < 0:
            raise OSError(f"Windows folder picker failed: 0x{hr & 0xffffffff:08x}")

        hr = _method(dialog, 20, ctypes.POINTER(ctypes.c_void_p))(dialog, ctypes.byref(item))
        if hr < 0:
            raise OSError(f"Windows folder picker result failed: 0x{hr & 0xffffffff:08x}")
        # IShellItem::GetDisplayName(SIGDN_FILESYSPATH)
        hr = _method(item, 5, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p))(
            item, 0x80058000, ctypes.byref(display_name)
        )
        if hr < 0:
            raise OSError(f"Windows folder path failed: 0x{hr & 0xffffffff:08x}")
        return ctypes.wstring_at(display_name.value)
    finally:
        if display_name.value:
            ole32.CoTaskMemFree(display_name)
        if item.value:
            _method(item, 2)(item)
        if dialog.value:
            _method(dialog, 2)(dialog)
        ole32.CoUninitialize()
