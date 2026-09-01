# Tao shortcut tren Desktop chay truc tiep bang pythonw.exe.
# Sau khi chay script, bo ghim icon cu va ghim shortcut nay vao taskbar.

$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$launcherPath = Join-Path $projectRoot "launch_tiktok_profile_manager.pyw"
$pythonwPath = Join-Path $projectRoot ".venv\Scripts\pythonw.exe"
$iconPath = Join-Path $projectRoot "assets\app_icon.ico"
$desktopPath = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktopPath "TikTok Profile Manager.lnk"
$appUserModelId = "ToolReup.TikTokProfileManager.Live"
if (-not (Test-Path -LiteralPath $launcherPath)) {
    throw "Khong tim thay launcher: $launcherPath"
}

if (-not (Test-Path -LiteralPath $pythonwPath)) {
    throw "Khong tim thay Python GUI launcher: $pythonwPath"
}

if (-not (Test-Path -LiteralPath $iconPath)) {
    throw "Khong tim thay icon: $iconPath"
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonwPath
$shortcut.Arguments = '"' + $launcherPath + '"'
$shortcut.WorkingDirectory = $projectRoot
$shortcut.IconLocation = $iconPath + ",0"
$shortcut.Description = "Mo TikTok Profile Manager"
$shortcut.Save()

$appIdInterop = @'
using System;
using System.Runtime.InteropServices;

[ComImport, Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IPropertyStore {
    uint GetCount(out uint cProps);
    uint GetAt(uint iProp, out PROPERTYKEY pkey);
    uint GetValue(ref PROPERTYKEY key, out PROPVARIANT pv);
    uint SetValue(ref PROPERTYKEY key, ref PROPVARIANT pv);
    uint Commit();
}

[StructLayout(LayoutKind.Sequential, Pack = 4)]
struct PROPERTYKEY {
    public Guid fmtid;
    public uint pid;
}

[StructLayout(LayoutKind.Explicit)]
struct PROPVARIANT {
    [FieldOffset(0)] public ushort vt;
    [FieldOffset(8)] public IntPtr pointerValue;
}

public static class ShortcutAppIdentity {
    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = true)]
    static extern int SHGetPropertyStoreFromParsingName(
        string path, IntPtr bindContext, uint flags, ref Guid riid, out IPropertyStore propertyStore);

    public static void SetAppUserModelId(string shortcutPath, string appUserModelId) {
        Guid iid = new Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99");
        IPropertyStore propertyStore;
        int result = SHGetPropertyStoreFromParsingName(shortcutPath, IntPtr.Zero, 2, ref iid, out propertyStore);
        if (result != 0) Marshal.ThrowExceptionForHR(result);

        PROPERTYKEY propertyKey = new PROPERTYKEY {
            fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"),
            pid = 5
        };
        PROPVARIANT propertyValue = new PROPVARIANT {
            vt = 31,
            pointerValue = Marshal.StringToCoTaskMemUni(appUserModelId)
        };
        try {
            int setResult = (int)propertyStore.SetValue(ref propertyKey, ref propertyValue);
            if (setResult != 0) Marshal.ThrowExceptionForHR(setResult);
            int commitResult = (int)propertyStore.Commit();
            if (commitResult != 0) Marshal.ThrowExceptionForHR(commitResult);
        }
        finally {
            Marshal.FreeCoTaskMem(propertyValue.pointerValue);
            Marshal.ReleaseComObject(propertyStore);
        }
    }
}
'@

if (-not ("ShortcutAppIdentity" -as [type])) {
    Add-Type -TypeDefinition $appIdInterop
}
[ShortcutAppIdentity]::SetAppUserModelId($shortcutPath, $appUserModelId)

Write-Host "Da tao shortcut: $shortcutPath"
