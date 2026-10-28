import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
BarWidget {
 id: root
 moduleName: "@PLUGIN_ID@"
 property bool up: false
 property int working: 0
 property int total: 0
 property string tip: ""
 property string localLine: ""
 // @REPO@ is replaced by install.sh with this checkout's path
 property string viewer: "@REPO@/bin/viewer"
 property string stat: "@REPO@/bin/barstat"
 implicitWidth: button.implicitWidth
 implicitHeight: button.implicitHeight
 Process {
  id: poll
  command: ["python3", root.stat]
  stdout: StdioCollector { onStreamFinished: {
   try {
    var s=JSON.parse(text); root.up=s.up===true; root.working=s.working||0; root.total=s.total||0; root.tip=s.tip||""; root.localLine=s.local_today ? "Local today: " + s.local_today.jobs + " jobs, " + s.local_today.chars + " chars" : ""
   } catch(e) {root.up=false; root.working=0; root.total=0; root.tip="Studio Routing status unavailable"}
  }}
 }
 Timer { interval: 2000; running: true; repeat: true; triggeredOnStart: true; onTriggered: {if(!poll.running) poll.running=true} }
 BarIconButton {
  id: button
  anchors.fill: parent
  bar: root.bar
  text: root.up ? (root.working > 0 ? "󰙅 " + root.working + "/" + root.total : "󰙅") : "󰙅"
  foreground: !root.up ? "#ff7777" : root.working > 0 ? "#e6ac87" : root.bar.barForeground
  tooltipText: "Studio Routing"
   + (root.up ? "\n" + root.working + " of " + root.total + " working" : "\nserver offline")
   + (root.tip ? "\n\n" + root.tip : "")
   + (root.localLine ? "\n" + root.localLine : "")
   + "\n\nClick: open the app"
  onPressed: function(mouseButton) {
   Quickshell.execDetached(["bash","-lc", root.viewer])
  }
 }
}
