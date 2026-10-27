-- Open the studio's Herdr session in its own, studio-sized Terminal window.
-- Usage: osascript open-terminal.applescript "<attach command>"
-- A default 80x24 window (or a tab in a minimized one) is far too small for a six-pane team.
-- Find the window through the tty of the tab `do script` returns, never "front window":
-- with Terminal in the background that is somebody else's window. Move it to the top-left
-- first, because Terminal clamps a window's size to what fits from where it spawned.
on run argv
	set attachCmd to item 1 of argv
	tell application "Terminal"
		set t to do script "exec " & attachCmd
		set theTTY to tty of t
		repeat with w in windows
			try
				if tty of selected tab of w is theTTY then
					set bounds of w to {0, 0, 1700, 1100}
					set number of columns of w to 220
					set number of rows of w to 58
					set custom title of w to "Dev Studio"
				end if
			end try
		end repeat
		activate
	end tell
end run
