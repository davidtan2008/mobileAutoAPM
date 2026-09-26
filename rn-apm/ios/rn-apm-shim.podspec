Pod::Spec.new do |s|
  s.name         = 'rn-apm-shim'
  s.version      = '0.1.0'
  s.summary      = 'rn-apm 的 iOS 原生补齐实现（启动时间 / 内存 / 崩溃关联）'
  s.description  = <<-DESC
    为 rn-apm JS SDK 提供三项原生能力：
      getProcessStartTime —— 用 sysctl(KERN_PROC_PID) 取进程创建时间
      getMemoryUsage      —— 用 task_vm_info.phys_footprint（不是 resident_size）
      reportJsError       —— 转发给宿主 App 的原生崩溃 SDK

    任何一项取不到都返回 nil / 空，**绝不返回 0**（0 会被误读成极快 / 极小）。
  DESC
  s.homepage     = 'https://github.com/davidtan2008/mobileAutoAPM'
  s.license      = { :type => 'MIT' }
  s.author       = { 'mobileAutoAPM' => 'noreply@example.com' }
  s.platform     = :ios, '12.0'
  s.source       = { :path => '.' }
  s.source_files = 'RnApm.m'
  s.frameworks   = 'UIKit'

  s.dependency 'React-Core'
end
