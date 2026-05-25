# VHS tape for VOC AI demo GIF
# Prerequisites: VOC_API_KEY must be set in environment
# Usage: cd voc-amazon-reviews && VOC_API_KEY=your-key vhs demo/demo.tape

Output demo/voc-demo.gif
Output demo/voc-demo.mp4

Set Shell "bash"
Set FontSize 14
Set Width 1100
Set Height 800
Set Padding 20
Set Theme "Catppuccin Mocha"
Set TypingSpeed 50ms
Set CursorBlink false

# Type the real-looking command, but execute demo.sh
Type "bash demo/demo.sh"
Sleep 500ms
Enter

# API fetch ~10s + report render
Sleep 24s
