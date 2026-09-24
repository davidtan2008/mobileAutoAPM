#ifndef IOSAPM_EARLY_MARK_H
#define IOSAPM_EARLY_MARK_H

/// 进程创建时间（epoch 秒，含小数）。与 sysctl KERN_PROC_PID 同口径。
/// 取不到时返回 0。
double iosapm_process_start_time(void);

/// pre-main 耗时（毫秒）：进程创建 → 本 C 构造函数。
/// **取不到时返回 -1，不是 0** —— 0 会被误读成"pre-main 极快"，是危险的假数据。
int iosapm_premain_millis(void);

#endif /* IOSAPM_EARLY_MARK_H */
