-- Open the studio's Herdr session in its own, studio-sized Terminal window.
-- Usage: osascript open-terminal.applescript "<attach command>"
-- A default 80x24 window (or a tab in a minimized one) is far too small for a six-pane team.
-- Size the TAB that `do script` returns, never "front window": with Terminal in the
-- background that is somebody else's window.
on run argv
	set attachCmd to item 1 of argv
	tell application "Terminal"
		set t to do script "exec " & attachCmd
		set number of columns of t to 220
		set number of rows of t to 58
		set custom title of t to "Dev Studio"
		activate
	end tell
end run
