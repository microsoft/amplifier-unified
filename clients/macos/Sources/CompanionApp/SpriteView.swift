#if os(macOS)
import AppKit
import CompanionCore

@MainActor final class SpriteView: NSView {
    var motion = Motion.waiting
    var caption = "Offline"
    var open: (() -> Void)?
    private var frames: [[NSImage]] = []
    private var timer: Timer?
    private var tick = 0
    private var shape = ""
    override var isOpaque: Bool { false }
    override var mouseDownCanMoveWindow: Bool { true }

    func load(_ name: String) {
        let name = ["arc", "relay", "crest"].contains(name) ? name : "arc"
        guard shape != name else { return }
        guard let url = Bundle.module.url(forResource: name, withExtension: "png", subdirectory: "Resources"),
              let source = NSImage(contentsOf: url)?.cgImage(forProposedRect: nil, context: nil, hints: nil),
              source.width == 1536, source.height == 2288 else { return }
        frames = (0..<11).map { row in
            (0..<8).compactMap { column in
                source.cropping(to: CGRect(x: column * 192, y: row * 208, width: 192, height: 208))
                    .map { NSImage(cgImage: $0, size: NSSize(width: 192, height: 208)) }
            }
        }
        shape = name; needsDisplay = true
    }
    func animate(_ enabled: Bool) {
        timer?.invalidate(); timer = nil
        guard enabled else { return }
        timer = Timer.scheduledTimer(withTimeInterval: 0.14, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.tick += 1; self?.needsDisplay = true }
        }
        needsDisplay = true
    }
    override func draw(_ dirtyRect: NSRect) {
        guard frames.count == 11 else { return }
        let reduced = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
        var row = motion.rawValue, column = reduced ? 0 : tick % motion.count
        if !reduced, motion == .idle, let window {
            let cursor = NSEvent.mouseLocation
            let dx = cursor.x - window.frame.midX, dy = cursor.y - window.frame.midY
            let distance = hypot(dx, dy)
            if distance > 100 && distance < 500 {
                let angle = atan2(dx, dy) + 2 * Double.pi
                let direction = Int((angle / (2 * Double.pi) * 16).rounded()) % 16
                row = 9 + direction / 8; column = direction % 8
            }
        }
        frames[row][column].draw(in: NSRect(x: 0, y: 22, width: bounds.width, height: bounds.height - 22))
        let attrs: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: 11),
            .foregroundColor: NSColor.labelColor, .backgroundColor: NSColor.windowBackgroundColor]
        let text = NSAttributedString(string: "  " + caption + "  ", attributes: attrs)
        text.draw(at: NSPoint(x: (bounds.width - text.size().width) / 2, y: 2))
    }
    override func mouseDown(with event: NSEvent) {
        if event.clickCount == 2 { open?() }
        else { window?.performDrag(with: event) }
    }
}
#endif
