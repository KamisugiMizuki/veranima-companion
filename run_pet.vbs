Option Explicit
Dim shell, fso, root, pythonw, script
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = root & "\.venv\Scripts\pythonw.exe"
script = root & "\scripts\run_pet.py"
If Not fso.FileExists(pythonw) Then
  MsgBox ".venv is missing. Install the project environment first.", 16, "Veranima"
  WScript.Quit 1
End If
If Not fso.FileExists(root & "\pet\node_modules\electron\cli.js") Then
  MsgBox "Electron dependency is missing. Create the pet\node_modules junction first.", 16, "Veranima"
  WScript.Quit 1
End If
If Not fso.FileExists(root & "\pet\node_modules\ws\package.json") Then
  MsgBox "WebSocket dependency is missing. Create the pet\node_modules junction first.", 16, "Veranima"
  WScript.Quit 1
End If
shell.Run """" & pythonw & """ """ & script & """", 0, False
