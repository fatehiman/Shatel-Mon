' Launches Shatel Mon silently (no console window) using pythonw.
' Double-click to start, or drop a shortcut to this file in your Startup folder
' (Win+R -> shell:startup) to run it automatically at login.
Dim shell, fso, here
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = here
shell.Run "pythonw.exe """ & here & "\ShatelMon.py""", 0, False
