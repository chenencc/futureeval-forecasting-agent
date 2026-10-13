Option Explicit
' Run a console command without opening a visible window.
' Wait for completion so Task Scheduler receives the original exit code.
Dim shell, command, index, argument
If WScript.Arguments.Count = 0 Then WScript.Quit 2
command = ""
For index = 0 To WScript.Arguments.Count - 1
    argument = WScript.Arguments(index)
    If InStr(argument, Chr(34)) > 0 Then WScript.Quit 2
    If index > 0 Then command = command & " "
    command = command & Chr(34) & argument & Chr(34)
Next
Set shell = CreateObject("WScript.Shell")
WScript.Quit shell.Run(command, 0, True)
