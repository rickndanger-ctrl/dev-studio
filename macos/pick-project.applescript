-- Project picker shown when Dev Studio is launched from the Dock.
-- Usage: osascript pick-project.applescript "<projects, one per line>" "<default>"
-- Kept in its own file: the bash 3.2 that ships with macOS misreads AppleScript
-- text (apostrophes) placed inside a $( ... ) in the shell script.
on run argv
	set AppleScript's text item delimiters to linefeed
	set items_ to text items of (item 1 of argv)
	set def to item 2 of argv
	if def is "" or items_ does not contain def then set def to item 1 of items_
	set r to choose from list items_ with title "Dev Studio" with prompt "🎬 Which project is the team working on today?" default items {def} OK button name "Let's go"
	if r is false then return ""
	return item 1 of r
end run
