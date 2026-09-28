// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "quiet_otter",
    targets: [
        .target(name: "quiet_otter"),
        .testTarget(
            name: "quiet_otterTests",
            dependencies: ["quiet_otter"]
            , path: "SwiftTests"  // tests/ is Python's; macOS disks ignore case
        )
    ]
)
