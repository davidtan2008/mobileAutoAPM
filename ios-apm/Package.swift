// swift-tools-version: 5.9
import PackageDescription

// 注意：SwiftPM **不支持混合语言 target**，因此 C 与 Swift 必须分开：
//   IOSAPMEarlyMark  —— 纯 C，提供 pre-main 打点与进程创建时间
//   IOSAPM           —— Swift，依赖上面的 C target
let package = Package(
    name: "IOSAPM",
    platforms: [.iOS(.v15), .macOS(.v12)],
    products: [
        .library(name: "IOSAPM", targets: ["IOSAPM"])
    ],
    targets: [
        // 纯 C：构造函数据在 dyld 阶段运行，早于任何 Swift 代码
        .target(
            name: "IOSAPMEarlyMark",
            path: "Sources/IOSAPMEarlyMark",
            publicHeadersPath: "include"
        ),
        // Swift：启动分段 + 内存水位
        .target(
            name: "IOSAPM",
            dependencies: ["IOSAPMEarlyMark"],
            path: "Sources/IOSAPM"
        ),
        .testTarget(
            name: "IOSAPMTests",
            dependencies: ["IOSAPM"],
            path: "Tests/IOSAPMTests"
        )
    ]
)
