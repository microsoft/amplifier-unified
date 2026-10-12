// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "AmplifierCompanion", platforms: [.macOS(.v13)],
    products: [.executable(name: "AmplifierTempo", targets: ["CompanionApp"])],
    targets: [
        .target(name: "CompanionCore"),
        .executableTarget(name: "CompanionApp", dependencies: ["CompanionCore"],
                          resources: [.copy("Resources")]),
        .testTarget(name: "CompanionCoreTests", dependencies: ["CompanionCore"])
    ])
