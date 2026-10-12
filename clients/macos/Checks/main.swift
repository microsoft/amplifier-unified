import AppKit
final class ProbeEditor: NSTextView {
 var pasted = 0
 override func paste(_ sender: Any?) { pasted += 1 }
}
final class FieldDelegate: NSObject, NSWindowDelegate {
 let editor = ProbeEditor()
 func windowWillReturnFieldEditor(_ sender: NSWindow, to client: Any?) -> Any? { editor }
}
MainActor.assumeIsolated {
 let app = NSApplication.shared
 app.setActivationPolicy(.accessory)
 let window = EditingWindow(contentRect: NSRect(x: -10000, y: -10000, width: 200, height: 100), styleMask: [.titled], backing: .buffered, defer: false)
 let delegate = FieldDelegate()
 window.delegate = delegate
 let field = NSSecureTextField(frame: NSRect(x: 10,y: 10,width: 180,height: 25))
 window.contentView!.addSubview(field)
 window.makeKeyAndOrderFront(nil)
 delegate.editor.isFieldEditor = true
 window.makeFirstResponder(field)
 precondition(window.firstResponder?.responds(to: #selector(NSText.paste(_:))) == true, "Secure field editor must support paste")
 // Use a probe responder for dispatch; do not read or change the shared clipboard.
 window.contentView!.addSubview(delegate.editor)
 window.makeFirstResponder(delegate.editor)
 let event = NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: .command, timestamp: 0, windowNumber: window.windowNumber, context: nil, characters: "v", charactersIgnoringModifiers: "v", isARepeat: false, keyCode: 9)!
 precondition(window.performKeyEquivalent(with: event), "Command-V was not handled")
 precondition(delegate.editor.pasted == 1, "Paste was not routed to the focused editor")
 print("PASS: secure editor supports paste; Command-V routes to focused editor")
 window.orderOut(nil)
}
