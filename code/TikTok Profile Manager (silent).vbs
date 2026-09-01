Option Explicit

' Chay TikTok Profile Manager ma khong hien cua so CMD/PowerShell.
' Neu can xem loi khoi dong, hay chay start_tiktok_profile_manager.bat thay vi file nay.
Dim fileSystem, shell, projectRoot, startScript, pythonwPath, command

Set fileSystem = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

projectRoot = fileSystem.GetParentFolderName(WScript.ScriptFullName)
startScript = fileSystem.BuildPath(projectRoot, "launch_tiktok_profile_manager.pyw")
pythonwPath = fileSystem.BuildPath(projectRoot, ".venv\Scripts\pythonw.exe")

If Not fileSystem.FileExists(startScript) Then
    MsgBox "Khong tim thay file khoi dong:" & vbCrLf & startScript, vbCritical, "TikTok Profile Manager"
    WScript.Quit 1
End If

If Not fileSystem.FileExists(pythonwPath) Then
    MsgBox "Khong tim thay Python moi truong ao:" & vbCrLf & pythonwPath, vbCritical, "TikTok Profile Manager"
    WScript.Quit 1
End If

command = """" & pythonwPath & """ """ & startScript & """"
shell.Run command, 0, False
